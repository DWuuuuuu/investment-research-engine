"""Order-flow / whale-concentration layer.

Fetches recent trades from the Polymarket data API for the top finance-relevant
markets and computes:

  • HHI (Herfindahl-Hirschman Index) concentration per market
      0 = perfectly distributed, 1 = one wallet holds everything
  • Top wallets by total notional traded across markets
  • Recent large trades (biggest individual fills)

No auth required — the Polymarket trades endpoint is public.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .clients import fetch_json, FetchError

_TRADES_URL = "https://data-api.polymarket.com/trades"
_TRADES_PER_MARKET = 50          # trades to fetch per market
_TOP_MARKETS = 8                 # how many markets to analyse
_LARGE_TRADE_THRESHOLD = 500     # $500+ = "large trade"
_RELEVANT_CATEGORIES = frozenset(
    ["macro", "crypto", "markets", "ai_tech"]
)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Trade:
    wallet: str
    name: str                 # pseudonym / display name (may be empty)
    side: str                 # "BUY" | "SELL"
    outcome: str              # "Yes" | "No"
    size: float                # outcome-token SHARES, not USDC (Polymarket's data-api
                                # /trades response has no usdcSize field — confirmed live;
                                # this used to be treated as dollar notional throughout
                                # this file, which made the $500 "large trade" threshold
                                # and every wallet-flow $ figure meaningless, since the
                                # same 500 "size" is $25 notional at price 0.05 and $475
                                # at price 0.95. Fixed 2026-08-10 — use `usd` below instead.
    price: float                # 0-1, per-share
    usd: float                  # size * price — the actual dollar notional
    timestamp: str
    tx_hash: str


@dataclass
class MarketFlow:
    market_id: str
    condition_id: str
    title: str
    category: str
    probability: float
    volume: float
    hhi: float                # 0-1 concentration score
    concentration_label: str  # "Low" | "Medium" | "High"
    top_wallets: list[dict]   # [{wallet, name, net_usd, side}]
    recent_trades: list[Trade]
    large_trades: list[Trade]
    url: str | None = None


@dataclass
class WalletSummary:
    wallet: str
    name: str
    total_buy_usd: float
    total_sell_usd: float
    markets_active: int
    recent_markets: list[str]  # titles of markets traded


@dataclass
class OrderFlowPayload:
    markets: list[MarketFlow] = field(default_factory=list)
    top_wallets: list[WalletSummary] = field(default_factory=list)
    global_large_trades: list[dict] = field(default_factory=list)
    error: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _concentration_label(hhi: float) -> str:
    if hhi >= 0.25:
        return "High"
    if hhi >= 0.10:
        return "Medium"
    return "Low"


def _compute_hhi(trades: list[Trade]) -> tuple[float, list[dict]]:
    """Aggregate net exposure per wallet and compute HHI."""
    wallet_net: dict[str, float] = {}
    wallet_names: dict[str, str] = {}

    for t in trades:
        key = t.wallet
        wallet_names[key] = t.name or t.wallet[:10] + "…"
        # BUY-YES or SELL-NO → long; SELL-YES or BUY-NO → short
        if (t.side == "BUY" and t.outcome.lower() in ("yes", "y")) or \
           (t.side == "SELL" and t.outcome.lower() in ("no", "n")):
            wallet_net[key] = wallet_net.get(key, 0) + t.usd
        else:
            wallet_net[key] = wallet_net.get(key, 0) - t.usd

    if not wallet_net:
        return 0.0, []

    total = sum(abs(v) for v in wallet_net.values()) or 1.0
    shares = {w: abs(v) / total for w, v in wallet_net.items()}
    hhi = sum(s ** 2 for s in shares.values())

    top = sorted(
        [
            {
                "wallet": w,
                "name": wallet_names.get(w, w[:10] + "…"),
                "net_usd": round(wallet_net[w], 1),
                "share_pct": round(shares[w] * 100, 1),
                "side": "long" if wallet_net[w] >= 0 else "short",
            }
            for w in wallet_net
        ],
        key=lambda x: abs(x["net_usd"]),
        reverse=True,
    )[:5]

    return round(hhi, 4), top


def _parse_trades(raw: list[dict]) -> list[Trade]:
    out: list[Trade] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        try:
            size = float(r.get("size") or 0)
            price = float(r.get("price") or 0)
            usd = r.get("usdcSize")   # prefer the API's own figure if this response ever has it
            usd = float(usd) if usd not in (None, "") else size * price
            out.append(
                Trade(
                    wallet=str(r.get("proxyWallet") or ""),
                    name=str(r.get("pseudonym") or r.get("name") or ""),
                    side=str(r.get("side") or "").upper(),
                    outcome=str(r.get("outcome") or ""),
                    size=size,
                    price=price,
                    usd=usd,
                    timestamp=str(r.get("timestamp") or ""),
                    tx_hash=str(r.get("transactionHash") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


# ── Public API ────────────────────────────────────────────────────────────────

def orderflow_data(connection: Any, max_markets: int = _TOP_MARKETS) -> OrderFlowPayload:
    """Main entry point: fetch order-flow data for top finance markets."""
    # 1. Pull top markets from DB
    cur = connection.cursor()
    cats_placeholder = ",".join(f"'{c}'" for c in sorted(_RELEVANT_CATEGORIES))
    cur.execute(
        f"""
        SELECT m.market_id, m.title, m.category, m.url, m.raw_json,
               MAX(s.volume) AS volume, s.liquidity, s.probability
        FROM markets m
        JOIN snapshots s ON s.market_id = m.market_id AND s.source = m.source
        WHERE m.source = 'polymarket'
          AND s.volume > 100000
          AND m.category IN ({cats_placeholder})
        GROUP BY m.market_id
        ORDER BY volume DESC
        LIMIT 60
        """,
    )
    rows = cur.fetchall()

    # Extract conditionId from raw_json
    candidates: list[dict] = []
    for r in rows:
        row = dict(r)
        raw = json.loads(row.get("raw_json") or "{}")
        condition_id = raw.get("market", {}).get("conditionId", "")
        if not condition_id:
            continue
        candidates.append({**row, "condition_id": condition_id,
                            "category": (row.get("category") or "").lower()})

    # Take top N by volume
    top = candidates[:max_markets]
    if not top:
        return OrderFlowPayload(error="No markets found in DB")

    # 2. Fetch trades for each market
    market_flows: list[MarketFlow] = []
    wallet_totals: dict[str, dict] = {}   # wallet → {buy, sell, markets, names}

    for m in top:
        cid = m["condition_id"]
        try:
            raw_trades = fetch_json(
                f"{_TRADES_URL}?market={cid}&limit={_TRADES_PER_MARKET}",
                timeout=15,
            )
        except FetchError:
            raw_trades = []

        if not isinstance(raw_trades, list):
            raw_trades = []

        trades = _parse_trades(raw_trades)
        hhi, top_wallets = _compute_hhi(trades)
        large = [t for t in trades if t.usd >= _LARGE_TRADE_THRESHOLD]

        # Accumulate wallet-level stats
        for t in trades:
            if not t.wallet:
                continue
            entry = wallet_totals.setdefault(
                t.wallet,
                {"name": t.name, "buy": 0.0, "sell": 0.0, "markets": set()},
            )
            if t.name:
                entry["name"] = t.name
            entry["markets"].add(m["title"])
            if t.side == "BUY":
                entry["buy"] += t.usd
            else:
                entry["sell"] += t.usd

        market_flows.append(
            MarketFlow(
                market_id=m["market_id"],
                condition_id=cid,
                title=m["title"],
                category=m.get("category") or "",
                probability=float(m.get("probability") or 0),
                volume=float(m.get("volume") or 0),
                hhi=hhi,
                concentration_label=_concentration_label(hhi),
                top_wallets=top_wallets,
                recent_trades=trades[:10],
                large_trades=large[:5],
                url=m.get("url"),
            )
        )

    # 3. Global top wallets (by total notional)
    top_wallets_list = sorted(
        [
            WalletSummary(
                wallet=w,
                name=v["name"] or w[:12] + "…",
                total_buy_usd=round(v["buy"], 0),
                total_sell_usd=round(v["sell"], 0),
                markets_active=len(v["markets"]),
                recent_markets=list(v["markets"])[:3],
            )
            for w, v in wallet_totals.items()
            if v["buy"] + v["sell"] > 1000
        ],
        key=lambda x: x.total_buy_usd + x.total_sell_usd,
        reverse=True,
    )[:10]

    # 4. Global large trades across all markets (flat list for the feed)
    all_large: list[dict] = []
    for mf in market_flows:
        for t in mf.large_trades:
            all_large.append(
                {
                    "market_title": mf.title[:60],
                    "market_url": mf.url,
                    "wallet": t.wallet,
                    "name": t.name or t.wallet[:10] + "…",
                    "side": t.side,
                    "outcome": t.outcome,
                    "usd": round(t.usd, 0),
                    "shares": round(t.size, 1),
                    "price": round(t.price, 3),
                    "timestamp": t.timestamp,
                }
            )
    all_large.sort(key=lambda x: x["usd"], reverse=True)

    return OrderFlowPayload(
        markets=market_flows,
        top_wallets=top_wallets_list,
        global_large_trades=all_large[:20],
    )

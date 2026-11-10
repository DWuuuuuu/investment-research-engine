"""FOMC rate path — market-implied probabilities for upcoming Fed decisions.

Built for the Macro page. Answers one question per meeting: what does the market
think the Fed will do, and how much tightening/easing is priced cumulatively.

WHY THIS IS NOT "CME FedWatch"
------------------------------
CME's published FedWatch percentages are not obtainable programmatically. Their
Data Terms of Use prohibit automated access and the endpoints return an explicit
IP block ("suspected web scraping activity"). We do not work around that.

What this module does instead is use the SAME underlying economics from two
independent, openly-accessible sources and show them side by side:

  1. PRIMARY — Kalshi `KXFEDDECISION-*` markets. A regulated exchange quoting
     the outcome directly as a probability, for every scheduled meeting. No
     methodology assumptions at all: the price IS the probability.

  2. CROSS-CHECK — 30-Day Fed Funds futures (ZQ), the very instrument CME's
     FedWatch is derived from, via the front-month contract. Implied average
     EFFR for the contract month = 100 - price. Compared against the average
     EFFR implied by the Kalshi distribution over the same month.

  3. ANCHOR — FRED `DFEDTARU`/`DFEDTARL` (current target range) and `EFFR`.

Measured 2026-09-10 the two disagreed by 1.6 bp, which is inside the bid-ask
spread on either. That agreement is the reason this panel is trustworthy; when
the `futures_check.gap_bp` widens materially, treat BOTH readings with suspicion
rather than picking the one you like.

WHY NOT THE EXISTING product/fedwatch.py
----------------------------------------
That module serves the separate prediction-markets dashboard and reads Polymarket
rows out of `data/prediction_markets.sqlite`. As of 2026-09-10 that DB's newest
snapshot was 2026-08-26 — fifteen days stale, for a meeting six days away. It is
left untouched; this module fetches live and is the one the Macro page uses.

NUMBERS THAT NEED CARE
----------------------
Kalshi's API returns prices in `*_dollars` fields (0.44 = 44%) and sizes in
`*_fp` fields. The older un-suffixed names (`yes_bid`, `last_price`, `volume`)
are still present in the schema but now come back None — anything reading those
silently sees no data.

Mid-quotes do not sum to 1. Across the six meetings the raw sum ran 0.975 to
1.030, because each leg carries its own spread. Probabilities are therefore
normalised, and the pre-normalisation sum is reported as `raw_sum` so a badly
quoted meeting is visible rather than hidden.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_SERIES = "KXFEDDECISION"
_FRED = "https://api.stlouisfed.org/fred/series/observations"

_DATA = Path(__file__).resolve().parent.parent / "data" / "fomc_cache"
_TTL = 10 * 60          # this moves intraday; keep it short
_MAX_MEETINGS = 8

# Canonical ordering, easing -> tightening, with the bp each implies.
_BUCKETS: list[tuple[str, str, float]] = [
    ("Cut >25bps",         "cut_50",  -50.0),
    ("Cut 25bps",          "cut_25",  -25.0),
    ("Fed maintains rate", "hold",      0.0),
    ("Hike 25bps",         "hike_25",  25.0),
    ("Hike >25bps",        "hike_50",  50.0),
]
_BY_TITLE = {t: (k, bp) for t, k, bp in _BUCKETS}

# Days of the contract month governed by the PREVIOUS decision. A Fed decision
# takes effect the day after the meeting ends, so a late-month meeting leaves
# only a few days at the new rate — which is what makes the front-month contract
# mostly a read on the PRIOR meeting.
_EFFECTIVE_LAG_DAYS = 1


def _get(url: str, timeout: float = 20) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ── FRED anchor ──────────────────────────────────────────────────────────
def _fred_latest(series: str, key: str) -> tuple[float | None, str | None]:
    if not key:
        return None, None
    url = _FRED + "?" + urllib.parse.urlencode(
        {"series_id": series, "api_key": key, "file_type": "json",
         "sort_order": "desc", "limit": 8})
    try:
        obs = _get(url).get("observations", [])
    except Exception:                              # noqa: BLE001
        return None, None
    for o in obs:                                  # FRED writes "." for no print
        v = o.get("value")
        if v not in (None, "", "."):
            try:
                return float(v), o.get("date")
            except ValueError:
                continue
    return None, None


# ── Kalshi ───────────────────────────────────────────────────────────────
def _meeting_events() -> list[dict]:
    p = _get(f"{_KALSHI}/events?series_ticker={_SERIES}&status=open&limit=40")
    evs = p.get("events", []) if isinstance(p, dict) else []
    return [e for e in evs if e.get("event_ticker")]


def _meeting_probs(event_ticker: str) -> dict[str, Any]:
    """One meeting's distribution. Returns a tri-state `state`, never a silent 0."""
    out: dict[str, Any] = {"event": event_ticker, "state": "unavailable"}
    try:
        p = _get(f"{_KALSHI}/markets?event_ticker={urllib.parse.quote(event_ticker)}&limit=60")
    except Exception as exc:                       # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    mks = p.get("markets", []) if isinstance(p, dict) else []
    if not mks:
        out["error"] = "no markets returned"
        return out

    legs, raw, spreads, vol, oi, close_at = {}, 0.0, [], 0.0, 0.0, None
    for m in mks:
        title = m.get("yes_sub_title") or m.get("subtitle") or ""
        meta = _BY_TITLE.get(title)
        if not meta:
            continue
        key, _bp = meta
        bid, ask = m.get("yes_bid_dollars"), m.get("yes_ask_dollars")
        if bid is None or ask is None:
            continue
        try:
            bid, ask = float(bid), float(ask)
        except (TypeError, ValueError):
            continue
        mid = (bid + ask) / 2.0
        legs[key] = mid
        raw += mid
        spreads.append(ask - bid)
        for src, acc in (("volume_fp", "vol"), ("open_interest_fp", "oi")):
            try:
                v = float(m.get(src) or 0)
            except (TypeError, ValueError):
                v = 0.0
            if acc == "vol":
                vol += v
            else:
                oi += v
        close_at = close_at or m.get("close_time")

    if not legs or raw <= 0:
        out["error"] = "no priced legs"
        return out

    # Normalise: mids carry a spread each, so the raw sum is not 1.
    probs = {k: legs.get(k, 0.0) / raw for k, _, _ in
             [(k, None, None) for _, k, _ in _BUCKETS]}
    exp_bp = sum(probs.get(k, 0.0) * bp for _, k, bp in _BUCKETS)
    hike = probs.get("hike_25", 0.0) + probs.get("hike_50", 0.0)
    cut = probs.get("cut_25", 0.0) + probs.get("cut_50", 0.0)

    out.update({
        "state": "ok",
        "probs": {k: round(v, 4) for k, v in probs.items()},
        "p_hike": round(hike, 4), "p_cut": round(cut, 4),
        "p_hold": round(probs.get("hold", 0.0), 4),
        "expected_bp": round(exp_bp, 1),
        "raw_sum": round(raw, 4),
        "max_spread": round(max(spreads), 4) if spreads else None,
        "volume": round(vol, 1), "open_interest": round(oi, 1),
        "close_time": close_at,
        "meeting_date": (close_at or "")[:10] or None,
    })
    return out


# ── Fed funds futures cross-check ────────────────────────────────────────
def _month_split(meeting_iso: str | None) -> tuple[int, int, int] | None:
    """(days at the prior rate, days at the new rate, days in month) for the
    contract month, given when that month's decision takes effect."""
    if not meeting_iso:
        return None
    try:
        d = date.fromisoformat(meeting_iso[:10])
    except ValueError:
        return None
    import calendar
    n = calendar.monthrange(d.year, d.month)[1]
    eff = d.day + _EFFECTIVE_LAG_DAYS               # first day at the new rate
    before = max(0, min(n, eff - 1))
    return before, n - before, n


# CME month codes. ZQV26 -> V = October, 26 = 2026.
_MONTH_CODE = {"F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
               "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12}


def _contract_month(sym: str | None) -> tuple[int, int] | None:
    """('ZQV26.CBT') -> (2026, 10). Returns None if unparseable."""
    if not sym:
        return None
    core = sym.split(".")[0].upper()
    if not core.startswith("ZQ") or len(core) < 5:
        return None
    code, yy = core[2], core[3:5]
    if code not in _MONTH_CODE or not yy.isdigit():
        return None
    return 2000 + int(yy), _MONTH_CODE[code]


def _futures_check(meetings: list[dict], effr: float | None) -> dict[str, Any]:
    """ZQ front month vs the same month implied by the Kalshi distribution.

    The contract month is read from the contract symbol, NOT guessed from the
    meeting list. An earlier version simply took the second priced meeting,
    which happened to be right in September 2026 and would silently compare the
    wrong month whenever the front contract covers a month with no meeting
    (Feb, May, Aug, Nov in a typical year) or has not yet rolled.
    """
    out: dict[str, Any] = {"state": "unavailable"}
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        t = yf.Ticker("ZQ=F")
        h = t.history(period="5d")
        if h is None or h.empty:
            out["error"] = "no ZQ=F history"
            return out
        px = float(h["Close"].iloc[-1])
        as_of = h.index[-1].date().isoformat()
        try:
            contract = (t.info or {}).get("underlyingSymbol")
        except Exception:                          # noqa: BLE001
            contract = None
    except Exception as exc:                       # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    implied = 100.0 - px                            # ZQ settles to 100 - avg EFFR
    ym = _contract_month(contract)
    out.update({"state": "ok", "price": round(px, 4), "as_of": as_of,
                "contract": contract, "implied_avg_effr": round(implied, 4),
                "contract_month": (f"{ym[0]}-{ym[1]:02d}" if ym else None)})

    ok = [m for m in meetings if m.get("state") == "ok" and m.get("meeting_date")]
    if effr is None or not ok:
        out["note"] = "no priced meetings to compare"
        return out
    if not ym:
        out["note"] = f"could not parse contract month from {contract!r}"
        return out

    # Rate prevailing entering the contract month = EFFR plus every expected
    # move from meetings that land BEFORE that month.
    prior_bp = 0.0
    in_month = None
    for m in ok:
        d = date.fromisoformat(m["meeting_date"][:10])
        if (d.year, d.month) == ym:
            in_month = m
            break
        if (d.year, d.month) < ym:
            prior_bp += m.get("expected_bp") or 0.0

    r_before = effr + prior_bp / 100.0
    if in_month is None:
        # No meeting that month: the whole month sits at one rate.
        kalshi_avg = r_before
        split = None
    else:
        sp = _month_split(in_month.get("meeting_date"))
        if not sp:
            out["note"] = "could not split the contract month"
            return out
        before, after, n = sp
        r_after = r_before + (in_month.get("expected_bp") or 0.0) / 100.0
        kalshi_avg = (before * r_before + after * r_after) / n
        split = {"days_at_prior_rate": before, "days_at_new_rate": after,
                 "days_in_month": n}

    out.update({
        "compare_month": f"{ym[0]}-{ym[1]:02d}",
        "meeting_in_month": in_month.get("meeting_date") if in_month else None,
        "kalshi_implied_avg_effr": round(kalshi_avg, 4),
        "gap_bp": round((implied - kalshi_avg) * 100, 1),
        "split": split,
        "agrees": abs(implied - kalshi_avg) * 100 <= 10.0,
    })
    return out


# ── assembly ─────────────────────────────────────────────────────────────
def fomc_path(fred_key: str = "", force: bool = False) -> dict[str, Any]:
    cache = _DATA / "fomc_path.json"
    if not force and cache.exists() and (time.time() - cache.stat().st_mtime) < _TTL:
        try:
            return json.loads(cache.read_text())
        except Exception:                          # noqa: BLE001
            pass

    tar_u, tar_u_d = _fred_latest("DFEDTARU", fred_key)
    tar_l, _ = _fred_latest("DFEDTARL", fred_key)
    effr, effr_d = _fred_latest("EFFR", fred_key)
    if effr is None:
        effr, effr_d = _fred_latest("DFF", fred_key)

    try:
        evs = _meeting_events()
    except Exception as exc:                       # noqa: BLE001
        return {"status": "error", "error": f"Kalshi events: {type(exc).__name__}: {exc}"}

    meetings = [_meeting_probs(e["event_ticker"]) for e in evs[:_MAX_MEETINGS]]
    for m, e in zip(meetings, evs):
        m["title"] = e.get("title") or e["event_ticker"]
    meetings = [m for m in meetings if m.get("state") == "ok" or m.get("error")]
    meetings.sort(key=lambda m: m.get("meeting_date") or "9999")

    # Cumulative path: expected bp and implied range after each meeting.
    cum = 0.0
    for m in meetings:
        if m.get("state") != "ok":
            continue
        cum += m.get("expected_bp") or 0.0
        m["cum_bp"] = round(cum, 1)
        if tar_l is not None and tar_u is not None:
            m["implied_range"] = (f"{tar_l + cum / 100:.2f}–{tar_u + cum / 100:.2f}%")

    out = {
        "status": "ready", "built_at": time.time(),
        "asof_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current": {
            "target_lower": tar_l, "target_upper": tar_u,
            "target_range": (f"{tar_l:.2f}–{tar_u:.2f}%"
                             if tar_l is not None and tar_u is not None else None),
            "effr": effr, "effr_date": effr_d, "target_date": tar_u_d,
        },
        "meetings": meetings,
        "next_meeting": next((m for m in meetings if m.get("state") == "ok"), None),
        "futures_check": _futures_check(meetings, effr),
        "source": {
            "primary": "Kalshi KXFEDDECISION (regulated exchange, direct probability)",
            "cross_check": "CME 30-Day Fed Funds futures, front month (ZQ=F) via Yahoo",
            "anchor": "FRED DFEDTARU / DFEDTARL / EFFR",
        },
        "caveats": [
            "NOT CME FedWatch. CME's published percentages are not programmatically "
            "accessible — their Data Terms of Use prohibit automated access and the "
            "endpoints return an explicit IP block. This uses the same economics from "
            "openly available sources, so the numbers will not tie exactly to CME's.",
            "Probabilities are normalised bid/ask midpoints. Each leg carries its own "
            "spread, so the raw sum is not 1 — it is reported per meeting as `raw_sum` "
            "so a thinly quoted meeting is visible rather than hidden.",
            "Later meetings are far less liquid than the next one. Check volume and "
            "open interest before reading much into a distant meeting.",
            "A prediction-market price is a risk-neutral probability, not a forecast. "
            "It embeds whatever risk premium traders demand.",
        ],
    }
    try:
        _DATA.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".partial")         # never truncate a live cache
        tmp.write_text(json.dumps(out))
        tmp.replace(cache)
    except Exception:                              # noqa: BLE001
        pass
    return out

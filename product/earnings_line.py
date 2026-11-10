"""Trailing-twelve-month EPS series — the "Earnings Line" on an O'Neil chart.

O'Neil's Datagraph overlays a TTM earnings line on the price panel. Because
both are drawn on a log scale, the VERTICAL GAP between price and the earnings
line is the P/E ratio, rendered visually: parallel lines mean a constant
multiple, a widening gap means multiple expansion.

EPS BASIS — the decision this module exists to enforce.

There are two available definitions and they are NOT interchangeable. Measured
across a sample of 40 matched quarters, 52% differed by more than 10%, and the
divergence is not confined to exotic names (NVDA 27.8%, PFE 143.9%, CRWD
161.1%). The choice materially changes the line for most stocks.

  GAAP diluted EPS (yfinance income_stmt) is one statutory standard and so is
  the more comparable number in principle — but yfinance returns only FIVE
  quarters, which yields two TTM points. Unusable for a multi-year chart.

  Consensus "Reported EPS" (yfinance get_earnings_dates) runs 48-99 quarters
  deep and is what the market actually reacts to, at the cost that non-GAAP
  conventions vary from company to company.

Depth wins, because a two-point line is not a line. Consistency is therefore
ENGINEERED here rather than inherited, at three levels:

  a) WITHIN a series — one source, one field, for a stock's whole history.
     There is deliberately no fallback from consensus to GAAP to patch a hole:
     a missing quarter breaks the TTM window instead. A silent basis change
     mid-line is the single failure that would make the chart actively lie,
     so it is made impossible rather than merely unlikely.
  b) ACROSS stocks in a market — every US name uses Reported EPS; every CN
     name uses fina_indicator dt_eps (diluted), deduped to the latest
     announcement per period and de-cumulated. Same field, same TTM window.
  c) ACROSS markets, BY LEVEL — not comparable, and not presented as such.
     USD vs CNY, US consensus-adjusted vs PRC statutory. Only per-stock
     quantities (trend, growth, multiple) and the rebased index are portable.

OTHER DELIBERATE CHOICES:
  - Points are keyed by ANNOUNCEMENT date, never fiscal period end. Keying by
    period end would draw earnings onto the chart weeks before the market knew
    them — a lookahead artifact baked into the picture.
  - A TTM point requires four CONSECUTIVE quarters. Gaps are skipped, never
    interpolated, matching the rule used by the price caches.
  - Growth from a non-positive base is reported as a turnaround flag, not a
    percentage: -0.10 -> 0.20 is not "+300% growth" in any useful sense.
  - CN filings are cumulative year-to-date, so quarters are de-cumulated
    (Q2 = H1 - Q1, and so on) and the result is asserted to sum back to the
    reported full-year figure.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data" / "earnings_line"
_TTL = 7 * 24 * 3600           # earnings move quarterly; the US call costs ~3s
# Bumped when the payload SHAPE changes: a cache written by an older version
# lacks the newer fields and would keep serving them as missing until its TTL
# expired, which for a 7-day TTL is a week of silently degraded markers.
_SCHEMA = 2
_US_LIMIT = 60                 # quarters requested from get_earnings_dates
_MIN_Q = 4                     # four quarters make one TTM point

# Fixed multiple, identical for every stock in both markets, so the price-to-
# earnings-line gap means the same thing on every chart. Deliberately NOT a
# per-stock "best fit": refitting per stock is what destroys comparability.
FIXED_MULTIPLE = 20.0


def _slug(market: str, symbol: str) -> str:
    return f"{market}_{re.sub(r'[^A-Za-z0-9._-]', '_', symbol)}.json"


def _rolling_ttm(quarters: list[dict]) -> list[dict]:
    """quarters: ascending [{d, q, period}]. Emits a TTM point only where four
    CONSECUTIVE quarters are present."""
    out = []
    for i in range(_MIN_Q - 1, len(quarters)):
        win = quarters[i - _MIN_Q + 1:i + 1]
        if any(w["q"] is None for w in win):
            continue
        out.append({"d": quarters[i]["d"],
                    "period": quarters[i].get("period"),
                    "q": round(quarters[i]["q"], 4),
                    "est": quarters[i].get("est"),
                    "surprise": quarters[i].get("surprise"),
                    "ttm": round(sum(w["q"] for w in win), 4)})
    return out


def _us_quarters(symbol: str) -> tuple[list[dict], list[str]]:
    import yfinance as yf
    warn: list[str] = []
    ed = yf.Ticker(symbol).get_earnings_dates(limit=_US_LIMIT)
    if ed is None or ed.empty or "Reported EPS" not in ed.columns:
        return [], ["no earnings dates returned"]
    rep = ed[ed["Reported EPS"].notna()].sort_index()
    seen: dict[str, dict] = {}
    for idx, row in rep.iterrows():
        d = idx.date().strftime("%Y%m%d")       # ANNOUNCEMENT date
        def _f(col):
            if col not in rep.columns:
                return None
            v = row[col]
            return None if v != v else round(float(v), 4)
        seen[d] = {"d": d, "q": float(row["Reported EPS"]), "period": None,
                   "est": _f("EPS Estimate"), "surprise": _f("Surprise(%)")}
    qs = [seen[k] for k in sorted(seen)]
    if len(qs) < _MIN_Q:
        warn.append(f"only {len(qs)} reported quarters; need {_MIN_Q}")
    return qs, warn


def _cn_quarters(symbol: str) -> tuple[list[dict], list[str]]:
    """Tushare filings are cumulative YTD, so de-cumulate into discrete
    quarters and verify the year sums back to the reported full-year figure."""
    from . import cn_data
    warn: list[str] = []
    rows = cn_data.call("fina_indicator",
                        {"ts_code": symbol, "start_date": "20150101",
                         "end_date": time.strftime("%Y%m%d")},
                        "ann_date,end_date,eps,dt_eps")
    if not rows:
        return [], ["no fina_indicator rows"]
    # fina_indicator returns restatements for the same period — keep the latest.
    latest: dict[str, dict] = {}
    for r in rows:
        k = r.get("end_date")
        if not k:
            continue
        if k not in latest or (r.get("ann_date") or "") > (latest[k].get("ann_date") or ""):
            latest[k] = r

    def val(period: str):
        r = latest.get(period) or {}
        v = r.get("dt_eps")                      # diluted preferred
        return r.get("eps") if v is None else v

    prior = {"0630": "0331", "0930": "0630", "1231": "0930"}
    qs: list[dict] = []
    for period in sorted(latest):
        cum, q = val(period), period[4:]
        if cum is None:
            continue
        if q == "0331":
            disc = cum
        else:
            pv = val(period[:4] + prior.get(q, ""))
            if pv is None:
                continue                          # gap: skip, never interpolate
            disc = cum - pv
        ann = latest[period].get("ann_date") or period
        qs.append({"d": ann, "q": round(float(disc), 4), "period": period})

    # Assertion: de-cumulated quarters must reconstruct the reported FY figure.
    for yr in {p["period"][:4] for p in qs if p.get("period")}:
        got = [p for p in qs if p["period"].startswith(yr)]
        fy = val(yr + "1231")
        if fy is not None and len(got) == 4:
            if abs(sum(g["q"] for g in got) - fy) > 0.02:
                warn.append(f"{yr}: de-cumulated quarters do not sum to reported FY EPS")
    qs.sort(key=lambda x: x["d"])
    return qs, warn


def _growth(points: list[dict], back: int) -> tuple[float | None, str | None]:
    """Growth over `back` quarters. A non-positive base makes a percentage
    meaningless, so that case returns a flag instead of a number."""
    if len(points) <= back:
        return None, None
    now, base = points[-1]["ttm"], points[-1 - back]["ttm"]
    if base is None or now is None:
        return None, None
    if base <= 0:
        return None, ("turnaround from loss" if now > 0 else "loss to loss")
    return round((now / base - 1) * 100, 1), None


def get_ttm_eps(market: str, symbol: str, refresh: bool = False) -> dict:
    market = "cn" if market == "cn" else "us"
    _DATA.mkdir(parents=True, exist_ok=True)
    cache = _DATA / _slug(market, symbol)
    if not refresh and cache.exists() and (time.time() - cache.stat().st_mtime) < _TTL:
        try:
            cached = json.loads(cache.read_text())
            if cached.get("schema") == _SCHEMA:
                return cached
        except Exception:                          # noqa: BLE001
            pass

    base = {"schema": _SCHEMA,
            "symbol": symbol, "market": market, "fixed_multiple": FIXED_MULTIPLE,
            "basis": "consensus_reported" if market == "us" else "statutory_diluted",
            "basis_label": ("Consensus reported EPS (TTM)" if market == "us"
                            else "Statutory diluted EPS · 摊薄 (TTM)"),
            "currency": "USD" if market == "us" else "CNY"}
    try:
        qs, warn = _us_quarters(symbol) if market == "us" else _cn_quarters(symbol)
    except Exception as exc:                       # noqa: BLE001
        return {**base, "status": "error", "error": str(exc), "points": []}

    pts = _rolling_ttm(qs)
    if not pts:
        out = {**base, "status": "unavailable", "points": [], "quarters": len(qs),
               "warnings": warn or ["not enough consecutive quarters for a TTM point"]}
        cache.write_text(json.dumps(out))          # cache the negative too
        return out

    g1, f1 = _growth(pts, 4)
    g2, f2 = _growth(pts, 8)
    latest = pts[-1]["ttm"]
    out = {**base, "status": "ready", "points": pts,
           "quarters": len(qs), "ttm_points": len(pts),
           "latest_ttm": latest, "latest_date": pts[-1]["d"],
           "negative_ttm": latest <= 0,
           "growth_1y": g1, "growth_1y_flag": f1,
           "growth_2y": g2, "growth_2y_flag": f2,
           "warnings": warn, "built_at": time.time()}
    cache.write_text(json.dumps(out))
    return out

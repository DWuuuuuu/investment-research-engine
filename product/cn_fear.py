"""A-share Fear & Greed Index — ported into the platform from the standalone
research project (Quant/Projects/AShare_Fear_Greed_Index).

Composite market-sentiment gauge (0-100, 100 = greed) from six behavioural legs,
each mapped to a rolling 5-year percentile (point-in-time; never a full-sample
z-score).

RETRACTED CLAIM (2026-08-11): this file previously claimed the gauge was
"validated against forward CSI300 returns" with a specific "U-shaped" forward-
return pattern (both fear and greed tails bullish, greed's top decile averaging
+28.8%/yr) and shipped that as an actionable "don't sell during greed" directive
in the UI. That claim did not hold up under two independent checks:
  1. A data-integrity audit reproduced this using the platform's own stored
     history and current CSI300 prices: greed >75 forward return was +5.5%
     (not +28.8%), and the top decile specifically was -0.3% — negative, the
     opposite sign of what was claimed.
  2. The Indicators Pool documentation's own review of the source project found
     its README shows the 25/75 timing rule TIES a drawdown-matched benchmark —
     i.e. no edge survives once you control for greed periods simply coinciding
     with already-elevated prices. Their words: "an implemented development
     benchmark whose incremental edge did not survive honest accounting."
Two independent, differently-motivated reviews agreeing is enough to treat the
original claim as false, not merely unproven. The composite SCORE below (the
6-leg percentile average) is unaffected — only the "this predicts forward
returns" layer on top of it is retracted. Treat this as a positioning-intensity
gauge (how stretched sentiment is), not a market-timing signal, until a real
point-in-time, leakage-free backtest is built and independently reviewed.

Fetches ~8 years via the platform's cn_data wrapper, caches the computed series to
data/cn_fear.csv, rebuilds in the background when stale (daily TTL).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
_CACHE = _DATA / "cn_fear.csv"
_TTL = 20 * 3600           # rebuild at most ~daily
_ROLL = 1250               # 5y percentile window
_MINP = 500                # 2y warmup
_YEARS_BACK = 9            # enough history for a valid current 5y percentile + display

_state = {"building": False, "ts": 0.0, "error": None}
_lock = threading.Lock()

_ZONES = [
    (20, "Extreme Fear", "#16a34a"),   # green = opportunity (fear is a buy)
    (40, "Fear", "#65a30d"),
    (60, "Neutral", "#a16207"),
    (80, "Greed", "#ea580c"),
    (101, "Extreme Greed", "#dc2626"),
]
_LEG_LABELS = {
    "leverage_temp": "Margin leverage (level)",
    "leverage_flow": "Leverage flow (20d Δ)",
    "turnover_heat": "Turnover heat",
    "valuation_pct": "Valuation (CSI300 PE)",
    "price_strength": "Price strength (vs 52w high)",
    "momentum": "Momentum (vs 125d mean)",
}
# per-leg interpretation (each is a rolling 5-year percentile, 100 = most extreme/greedy)
_LEG_DESC = {
    "leverage_temp": "Total margin-financing balance (borrowed money, SSE+SZSE) ÷ market float value — how much of the market is running on leverage. Higher = more borrowed conviction = greedier/frothier; extreme highs mark leveraged tops. An intensity leg.",
    "leverage_flow": "The 20-day change in margin balance — is leverage building or unwinding? Greed is borrowing that's ACCELERATING. This leg de-levers fast in selloffs while the level lags, so a collapsing flow even at a high level (like now) is the classic 'leveraged top rolling over' tell.",
    "turnover_heat": "Float-weighted free-float turnover (how fast shares change hands), 20-day mean — the churn/speed of speculation (击鼓传花). High = frenzied trading = greed; low = apathy/capitulation. An intensity leg.",
    "valuation_pct": "CSI 300 trailing P/E as a rolling percentile — the price of optimism. High = investors paying up (greed); low = cheap (fear). Cheapness vs the market's OWN 5-year range, not vs the US.",
    "price_strength": "CSI 300 close ÷ its own 252-day (52-week) high — proximity to the yearly peak (the CNN Fear & Greed 'price strength' leg). Near the high = confidence/greed; deep below = fear. A direction leg.",
    "momentum": "CSI 300 close ÷ its 125-day (~6-month) average — trend participation. Above & rising = greed; below = fear. Supplies the direction the intensity legs can't. A direction leg.",
}


def _zone(v: float):
    for hi, name, color in _ZONES:
        if v < hi:
            return name, color
    return "Extreme Greed", "#dc2626"


# ── build ────────────────────────────────────────────────────────────────────

def _fetch_raw():
    import pandas as pd
    from . import cn_data
    from datetime import date
    y0 = date.today().year - _YEARS_BACK
    years = [(f"{y}0101", f"{y}1231") for y in range(y0, date.today().year + 1)]

    def _df(rows):
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # market margin balance (SSE+SZSE)
    mg = []
    for s, e in years:
        rows = cn_data.call("margin", {"start_date": s, "end_date": e},
                            "trade_date,exchange_id,rzye")
        time.sleep(0.12)
        if rows:
            mg.append(_df(rows))
    margin = pd.concat(mg)
    margin = margin[margin["exchange_id"].isin(["SSE", "SZSE"])]
    margin["rzye"] = pd.to_numeric(margin["rzye"], errors="coerce")
    # Completeness guard: the combined leverage legs need BOTH SSE and SZSE margin
    # balances. On the latest day one exchange often publishes hours before the
    # other (e.g. 07-17: only SSE posted) — summing the partial figure would halve
    # the level and read a false "extreme low". Require both; else leave NaN so the
    # day fails the ≥5-leg test and drops out until the data completes.
    mp = margin.pivot_table(index="trade_date", columns="exchange_id", values="rzye", aggfunc="sum")
    if "SSE" in mp.columns and "SZSE" in mp.columns:
        margin = (mp["SSE"] + mp["SZSE"]).rename("rzye")   # NaN if either missing
    else:
        margin = margin.groupby("trade_date")["rzye"].sum().rename("rzye")

    def basics(code, name):
        out = []
        for s, e in years:
            rows = cn_data.call("index_dailybasic",
                                {"ts_code": code, "start_date": s, "end_date": e},
                                "trade_date,float_mv,turnover_rate_f,pe_ttm")
            time.sleep(0.12)
            if rows:
                out.append(_df(rows))
        d = pd.concat(out).set_index("trade_date").sort_index()
        for c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
        return d.add_prefix(name + "_")

    sse = basics("000001.SH", "sse")
    szc = basics("399001.SZ", "szc")
    hs300 = basics("000300.SH", "hs")

    px = []
    for s, e in years:
        rows = cn_data.call("index_daily", {"ts_code": "000300.SH", "start_date": s, "end_date": e},
                            "trade_date,close")
        time.sleep(0.12)
        if rows:
            px.append(_df(rows))
    close = pd.concat(px).set_index("trade_date").sort_index()["close"]
    close = pd.to_numeric(close, errors="coerce").rename("hs300_close")

    raw = pd.concat([margin, sse, szc, hs300, close], axis=1).sort_index()
    raw.index = pd.to_datetime(raw.index)
    return raw


def _build(raw):
    import numpy as np
    import pandas as pd
    r = raw.dropna(subset=["hs300_close"]).copy()

    def roll_pct(s):
        return s.rolling(_ROLL, min_periods=_MINP).rank(pct=True)

    lev = r["rzye"] / (r["sse_float_mv"] + r["szc_float_mv"])
    lev_flow = np.log(r["rzye"]).diff(20)
    turn = ((r["sse_turnover_rate_f"] * r["sse_float_mv"] + r["szc_turnover_rate_f"] * r["szc_float_mv"])
            / (r["sse_float_mv"] + r["szc_float_mv"])).rolling(20).mean()
    strength = r["hs300_close"] / r["hs300_close"].rolling(252).max()
    mom = r["hs300_close"] / r["hs300_close"].rolling(125).mean()

    comp = pd.DataFrame({
        "leverage_temp": roll_pct(lev),
        "leverage_flow": roll_pct(lev_flow),
        "turnover_heat": roll_pct(turn),
        "valuation_pct": roll_pct(r["hs_pe_ttm"]),
        "price_strength": roll_pct(strength),
        "momentum": roll_pct(mom),
    })
    fg = (comp.mean(axis=1) * 100).rename("fear_greed")
    fg = fg.where(comp.notna().sum(axis=1) >= 5)
    out = pd.concat([fg, comp * 100], axis=1).dropna(subset=["fear_greed"])
    return out


def _rebuild():
    with _lock:
        if _state["building"]:
            return
        _state["building"] = True
    try:
        df = _build(_fetch_raw())
        _DATA.mkdir(exist_ok=True)
        df.to_csv(_CACHE)
        _state["ts"] = time.time()
        _state["error"] = None
    except Exception as exc:                       # noqa: BLE001
        _state["error"] = str(exc)
    finally:
        _state["building"] = False


def ensure(background=True):
    fresh = _CACHE.exists() and (time.time() - _CACHE.stat().st_mtime) < _TTL
    if fresh:
        return
    if background and _CACHE.exists():
        if not _state["building"]:
            threading.Thread(target=_rebuild, daemon=True).start()
        return
    _rebuild()                                     # blocking (first-ever build)


# ── serve ────────────────────────────────────────────────────────────────────

def get_fear(history_days: int = 4000) -> dict:   # default ≈ full valid series
    import pandas as pd
    ensure(background=True)
    if not _CACHE.exists():
        if _state["error"]:
            return {"status": "error", "error": _state["error"]}
        return {"status": "building", "note": "First build in progress (fetching ~9y of Tushare data) — retry shortly."}
    df = pd.read_csv(_CACHE, index_col=0, parse_dates=True)
    if df.empty:
        return {"status": "error", "error": "Empty index."}
    last = df.iloc[-1]
    val = round(float(last["fear_greed"]), 1)
    name, color = _zone(val)
    prev = df["fear_greed"].iloc[-2] if len(df) > 1 else val
    m20 = df["fear_greed"].iloc[-21] if len(df) > 21 else val

    # 1 decimal so near-extreme percentiles read honestly (99.7, not a clamped-looking 100)
    legs = [{"key": k, "label": _LEG_LABELS[k], "value": round(float(last[k]), 1), "desc": _LEG_DESC[k]}
            for k in _LEG_LABELS if k in df.columns]
    hist = df["fear_greed"].tail(history_days)
    history = [{"d": d.strftime("%Y-%m-%d"), "v": round(float(v), 1)} for d, v in hist.items()]

    # Positioning-intensity read only — NOT a forward-return/timing claim (the
    # previous "validated U-shape" + specific return figures were retracted
    # 2026-08-11; see the module docstring for why).
    if val < 25:
        read = "Extreme fear — leverage, turnover and price strength all read stretched-low. A positioning-intensity extreme, not a validated buy signal."
    elif val > 75:
        read = "Extreme greed — leverage, turnover and price strength all read stretched-high. A positioning-intensity extreme, not a validated hold/sell signal."
    elif val < 40:
        read = "Fear — sentiment leaning weak, without being at an extreme."
    elif val > 60:
        read = "Greed — sentiment leaning hot, without being at an extreme."
    else:
        read = "Neutral — no strong read in either direction."

    return {
        "status": "ready",
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "value": val, "zone": name, "color": color,
        "change_1d": round(val - float(prev), 1),
        "change_20d": round(val - float(m20), 1),
        "legs": legs, "history": history, "read": read,
        "stale": (time.time() - _CACHE.stat().st_mtime) > _TTL,
        "extremes": {
            "fear_extreme": 25, "greed_extreme": 75,
            "note": "These are positioning-intensity thresholds only. An earlier claim that forward CSI300 returns are 'U-shaped' against this level (both tails bullish, greed's top decile averaging +28.8%/yr) was retracted 2026-08-11 — it did not reproduce under independent review (see module docstring). Treat extremes as 'sentiment is stretched,' not as a validated buy/sell timing signal.",
        },
    }

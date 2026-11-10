"""Signal Lab — validation loop v1: backtest one or more feature conditions on a
single stock or ETF against its own history.

Honest by design:
- Signals are computed only from data available at day t; forward returns run t → t+h.
- Reports BOTH all-signal-day averages (overlapping, inflated n) and episode onsets
  (first day the condition turns true) — episodes are the honest sample size.
- Baseline = unconditional forward return over the same period, so "edge" is
  signal-minus-baseline, not raw return.
- Single-name factor tests are statistically thin; caveats are returned with results.

US: yfinance ~10y adjusted daily history (P/E band reconstructed from ~4y of annual
EPS with a 90-day reporting lag — flagged as approximate).
CN: Tushare daily × adj_factor for returns + daily_basic for genuinely point-in-time
daily pe_ttm / pb / dividend-yield / turnover history (10+ yrs).
"""
from __future__ import annotations

import math
from datetime import date, timedelta


# ── feature menu (id → label + which markets support it) ─────────────────────
FEATURES = {
    "mom12":    {"label": "12-mo return %",            "markets": ["us", "cn"]},
    "ma200":    {"label": "Gap vs 200-day MA %",       "markets": ["us", "cn"]},
    "drawdown": {"label": "Drawdown from 52-wk high %","markets": ["us", "cn"]},
    "rsi14":    {"label": "RSI (14-day)",              "markets": ["us", "cn"]},
    "pe":       {"label": "Trailing P/E",              "markets": ["us", "cn"]},
    "pb":       {"label": "P/B",                       "markets": ["cn"]},
    "divyield": {"label": "Dividend yield %",          "markets": ["us", "cn"]},
    "turnover": {"label": "Turnover rate %",           "markets": ["cn"]},
}

_HORIZON_MENU = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "12m": 252, "24m": 504, "36m": 756}
_DEFAULT_HORIZONS = ["1m", "3m", "6m", "12m"]


def _rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn
    return 100 - 100 / (1 + rs)


def _load_us(ticker: str):
    """Return (df with close + feature columns, caveats list)."""
    import pandas as pd
    import yfinance as yf
    t = yf.Ticker(ticker)
    hist = t.history(period="10y", auto_adjust=True)
    if hist is None or hist.empty or len(hist) < 300:
        raise ValueError(f"Not enough price history for {ticker}.")
    hist.index = hist.index.tz_localize(None)
    df = pd.DataFrame({"close": hist["Close"]})
    caveats = []

    df["mom12"] = (df["close"] / df["close"].shift(252) - 1) * 100
    df["ma200"] = (df["close"] / df["close"].rolling(200).mean() - 1) * 100
    df["drawdown"] = (df["close"] / df["close"].rolling(252, min_periods=60).max() - 1) * 100
    df["rsi14"] = _rsi(df["close"])

    # dividend yield: trailing 365d dividend sum ÷ close (Dividends col from history)
    if "Dividends" in hist.columns and hist["Dividends"].sum() > 0:
        div_ttm = hist["Dividends"].rolling("365D").sum()
        df["divyield"] = div_ttm / df["close"] * 100
    else:
        df["divyield"] = float("nan")

    # trailing P/E from annual diluted EPS, stepped forward with a 90-day report lag
    try:
        fin = t.income_stmt
        eps_row = None
        for name in ("Diluted EPS", "Basic EPS"):
            if fin is not None and name in fin.index:
                eps_row = fin.loc[name].dropna()
                break
        if eps_row is not None and len(eps_row):
            eps = eps_row.sort_index()
            eps.index = pd.to_datetime(eps.index).tz_localize(None) + pd.Timedelta(days=90)
            eps_daily = eps.reindex(df.index.union(eps.index)).ffill().reindex(df.index)
            df["pe"] = df["close"].where(eps_daily > 0) / eps_daily
            caveats.append("US P/E band uses ~4 yrs of annual EPS stepped with a 90-day report lag — approximate; A-shares have true daily P/E history.")
        else:
            df["pe"] = float("nan")
    except Exception:
        df["pe"] = float("nan")
    return df, caveats


def _load_cn(ts_code: str):
    import pandas as pd
    from . import cn_data
    start = (date.today() - timedelta(days=3700)).strftime("%Y%m%d")
    daily = cn_data.call("daily", {"ts_code": ts_code, "start_date": start},
                         "ts_code,trade_date,close")
    adj = cn_data.call("adj_factor", {"ts_code": ts_code, "start_date": start},
                       "ts_code,trade_date,adj_factor")
    basic = cn_data.call("daily_basic", {"ts_code": ts_code, "start_date": start},
                         "ts_code,trade_date,pe_ttm,pb,dv_ratio,turnover_rate")
    if not daily or len(daily) < 300:
        raise ValueError(f"Not enough A-share history for {ts_code}.")
    dd = pd.DataFrame(daily).set_index("trade_date")
    af = pd.DataFrame(adj).set_index("trade_date") if adj else None
    df = pd.DataFrame(index=sorted(dd.index))
    close_raw = pd.to_numeric(dd["close"], errors="coerce").reindex(df.index)
    if af is not None and "adj_factor" in af.columns:
        factor = pd.to_numeric(af["adj_factor"], errors="coerce").reindex(df.index).ffill()
        df["close"] = close_raw * factor          # back-adjusted for returns
    else:
        df["close"] = close_raw
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    df["mom12"] = (df["close"] / df["close"].shift(252) - 1) * 100
    df["ma200"] = (df["close"] / df["close"].rolling(200).mean() - 1) * 100
    df["drawdown"] = (df["close"] / df["close"].rolling(252, min_periods=60).max() - 1) * 100
    df["rsi14"] = _rsi(df["close"])

    if basic:
        bb = pd.DataFrame(basic).set_index("trade_date")
        bb.index = pd.to_datetime(bb.index)
        bb = bb.sort_index().reindex(df.index)
        df["pe"] = pd.to_numeric(bb["pe_ttm"], errors="coerce")
        df["pb"] = pd.to_numeric(bb["pb"], errors="coerce")
        df["divyield"] = pd.to_numeric(bb["dv_ratio"], errors="coerce")
        df["turnover"] = pd.to_numeric(bb["turnover_rate"], errors="coerce")
    return df, []


def run_signal_test(market: str, ticker: str, conds: list[dict],
                    lookback_years: int = 10, horizons: list[str] | None = None) -> dict:
    """conds: [{feature, op ('gte'|'lte'), value}, …] AND-combined.
    lookback_years: evaluation window (features warm up on the full history first).
    horizons: labels from _HORIZON_MENU (default 1m/3m/6m/12m).
    Returns forward-return stats vs baseline + condition-long strategy vs buy-hold."""
    import pandas as pd

    horizons = [h for h in (horizons or _DEFAULT_HORIZONS) if h in _HORIZON_MENU] or _DEFAULT_HORIZONS
    lookback_years = max(1, min(int(lookback_years or 10), 10))

    ticker = ticker.strip().upper() if market != "cn" else ticker.strip()
    if not ticker:
        return {"status": "error", "error": "No ticker given."}
    if not conds:
        return {"status": "error", "error": "Add at least one condition."}
    for c in conds:
        f = c.get("feature")
        if f not in FEATURES:
            return {"status": "error", "error": f"Unknown feature '{f}'."}
        if market not in FEATURES[f]["markets"]:
            return {"status": "error", "error": f"{FEATURES[f]['label']} isn't available for this market."}

    try:
        df, caveats = _load_cn(ticker) if market == "cn" else _load_us(ticker)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    # evaluation window: features warm up on the full fetched history, then all
    # stats are computed only over the last `lookback_years`
    cutoff = df.index.max() - pd.Timedelta(days=round(lookback_years * 365.25))
    df = df[df.index >= cutoff]
    if len(df) < 250:
        return {"status": "error", "error": f"Only {len(df)} trading days in a {lookback_years}-year window — not enough."}

    # AND-combine conditions (NaN feature ⇒ condition false, day excluded)
    mask = pd.Series(True, index=df.index)
    cond_desc = []
    for c in conds:
        feat, op, val = c["feature"], c.get("op", "gte"), float(c.get("value", 0))
        series = df.get(feat)
        if series is None or series.dropna().empty:
            return {"status": "error", "error": f"No data for {FEATURES[feat]['label']} on {ticker}."}
        mask &= (series >= val) if op == "gte" else (series <= val)
        mask &= series.notna()
        cond_desc.append(f"{FEATURES[feat]['label']} {'≥' if op == 'gte' else '≤'} {val:g}")

    close = df["close"]
    n_days = int(mask.sum())
    if n_days < 5:
        return {"status": "error", "error": f"Condition true on only {n_days} days — too few to evaluate. Loosen the thresholds."}

    onsets = mask & ~mask.shift(1, fill_value=False)
    n_episodes = int(onsets.sum())

    horizons_out = []
    for hlabel in horizons:
        h = _HORIZON_MENU[hlabel]
        fwd = close.shift(-h) / close - 1
        sig, base = fwd[mask].dropna(), fwd.dropna()
        ons = fwd[onsets].dropna()
        if len(sig) < 3:
            continue
        horizons_out.append({
            "h": hlabel,
            "signal_avg": round(float(sig.mean()) * 100, 1),
            "onset_avg": round(float(ons.mean()) * 100, 1) if len(ons) else None,
            "baseline_avg": round(float(base.mean()) * 100, 1),
            "edge": round(float(sig.mean() - base.mean()) * 100, 1),
            "hit_rate": round(float((sig > 0).mean()) * 100),
            "base_hit": round(float((base > 0).mean()) * 100),
            "n_days": int(len(sig)), "n_onsets": int(len(ons)),
        })

    # ── condition-long strategy: hold while condition true (enter next day), else cash ──
    ret = close.pct_change().fillna(0.0)
    in_pos = mask.shift(1, fill_value=False)
    strat = (1 + ret.where(in_pos, 0.0)).cumprod()
    bh = (1 + ret).cumprod()
    first_valid = df[[c["feature"] for c in conds]].dropna().index
    if len(first_valid):
        s0 = first_valid[0]
        strat, bh = strat[strat.index >= s0], bh[bh.index >= s0]
        strat, bh = strat / strat.iloc[0], bh / bh.iloc[0]

    def _stats(curve):
        if len(curve) < 40:
            return None
        yrs = (curve.index[-1] - curve.index[0]).days / 365.25
        cagr = (float(curve.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0.5 else None
        daily = curve.pct_change().dropna()
        sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() else None
        maxdd = float((curve / curve.cummax() - 1).min()) * 100
        return {"cagr": round(cagr, 1) if cagr is not None else None,
                "sharpe": round(sharpe, 2) if sharpe is not None else None,
                "maxdd": round(maxdd, 1),
                "total": round((float(curve.iloc[-1]) - 1) * 100, 1)}

    step = max(1, len(strat) // 300)
    exposure = round(float(in_pos[in_pos.index >= strat.index[0]].mean()) * 100) if len(strat) else None

    caveats += [
        f"Single-instrument test: {n_episodes} independent episodes is the honest sample size (the {n_days} signal-day count double-counts overlapping windows).",
        "Cross-sectional factors (value, quality) prove themselves across many stocks — a single-name test mainly validates timing/band rules for THIS name.",
        "In-sample rule mining: thresholds tuned on the same history will overfit; treat edges under ~2-3pp as noise.",
    ]

    return {
        "status": "ready",
        "ticker": ticker, "market": market,
        "conditions": cond_desc,
        "period": {"from": str(strat.index[0].date()) if len(strat) else None,
                   "to": str(close.index[-1].date())},
        "n_days": n_days, "n_episodes": n_episodes, "exposure_pct": exposure,
        "horizons": horizons_out,
        "dates": [str(d.date()) for d in strat.index[::step]],
        "strategy": [round(float(v), 4) for v in strat.iloc[::step]],
        "buyhold": [round(float(v), 4) for v in bh.reindex(strat.index).iloc[::step]],
        "strategy_stats": _stats(strat), "buyhold_stats": _stats(bh),
        "caveats": caveats,
    }

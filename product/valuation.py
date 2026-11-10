"""Valuation-vs-own-history percentiles — the first Inefficiency Radar archetype
(§1.5D of the build blueprint): is this name cheap *relative to its own recent
history*, rather than in absolute terms?

"Cheap vs own history" needs only the stock's OWN time series of each ratio — not
the point-in-time cross-section — so it is buildable now (the sector-peer percentile
is what remains snapshot-gated). Reuses the Signal Lab loaders so the numbers agree
with the Validate page: CN gets genuine daily point-in-time P/E·P/B·yield from
Tushare daily_basic; US gets a reconstructed (flagged, ~4-5y) P/E band + yield.
"""
from __future__ import annotations

# metric key → (label, cheap-direction). "low" = cheap when the ratio is low
# (P/E, P/B); "high" = cheap when the ratio is high (dividend yield).
_METRICS = [
    ("pe", "P/E (trailing)", "low"),
    ("pb", "P/B", "low"),
    ("divyield", "Dividend yield", "high"),
]


def valuation_percentile(market: str, ticker: str, years: int = 5) -> dict:
    import numpy as np
    import pandas as pd
    from . import signal_lab

    ticker = ticker.strip().upper() if market != "cn" else ticker.strip()
    if not ticker:
        return {"status": "error", "error": "No ticker given."}
    years = max(1, min(int(years or 5), 10))
    try:
        df, caveats = signal_lab._load_cn(ticker) if market == "cn" else signal_lab._load_us(ticker)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    if df is None or df.empty:
        return {"status": "error", "error": f"No history for {ticker}."}

    cutoff = df.index.max() - pd.Timedelta(days=round(years * 365.25))
    win = df[df.index >= cutoff]

    metrics = []
    for key, label, direction in _METRICS:
        if key not in win.columns:
            continue
        s = win[key].replace([np.inf, -np.inf], np.nan).dropna()
        s = s[s > 0] if key in ("pe", "pb") else s
        if len(s) < 60:
            continue
        cur = float(s.iloc[-1])
        # percentile of current value within its own window (fraction ≤ current)
        below = float((s <= cur).mean()) * 100
        # "cheaper than X% of the window" — invert for low-is-cheap ratios
        cheaper_than = round(100 - below if direction == "low" else below)
        zone = "cheap" if cheaper_than >= 70 else "expensive" if cheaper_than <= 30 else "fair"
        step = max(1, len(s) // 260)
        hist = [{"d": d.strftime("%Y-%m-%d"), "v": round(float(v), 2)} for d, v in s.iloc[::step].items()]
        metrics.append({
            "key": key, "label": label, "direction": direction,
            "current": round(cur, 2),
            "cheaper_than": cheaper_than, "zone": zone,
            "min": round(float(s.min()), 2), "max": round(float(s.max()), 2),
            "median": round(float(s.median()), 2),
            "history": hist,
        })

    if not metrics:
        return {"status": "error", "error": f"Not enough valuation history for {ticker}."}

    # headline = the primary cheapness read (P/E if present, else first metric)
    primary = next((m for m in metrics if m["key"] == "pe"), metrics[0])
    return {
        "status": "ready", "ticker": ticker, "market": market, "years": years,
        "window": {"from": win.index.min().strftime("%Y-%m-%d"),
                   "to": win.index.max().strftime("%Y-%m-%d")},
        "metrics": metrics, "primary": primary["key"],
        "caveats": caveats,
        "note": "Percentile is against this stock's OWN last-{}-year range, not its sector. "
                "\"Cheaper than X%\" = the share of the window trading richer than today.".format(years),
    }

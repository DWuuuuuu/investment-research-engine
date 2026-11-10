"""Portfolio construction — lightweight batch quotes for a set of tickers.

Reuses the Filter universe snapshot (instant, already has price / P/E / PEG /
sector / market-cap / yield) and only falls back to a live yfinance fetch for
tickers not in the snapshot. Aggregates (weighted P/E etc.) and weighting schemes
are computed client-side; this just supplies clean per-ticker facts.
"""
from __future__ import annotations

_FIELDS = ("symbol", "name", "sector", "price", "pe", "peg", "div_yield", "market_cap")


def _from_row(sym: str, r: dict) -> dict:
    return {
        "symbol": sym,
        "name": r.get("name") or sym,
        "sector": r.get("sector") or r.get("industry") or "—",
        "price": r.get("price"),
        "pe": r.get("pe"),
        "peg": r.get("peg"),
        "div_yield": r.get("div_yield"),
        "market_cap": r.get("market_cap"),
        "source": "snapshot",
    }


def _fetch_us(tickers: list[str]) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf

    def one(sym: str):
        try:
            info = yf.Ticker(sym).info or {}
            px = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
            if not px:
                return sym, None
            pe = info.get("trailingPE")
            eg = info.get("earningsGrowth")
            peg = info.get("trailingPegRatio")
            if peg is None and pe and eg and eg > 0:
                peg = round(pe / (eg * 100), 2)
            dy = info.get("dividendYield")
            return sym, {
                "symbol": sym,
                "name": info.get("longName") or info.get("shortName") or sym,
                "sector": info.get("sector") or "—",
                "price": round(px, 2) if px else None,
                "pe": round(pe, 1) if pe else None,
                "peg": round(peg, 2) if peg else None,
                "div_yield": round(dy, 2) if dy else None,   # yfinance already in %
                "market_cap": info.get("marketCap"),
                "source": "live",
            }
        except Exception:
            return sym, None

    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, d in ex.map(one, tickers):
            out[sym] = d
    return out


def quote_batch(market: str, tickers: list[str]) -> dict:
    from . import filter_screener as fs

    seen, clean = set(), []
    for t in tickers:
        t = (t or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            clean.append(t)
    clean = clean[:80]
    if not clean:
        return {"status": "ready", "holdings": []}

    by = {}
    try:
        if market == "cn":
            fs.ensure_cn_snapshot()
            by = {r["symbol"]: r for r in (fs._CN.get("rows") or [])}
        else:
            fs.ensure_us_snapshot()
            by = {r["symbol"]: r for r in (fs._US.get("rows") or [])}
    except Exception:
        by = {}

    holdings, missing = [], []
    for t in clean:
        if t in by:
            holdings.append(_from_row(t, by[t]))
        else:
            missing.append(t)

    # live fallback (US only; A-shares outside the snapshot are rare / need Tushare)
    if missing and market != "cn":
        for sym, d in _fetch_us(missing).items():
            holdings.append(d or {"symbol": sym, "name": sym, "sector": "—", "price": None,
                                  "pe": None, "peg": None, "div_yield": None, "market_cap": None,
                                  "source": "unresolved"})
    elif missing:
        for sym in missing:
            holdings.append({"symbol": sym, "name": sym, "sector": "—", "price": None,
                             "pe": None, "peg": None, "div_yield": None, "market_cap": None,
                             "source": "unresolved"})

    # preserve the caller's ordering
    order = {t: i for i, t in enumerate(clean)}
    holdings.sort(key=lambda h: order.get(h["symbol"], 1e9))
    return {"status": "ready", "holdings": holdings,
            "unresolved": [h["symbol"] for h in holdings if h.get("source") == "unresolved"]}


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio backtest + risk metrics
# ══════════════════════════════════════════════════════════════════════════════

def _curve_stats(curve, bench_rets=None):
    """Rich performance/risk metrics from a normalised equity curve (pandas Series,
    DatetimeIndex, starts at 1.0). bench_rets optional for beta."""
    import numpy as np
    if curve is None or len(curve) < 30:
        return None
    yrs = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-6)
    total = float(curve.iloc[-1] / curve.iloc[0] - 1)
    cagr = float((curve.iloc[-1] / curve.iloc[0]) ** (1 / yrs) - 1) if yrs > 0.25 else None
    dr = curve.pct_change().dropna()
    vol = float(dr.std() * np.sqrt(252)) if len(dr) > 1 else None
    sharpe = float(dr.mean() * 252 / (dr.std() * np.sqrt(252))) if dr.std() else None
    # Downside deviation vs a 0 target, using ALL observations in the denominator
    # (up-days contribute 0 to the shortfall, they aren't excluded from the count).
    # Previously this took .std() of the negative-return subset alone, which
    # measures dispersion around THAT SUBSET's own mean rather than around the
    # 0 target, and divides by count-of-down-days instead of total days —
    # confirmed to inflate Sortino by a material amount (~17% on a test series;
    # exact effect depends on the return distribution). Fixed 2026-08-10.
    downside_shortfall = dr.clip(upper=0.0)
    dvol = float(np.sqrt((downside_shortfall ** 2).mean()) * np.sqrt(252)) if len(dr) > 1 else None
    sortino = float(dr.mean() * 252 / dvol) if dvol else None
    dd = curve / curve.cummax() - 1
    maxdd = float(dd.min())
    mar = float(cagr / abs(maxdd)) if (cagr is not None and maxdd < 0) else None
    # longest underwater stretch (days below a prior peak)
    under = dd < -1e-9
    longest = cur = 0
    run_start = None
    for ts, u in under.items():
        if u:
            run_start = run_start or ts
            cur = (ts - run_start).days
            longest = max(longest, cur)
        else:
            run_start = None
    # rolling 12-mo best / worst
    roll = curve.pct_change(252).dropna()
    best12 = float(roll.max()) if len(roll) else None
    worst12 = float(roll.min()) if len(roll) else None
    # % positive months
    monthly = curve.resample("ME").last().pct_change().dropna()
    pos_m = float((monthly > 0).mean()) if len(monthly) else None
    beta = None
    if bench_rets is not None:
        j = dr.to_frame("p").join(bench_rets.rename("b"), how="inner").dropna()
        if len(j) > 30 and j["b"].var():
            beta = float(j["p"].cov(j["b"]) / j["b"].var())
    pct = lambda v: None if v is None else round(v * 100, 1)
    return {
        "total_return": pct(total), "cagr": pct(cagr), "vol": pct(vol),
        "max_drawdown": pct(maxdd), "mar": None if mar is None else round(mar, 2),
        "sharpe": None if sharpe is None else round(sharpe, 2),
        "sortino": None if sortino is None else round(sortino, 2),
        "beta": None if beta is None else round(beta, 2),
        "best_12m": pct(best12), "worst_12m": pct(worst12),
        "pos_months": None if pos_m is None else round(pos_m * 100),
        "dd_days": longest,
    }


def _prices_us(tickers, bench, years):
    import yfinance as yf
    data = yf.download(tickers + [bench], period=f"{years}y", auto_adjust=True, progress=False)
    return data["Close"] if "Close" in data else data


def _prices_cn(tickers, bench, years):
    import pandas as pd
    from datetime import date, timedelta
    from . import cn_data
    start = (date.today() - timedelta(days=int(years * 365.25) + 10)).strftime("%Y%m%d")
    cols = {}
    for ts in tickers:
        rows = cn_data.call("daily", {"ts_code": ts, "start_date": start}, "ts_code,trade_date,close")
        adj = cn_data.call("adj_factor", {"ts_code": ts, "start_date": start}, "ts_code,trade_date,adj_factor")
        af = {a["trade_date"]: _to_float(a.get("adj_factor")) for a in adj}
        ser = {r["trade_date"]: _to_float(r.get("close")) * af.get(r["trade_date"], 1)
               for r in rows if _to_float(r.get("close")) and af.get(r["trade_date"])}
        if ser:
            cols[ts] = pd.Series(ser)
    bidx = cn_data.call("index_daily", {"ts_code": bench, "start_date": start}, "ts_code,trade_date,close")
    bser = {r["trade_date"]: _to_float(r.get("close")) for r in bidx if _to_float(r.get("close"))}
    if bser:
        cols[bench] = pd.Series(bser)
    df = pd.DataFrame(cols)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def backtest_portfolio(market: str, holdings: list[dict], years: int = 5) -> dict:
    import pandas as pd
    years = max(1, min(int(years or 5), 10))
    tickers = [(h.get("symbol") or "").strip().upper() for h in holdings if h.get("symbol")]
    weights = {(h.get("symbol") or "").strip().upper(): float(h.get("weight") or 0) for h in holdings}
    tickers = [t for t in dict.fromkeys(tickers) if t][:60]
    if len(tickers) < 1:
        return {"status": "error", "error": "No holdings to backtest."}
    bench = "000300.SH" if market == "cn" else "SPY"
    try:
        close = _prices_cn(tickers, bench, years) if market == "cn" else _prices_us(tickers, bench, years)
    except Exception as exc:
        return {"status": "error", "error": f"Price fetch failed: {exc}"}
    if close is None or close.empty:
        return {"status": "error", "error": "No price history available."}
    close = close.dropna(how="all")

    valid = [t for t in tickers if t in close.columns and close[t].notna().sum() > 60]
    excluded = [t for t in tickers if t not in valid]
    if not valid or bench not in close.columns:
        return {"status": "error", "error": "Not enough overlapping price history."}
    sub = close[valid + [bench]].dropna()             # common window (all present)
    if len(sub) < 60:
        return {"status": "error", "error": "Holdings' price histories barely overlap — window too short to measure."}

    wsum = sum(weights.get(t, 0) for t in valid)
    w = {t: (weights.get(t, 0) / wsum if wsum > 0 else 1 / len(valid)) for t in valid}
    norm = sub[valid] / sub[valid].iloc[0]
    port = (norm * pd.Series(w)).sum(axis=1)
    bench_curve = sub[bench] / sub[bench].iloc[0]
    bench_rets = sub[bench].pct_change().dropna()

    # correlation matrix of daily returns
    corr_block = None
    if len(valid) >= 2:
        cm = sub[valid].pct_change().dropna().corr()
        matrix = [[round(float(cm.loc[a, b]), 2) for b in valid] for a in valid]
        offs = [float(cm.loc[valid[i], valid[j]]) for i in range(len(valid)) for j in range(i + 1, len(valid))]
        corr_block = {"tickers": valid, "matrix": matrix,
                      "avg_corr": round(sum(offs) / len(offs), 2) if offs else None}

    step = max(1, len(port) // 260)
    return {
        "status": "ready", "market": market,
        "window": {"from": str(sub.index[0].date()), "to": str(sub.index[-1].date()),
                   "years": round((sub.index[-1] - sub.index[0]).days / 365.25, 1)},
        "n_holdings": len(valid), "excluded": excluded,
        "benchmark_name": "CSI 300" if market == "cn" else "SPY",
        "dates": [str(d.date()) for d in port.index[::step]],
        "portfolio": [round(float(v), 4) for v in port.iloc[::step]],
        "benchmark": [round(float(v), 4) for v in bench_curve.iloc[::step]],
        "portfolio_stats": _curve_stats(port, bench_rets),
        "benchmark_stats": _curve_stats(bench_curve),
        "correlation": corr_block,
    }


def asset_overlap(market: str, tickers: list[str]) -> dict:
    """Flag double-exposure: individual stocks that also sit inside held ETFs, and
    ETF-vs-ETF overlap on their top holdings. US only (yfinance funds_data)."""
    tickers = [(t or "").strip().upper() for t in tickers if t and t.strip()]
    tickers = list(dict.fromkeys(tickers))[:40]
    if market == "cn":
        return {"status": "ready", "note": "Overlap analysis is US-only for now.",
                "etfs": [], "stock_in_etf": [], "etf_pairs": []}
    if len(tickers) < 2:
        return {"status": "ready", "etfs": [], "stock_in_etf": [], "etf_pairs": []}

    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf

    def holdings(sym):
        for _ in range(2):   # funds_data is flaky; one retry markedly improves ETF detection
            try:
                th = yf.Ticker(sym).funds_data.top_holdings
                if th is not None and not th.empty and "Holding Percent" in th.columns:
                    return sym, {str(ix).upper(): round(float(v) * 100, 1)
                                 for ix, v in th["Holding Percent"].items() if v == v}
            except Exception:
                pass
        return sym, None

    hold_map = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, h in ex.map(holdings, tickers):
            if h:
                hold_map[sym] = h
    etfs = list(hold_map)
    stocks = [t for t in tickers if t not in hold_map]

    # (1) a stock you hold directly that also sits inside a held ETF
    stock_in_etf = []
    for etf, h in hold_map.items():
        for s in stocks:
            if s in h:
                stock_in_etf.append({"stock": s, "etf": etf, "weight_in_etf": h[s]})
    stock_in_etf.sort(key=lambda x: -x["weight_in_etf"])

    # (2) ETF ↔ ETF overlap on shared top holdings (approximate: top-10 only)
    etf_pairs = []
    for i in range(len(etfs)):
        for j in range(i + 1, len(etfs)):
            a, b = etfs[i], etfs[j]
            shared = set(hold_map[a]) & set(hold_map[b])
            if shared:
                ov = round(sum(min(hold_map[a][s], hold_map[b][s]) for s in shared), 1)
                etf_pairs.append({"a": a, "b": b, "shared": sorted(shared),
                                  "n_shared": len(shared), "overlap_weight": ov})
    etf_pairs.sort(key=lambda x: -x["overlap_weight"])
    return {"status": "ready", "etfs": etfs, "stocks_n": len(stocks),
            "stock_in_etf": stock_in_etf, "etf_pairs": etf_pairs,
            "note": "ETF holdings limited to each fund's top ~10 (yfinance) — overlap is a floor, not exhaustive."}

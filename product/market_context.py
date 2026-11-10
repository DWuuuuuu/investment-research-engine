"""Cross-asset market context — Weinstein stage analysis on major assets.

Weinstein's method is explicitly TOP-DOWN: judge the overall market first,
then the sector, then the stock. Until now this project's "market" step was a
single instrument (SPY for US, 中证全指 for CN) whose stage was computed but
whose actual moving averages were never shown. This module widens that step to
a small fixed set of major assets and surfaces the numbers.

Each instrument gets exactly the same treatment every stock already gets — the
weekly 30-period SMA and EMA, both slopes, the four-stage classification from
each, Mansfield RS vs SPY, and distance to 30-week resistance — by calling
stage_analysis._one() on a shared weekly date axis. Nothing here re-implements
an indicator; the arithmetic is the same code path the stock pages use, which
is what makes the readings comparable across the table.

INSTRUMENT CHOICES, and the one that is not obvious:

  Oil is quoted as CL=F, the WTI front-month FUTURES contract, because that is
  the price of oil. The investable ETFs are a different thing: they hold a
  rolling futures position, so their return includes roll yield. Over the
  trailing two years that gap was enormous — WTI front-month +15.3% versus USO
  +78.1% and BNO +76.1%, the market having been in backwardation — so the two
  can sit in DIFFERENT Weinstein stages at the same time. Treat this row as a
  macro read on the commodity, NOT as a proxy for what an oil ETF would have
  done. The front-month series also carries roll discontinuities at contract
  changeover, which a 30-week average smooths but does not remove.

  Bitcoin is BTC-USD, the asset itself, rather than a spot ETF: it has the
  longer history and the two track each other closely (+14.3% vs IBIT +13.6%
  over the same window). Note it trades 7 days a week, so its weekly bar spans
  more calendar time than the equity bars beside it.

CAVEATS (stated, not hidden):
  - Mansfield RS is measured against SPY for every row, so the SPY row's own
    RS is definitionally zero and is nulled rather than shown as a reading.
    For non-equity assets RS vs SPY is a cross-asset relative-strength read,
    which is a coherent question but not the one Weinstein was asking (he
    compared a stock to its own market).
  - Prices are yfinance auto_adjust=True (split- and dividend-adjusted), so
    TLT's and GLD's distributions are handled; the equity ETFs are therefore
    TOTAL-RETURN series, consistent with the benchmark used elsewhere.
  - The trailing weekly bar is INCOMPLETE (US weekly bars are labelled by week
    start). Price uses it; volume excludes it, since volume accumulates
    through the week and price does not. Same rule as the stock cache.
  - Volume for a futures contract and for a 24/7 crypto pair is not really
    comparable to ETF share volume; the volume ratio is shown but should not
    be read across rows.
  - Stage 1 vs 3 disambiguation uses the prior-leg slope, an explicit
    inference and not a formula from the book.
  - No backtest has been run.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
_CACHE = _DATA / "market_context.json"
# Short TTL on purpose: the whole build is ONE batched download of 7 tickers
# (~2.5s), and two of them (BTC-USD, CL=F) trade nearly 24/7, so a long TTL
# would show a materially stale price — BTC was observed +4.6% adrift after
# a few hours. The 30-week stage read is unaffected, but the printed price is.
_TTL = 3600
_WEEKS = 70                  # same depth as the stock cache (Mansfield 52 + 13)
_BENCH = "SPY"

# (ticker, display name, asset class). SPY first — it is the RS benchmark.
_INSTRUMENTS: list[tuple[str, str, str]] = [
    ("SPY",     "S&P 500",              "US equity"),
    ("QQQ",     "Nasdaq 100",           "US equity"),
    ("IWM",     "Russell 2000",         "US equity"),
    ("GLD",     "Gold",                 "Commodity"),
    ("CL=F",    "Oil — WTI front-month", "Commodity"),
    ("TLT",     "Treasuries 20y+",      "Bonds"),
    ("BTC-USD", "Bitcoin",              "Crypto"),
]

_state = {"building": False, "ts": 0.0, "error": None}
_lock = threading.Lock()


def _build() -> dict:
    import yfinance as yf
    from . import stage_analysis as SA

    tickers = [t for t, _, _ in _INSTRUMENTS]
    df = yf.download(tickers, period="2y", interval="1wk", auto_adjust=True,
                     progress=False, threads=True, group_by="column")
    if df is None or df.empty:
        raise RuntimeError("no weekly data returned")
    close, vol = df["Close"], df.get("Volume")
    close = close.tail(_WEEKS)
    if vol is not None:
        vol = vol.tail(_WEEKS)

    dates = [d.strftime("%Y%m%d") for d in close.index]
    partial = SA.is_partial_last_bar("us", dates[-1])

    def col(frame, t):
        if frame is None or t not in frame.columns:
            return [None] * len(dates)
        return [None if v != v else float(v) for v in frame[t].tolist()]

    bench = col(close, _BENCH)
    rows = []
    for t, name, cls in _INSTRUMENTS:
        closes = col(close, t)
        if not any(c is not None for c in closes):
            continue                       # per-instrument isolation
        try:
            v = SA._one(closes, col(vol, t), bench, partial)
        except Exception:                  # noqa: BLE001
            continue
        if t == _BENCH:
            # RS against itself is definitionally zero — not a reading.
            v["mansfield_rs"] = None
        v.update({"ticker": t, "name": name, "asset_class": cls})
        rows.append(v)

    # Rank by Mansfield RS so the table reads as a leadership ordering, which
    # is how Weinstein used relative strength. SPY (null RS) sorts last.
    ranked = sorted([r for r in rows if r.get("mansfield_rs") is not None],
                    key=lambda r: -r["mansfield_rs"])
    for i, r in enumerate(ranked, 1):
        r["rs_rank"] = i
        r["rs_rank_of"] = len(ranked)

    return {
        "status": "ready",
        "as_of": dates[-1],
        "weeks": len(dates),
        "partial_last_bar": partial,
        "benchmark": _BENCH,
        "ma_period": 30,
        "rows": rows,
        "built_at": time.time(),
    }


def _build_bg() -> None:
    try:
        payload = _build()
        _DATA.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(payload))
        _state["ts"] = time.time()
        _state["error"] = None
    except Exception as exc:               # noqa: BLE001
        _state["error"] = str(exc)
    finally:
        _state["building"] = False


def ensure(background: bool = True) -> None:
    fresh = _CACHE.exists() and (time.time() - _CACHE.stat().st_mtime) < _TTL
    with _lock:
        if fresh or _state["building"]:
            return
        _state["building"] = True
    if background:
        threading.Thread(target=_build_bg, daemon=True).start()
    else:
        _build_bg()


def get_market_context() -> dict:
    """Cross-asset stage table. Blocks only on a cold cache (~3s, 7 tickers in
    one batched download); refreshes in the background thereafter."""
    if not _CACHE.exists():
        ensure(background=False)           # cold start: one short synchronous build
    else:
        ensure(background=True)
    if not _CACHE.exists():
        return {"status": "error", "error": _state["error"] or "unavailable"}
    try:
        payload = json.loads(_CACHE.read_text())
    except Exception as exc:               # noqa: BLE001
        return {"status": "error", "error": str(exc)}
    payload["stale_error"] = _state["error"]
    return payload


# ── Market Index panel ───────────────────────────────────────────────────────
# Daily OHLCV for a single index/ETF so the Macro page can drive the SAME chart
# engine the stock pages use (candles, configurable MAs, volume, relative
# strength, oscillators). Nothing here re-implements an indicator — it only
# supplies the series.
# 15 minutes, not hours. A long TTL let instruments drift out of sync with each
# other: QQQ was observed serving Friday's last bar while TLT, fetched minutes
# earlier in the same panel, already had Monday's. Every closed bar agreed — the
# caches simply straddled a session boundary — but two instruments in one panel
# being as-of different DATES is misleading when you are comparing them.
_IDX_TTL = 15 * 60
_IDX_CACHE_DIR = _DATA / "index_history"

# (ticker, display name, group). Two of these are INDICES rather than tradable
# funds and therefore carry no volume — flagged so the chart can say so instead
# of drawing an empty volume panel and leaving you to wonder.
INDEX_INSTRUMENTS: list[dict] = [
    {"t": "SPY",      "name": "S&P 500 (SPY)",        "grp": "US equity"},
    {"t": "QQQ",      "name": "Nasdaq 100 (QQQ)",     "grp": "US equity"},
    {"t": "^IXIC",    "name": "Nasdaq Composite",     "grp": "US equity", "no_volume": False},
    {"t": "DIA",      "name": "Dow Jones (DIA)",      "grp": "US equity"},
    {"t": "IWM",      "name": "Russell 2000 (IWM)",   "grp": "US equity"},
    {"t": "EEM",      "name": "Emerging Markets",     "grp": "Global equity"},
    {"t": "BTC-USD",  "name": "Bitcoin",              "grp": "Crypto"},
    {"t": "ETH-USD",  "name": "Ethereum",             "grp": "Crypto"},
    {"t": "GLD",      "name": "Gold (GLD)",           "grp": "Commodity"},
    {"t": "SLV",      "name": "Silver (SLV)",         "grp": "Commodity"},
    {"t": "CL=F",     "name": "Oil — WTI front-month", "grp": "Commodity"},
    {"t": "TLT",      "name": "Treasuries 20y+",      "grp": "Bonds"},
    {"t": "^VIX",     "name": "VIX (volatility)",     "grp": "Volatility", "no_volume": True},
    {"t": "DX-Y.NYB", "name": "US Dollar Index",      "grp": "FX", "no_volume": True},
]
_IDX_BY_T = {i["t"]: i for i in INDEX_INSTRUMENTS}


def _idx_slug(sym: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]", "_", sym) + ".json"


def index_history(symbol: str, years: int = 5) -> dict:
    """Daily OHLCV for one instrument, plus the SPY series so the chart's
    relative-strength panel has a benchmark. Cached per symbol."""
    import json as _json
    import time as _time
    meta = _IDX_BY_T.get(symbol)
    if not meta:
        return {"status": "error", "error": f"unknown instrument {symbol!r}"}
    _IDX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _IDX_CACHE_DIR / _idx_slug(symbol)
    if cache.exists() and (_time.time() - cache.stat().st_mtime) < _IDX_TTL:
        try:
            return _json.loads(cache.read_text())
        except Exception:                          # noqa: BLE001
            pass

    import yfinance as yf
    tickers = [symbol] if symbol == _BENCH else [symbol, _BENCH]
    try:
        df = yf.download(tickers, period=f"{years}y", interval="1d", auto_adjust=True,
                         progress=False, threads=True, group_by="column")
    except Exception as exc:                       # noqa: BLE001
        return {"status": "error", "error": str(exc)}
    if df is None or df.empty:
        return {"status": "error", "error": f"no data for {symbol}"}

    def col(field, t):
        try:
            d = df[field]
        except Exception:                          # noqa: BLE001
            return None
        if hasattr(d, "columns"):
            return d[t] if t in d.columns else None
        return d

    close = col("Close", symbol)
    if close is None or close.dropna().empty:
        return {"status": "error", "error": f"no closes for {symbol}"}
    o, hi, lo = col("Open", symbol), col("High", symbol), col("Low", symbol)
    vol = col("Volume", symbol)
    bench = col("Close", _BENCH) if symbol != _BENCH else close

    def f(series, i):
        if series is None:
            return None
        try:
            v = series.iloc[i]
        except Exception:                          # noqa: BLE001
            return None
        return None if v != v else round(float(v), 4)

    rows, bench_map = [], {}
    for i, ts in enumerate(close.index):
        c = f(close, i)
        if c is None:
            continue                               # gaps skipped, never filled
        d = ts.strftime("%Y-%m-%d")
        rows.append({"d": d, "o": f(o, i), "h": f(hi, i), "l": f(lo, i),
                     # Volume stays None when absent.  It was `or 0`, which is
                     # actively harmful anywhere a rule compares V_t to V_{t-1}:
                     # a zero makes the comparison false and SUPPRESSES the
                     # signal, so a data outage reads as market calm rather than
                     # as missing data.  Consumers must handle null.
                     "c": c, "v": f(vol, i)})
        b = f(bench, i)
        if b is not None:
            bench_map[d] = b

    out = {
        "status": "ready", "symbol": symbol, "name": meta["name"], "group": meta["grp"],
        "no_volume": bool(meta.get("no_volume")),
        "benchmark_symbol": _BENCH, "benchmark_name": "S&P 500 (SPY)",
        "is_benchmark": symbol == _BENCH,
        "prices": rows,
        "benchmark": [{"d": k, "c": v} for k, v in bench_map.items()],
        "built_at": _time.time(),
    }
    try:
        cache.write_text(_json.dumps(out))
    except Exception:                              # noqa: BLE001
        pass
    return out

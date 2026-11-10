"""Weekly OHLCV history cache — foundation for Stan Weinstein Stage Analysis.

Phase 1 of the Stage Analysis build (2026-08-15). This module ONLY fetches and
stores weekly closes + volumes; every indicator (30-week SMA/EMA, slope,
Mansfield RS, volume ratio, base resistance, stage label) is computed downstream
in Phase 2 from this cache. Keeping the store indicator-agnostic means adding a
new moving-average variant (e.g. the EMA30 the user asked for alongside
Weinstein's canonical SMA30) costs nothing here.

WHY WEEKLY. Weinstein's method is explicitly a weekly-chart method — the
30-week moving average, the 52-week Mansfield RS lookback, and the
weekly-close breakout rule all live on a weekly cadence. Storing weekly bars
rather than daily keeps the cache ~5x smaller and matches the method's own
resolution.

VERIFIED DATA PATHS (checked live 2026-08-15 before writing any of this):
  CN  `weekly` supports whole-market bulk-by-date — ONE call returns 5,613
      stocks with close+vol for that week. So N weeks costs N calls total,
      not N-per-stock. Weekly date axis comes free from `index_weekly`.
  US  `yf.download(list_of_tickers, interval='1wk')` batches — 200 tickers x
      105 weekly bars in ~12s, including Volume. This is a DIFFERENT yfinance
      endpoint from the per-ticker `.financials`/`.quarterly_financials` path
      that repeatedly hit Yahoo's rate limiter earlier in this project
      (missing-ticker rate swung 6.2% -> 30% there). Batched download did not
      show that behaviour in testing, but it has not been stress-tested at
      full 5,757-ticker scale — treat a coverage regression as the first
      thing to check if US stage data looks thin.

BENCHMARK INDEX (needed for Mansfield RS in Phase 2):
  CN  000985.SH — 中证全指 (all-share). NOTE: the commonly-cited code
      `000985.CSI` returns ZERO rows from Tushare's index_weekly; it silently
      yields no data rather than erroring. Verified 000985.SH works (84 weekly
      bars over 2025-01..2026-08). Chosen over CSI300 per user decision: a
      broad all-share benchmark is closer to Weinstein's "vs the broad market"
      intent and fairer to the small/mid caps that dominate the A-share count.
  US  ^GSPC (S&P 500) — the standard Mansfield RS benchmark.

STORAGE SHAPE. A shared date axis with per-stock arrays aligned to it, rather
than per-stock date lists: weekly trading dates are near-identical across the
market, so a shared axis is far more compact AND makes the stock-vs-index
alignment that Mansfield RS needs trivial (same index into both arrays).
Missing weeks (suspension, halt, pre-IPO) are stored as null and are never
interpolated — same discipline as the cumulative-YTD differencing elsewhere in
this codebase: a gap is skipped, never inferred across.

SURVIVORSHIP. This cache holds CURRENT constituents only — a name delisted
last year is absent entirely. That is correct for a live screen (you can only
buy what's listed) but makes the cache survivorship-biased if anyone later
tries to backtest off it. Flagged, not hidden.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 70 weeks covers every Phase-2 indicator with room to spare:
#   30-week SMA/EMA .................. 30 weeks
#   ...its slope over ~13 weeks ...... 43 weeks
#   Mansfield RS (52-week SMA of RP) . 52 weeks
#   ...its slope over ~13 weeks ...... 65 weeks
# Anything beyond 70 is dead weight in the cache.
_WEEKS = 70

_CN_INDEX = "000985.SH"      # 中证全指 all-share — see docstring on the .CSI trap
# SPY, not ^GSPC. Bug found 2026-08-20: our stock closes come from
# yf.download(auto_adjust=True), i.e. DIVIDEND-ADJUSTED total return, while
# ^GSPC is a PRICE-ONLY index (an index has no dividends to reinvest).
# Comparing a total-return stock against a price-only benchmark systematically
# flatters every stock's Mansfield RS by roughly the index dividend yield —
# measured as a near-constant +0.5pp across NVDA/MSFT/TDG/UBER/TWLO, and SPY
# itself returned +40.05% adjusted vs +36.81% unadjusted over the window.
# SPY (auto_adjust=True) is total return on both sides, so the comparison is
# like-for-like. It also matches the chart's RS panel, so the detail page no
# longer shows two different benchmarks.
# CN needs no equivalent change: Tushare `weekly` closes and the 中证全指 index
# are both unadjusted price series, so that pair is already consistent.
_US_INDEX = "SPY"
_US_BATCH = 200              # verified: 200 tickers/~12s via yf.download

_TTL = 24 * 3600             # the in-progress week's bar keeps moving, so refresh daily

_CACHE_FILE = {
    "cn": _DATA_DIR / "price_history_cn.json",
    "us": _DATA_DIR / "price_history_us.json",
}
# Mid-build checkpoints land here, never on the live cache above.
_PARTIAL_FILE = {m: f.with_suffix(".partial.json") for m, f in _CACHE_FILE.items()}

# Each market's cache: {"dates": [...], "index": [...], "stocks": {sym: {"c": [...], "v": [...]}}}
# with every per-stock array aligned index-for-index to "dates".
_STATE: dict[str, dict[str, Any]] = {
    m: {"ts": 0.0, "dates": [], "index": [], "stocks": {}, "groups": {}, "building": False,
        "progress": 0, "total": 0, "error": None, "failed": 0, "disk_checked": False}
    for m in ("cn", "us")
}


# ── disk persistence ──────────────────────────────────────────────────────────

def _load_from_disk(market: str) -> None:
    st = _STATE[market]
    try:
        f = _CACHE_FILE[market]
        if f.exists():
            data = json.loads(f.read_text())
            if isinstance(data, dict) and isinstance(data.get("stocks"), dict):
                st["dates"] = data.get("dates") or []
                st["index"] = data.get("index") or []
                st["stocks"] = data["stocks"]
                st["groups"] = data.get("groups") or {}
                st["ts"] = float(data.get("ts") or 0)
    except Exception:
        pass   # missing/corrupt file just means "rebuild", same as a cold start


def _save_to_disk(market: str, final: bool = True) -> None:
    """Persist the cache. Mid-build CHECKPOINTS must never land on the live
    path: a rebuild that dies partway (e.g. a daemon thread going down with its
    parent process) would otherwise leave the live cache holding only the
    batches completed so far. That is exactly what happened here — the US
    weekly cache was found truncated to 2,282 of ~5,500 symbols, silently
    shrinking the universe behind Stage Analysis, the stage filters, sector
    stages and top-down. Checkpoints go to a .partial sibling and are promoted
    only once the build finishes."""
    st = _STATE[market]
    target = _CACHE_FILE[market] if final else _PARTIAL_FILE[market]
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "ts": st["ts"], "dates": st["dates"], "index": st["index"], "stocks": st["stocks"],
            "groups": st.get("groups") or {},
        }))
        if final:
            _PARTIAL_FILE[market].unlink(missing_ok=True)
    except Exception:
        pass   # still usable in-memory for this process even if the write fails


def _round_close(v: Any) -> float | None:
    try:
        f = float(v)
        return None if f != f else round(f, 3)   # f != f catches NaN
    except (TypeError, ValueError):
        return None


def _round_vol(v: Any) -> float | None:
    try:
        f = float(v)
        return None if f != f else round(f)
    except (TypeError, ValueError):
        return None


# ── CN build ──────────────────────────────────────────────────────────────────

def _build_cn() -> None:
    """One `index_weekly` call for the date axis + benchmark, then one bulk
    `weekly` call per week for the whole market. ~71 calls total."""
    from . import cn_data
    st = _STATE["cn"]
    try:
        start = (date.today() - timedelta(weeks=_WEEKS + 6)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")

        # Suffix convention differs per Tushare endpoint and both variants
        # fail silently with zero rows (index_weekly wants .SH, index_daily
        # wants .CSI — verified 2026-08-20). Try each rather than assume.
        idx_rows = []
        for _code in (_CN_INDEX, _CN_INDEX.split(".")[0] + ".CSI", _CN_INDEX.split(".")[0] + ".SH"):
            idx_rows = cn_data.call(
                "index_weekly", {"ts_code": _code, "start_date": start, "end_date": end},
                "ts_code,trade_date,close",
            )
            if idx_rows:
                break
        if not idx_rows:
            # The 000985.CSI-returns-nothing trap: fail loudly rather than
            # building a whole cache with no benchmark (which would silently
            # make Mansfield RS uncomputable for every stock in Phase 2).
            st["error"] = f"index_weekly returned no rows for {_CN_INDEX}"
            return
        idx_dedup: dict[str, float] = {}
        for r in idx_rows:
            d, c = r.get("trade_date"), _round_close(r.get("close"))
            if d and c is not None:
                idx_dedup[d] = c
        dates = sorted(idx_dedup)[-_WEEKS:]
        n = len(dates)
        st["dates"] = dates
        st["index"] = [idx_dedup[d] for d in dates]
        st["total"] = n
        st["progress"] = 0
        st["failed"] = 0

        # Industry map for Phase-4 group stages, stored WITH the cache so the
        # stock-detail page can show top-down context without first forcing a
        # whole-universe snapshot build (which it has no other reason to do).
        try:
            st["groups"] = {b["ts_code"]: (b.get("industry") or "").strip()
                            for b in cn_data._stock_basic_list() if b.get("industry")}
        except Exception:
            st["groups"] = {}

        stocks: dict[str, dict[str, list]] = {}
        for i, d in enumerate(dates):
            try:
                rows = cn_data.call("weekly", {"trade_date": d}, "ts_code,close,vol")
            except Exception:
                # Per-week isolation: one bad week must not discard the other
                # 69 (the lesson from the quarterly-fetch regression, where an
                # unguarded inner call threw away an already-good outer result).
                st["failed"] += 1
                st["progress"] += 1
                continue
            for r in rows:
                ts_code = r.get("ts_code")
                if not ts_code:
                    continue
                slot = stocks.get(ts_code)
                if slot is None:
                    slot = {"c": [None] * n, "v": [None] * n}
                    stocks[ts_code] = slot
                slot["c"][i] = _round_close(r.get("close"))
                slot["v"][i] = _round_vol(r.get("vol"))
            st["progress"] += 1
            # Periodic checkpoint so a late failure doesn't cost the whole run.
            if (i + 1) % 20 == 0:
                st["stocks"] = stocks
                st["ts"] = time.time()
                _save_to_disk("cn", final=False)

        # Gap repair. A failed bulk call leaves that week null for the ENTIRE
        # market (not just one stock), which would silently shrink any 30-week
        # SMA window containing it — 2 of 70 weeks failed on the first live
        # build (20260605, 20260612, consecutive: a transient API hiccup), so
        # this is a recurring failure mode worth healing rather than tolerating.
        # Only fully-empty weeks are retried; a partially-filled week is real
        # market data (suspensions), not a fetch failure.
        empty = [i for i in range(n) if not any(
            stocks[s]["c"][i] is not None for s in stocks)] if stocks else []
        for i in empty:
            try:
                rows = cn_data.call("weekly", {"trade_date": dates[i]}, "ts_code,close,vol")
            except Exception:
                continue
            for r in rows:
                ts_code = r.get("ts_code")
                slot = stocks.get(ts_code) if ts_code else None
                if slot is None:
                    continue
                slot["c"][i] = _round_close(r.get("close"))
                slot["v"][i] = _round_vol(r.get("vol"))
            if rows:
                st["failed"] = max(0, st["failed"] - 1)

        st["stocks"] = stocks
        st["ts"] = time.time()
        _save_to_disk("cn")
        st["error"] = None
    except Exception as exc:                       # noqa: BLE001
        st["error"] = str(exc)
    finally:
        st["building"] = False


# ── US build ──────────────────────────────────────────────────────────────────

def _compact(ts: Any) -> str:
    """pandas Timestamp -> 'YYYYMMDD', matching CN's date format."""
    return str(ts)[:10].replace("-", "")


def _build_us() -> None:
    """Batched `yf.download` — ~29 calls of 200 tickers each for the full
    universe, plus one for the benchmark."""
    import yfinance as yf
    from .filter_screener import _US, ensure_us_snapshot
    st = _STATE["us"]
    try:
        ensure_us_snapshot()
        for _ in range(30):
            if _US["rows"] and _US["meta_ready"]:
                break
            time.sleep(2)
        symbols = [r["symbol"] for r in _US["rows"]]
        st["groups"] = {r["symbol"]: (r.get("industry") or "").strip()
                        for r in _US["rows"] if r.get("industry")}   # see CN twin
        if not symbols:
            st["error"] = "US snapshot unavailable — cannot build price history"
            return

        idx_df = yf.download(_US_INDEX, period="2y", interval="1wk",
                             progress=False, auto_adjust=True)
        if idx_df is None or idx_df.empty:
            st["error"] = f"no benchmark history for {_US_INDEX}"
            return
        idx_close = idx_df["Close"]
        if hasattr(idx_close, "columns"):        # single-ticker download can still nest
            idx_close = idx_close.iloc[:, 0]
        idx_close = idx_close.dropna()
        axis = list(idx_close.index)[-_WEEKS:]
        dates = [_compact(t) for t in axis]
        n = len(dates)
        st["dates"] = dates
        st["index"] = [_round_close(idx_close.loc[t]) for t in axis]
        st["total"] = len(symbols)
        st["progress"] = 0
        st["failed"] = 0

        stocks: dict[str, dict[str, list]] = {}
        for b0 in range(0, len(symbols), _US_BATCH):
            batch = symbols[b0:b0 + _US_BATCH]
            try:
                df = yf.download(batch, period="2y", interval="1wk",
                                 progress=False, auto_adjust=True)
                if df is None or df.empty:
                    raise ValueError("empty frame")
                close, vol = df["Close"], df["Volume"]
                if not hasattr(close, "columns"):   # lone-ticker batch -> Series
                    close, vol = close.to_frame(batch[0]), vol.to_frame(batch[0])
                # Reindex onto the benchmark's axis so every stock aligns
                # index-for-index with the index series (what Mansfield RS needs).
                close, vol = close.reindex(axis), vol.reindex(axis)
                for sym in batch:
                    if sym not in close.columns:
                        continue
                    cs = [_round_close(v) for v in close[sym].tolist()]
                    vs = [_round_vol(v) for v in vol[sym].tolist()] if sym in vol.columns else [None] * n
                    if sum(1 for c in cs if c is not None) < 30:
                        continue   # too little history to be usable for a 30-week MA
                    stocks[sym] = {"c": cs, "v": vs}
            except Exception:
                st["failed"] += len(batch)         # per-batch isolation, same rule as CN
            st["progress"] += len(batch)
            st["stocks"] = stocks
            st["ts"] = time.time()
            _save_to_disk("us", final=False)       # checkpoint (partial file, not the live cache)

        st["stocks"] = stocks
        st["ts"] = time.time()
        _save_to_disk("us")
        st["error"] = None
    except Exception as exc:                       # noqa: BLE001
        st["error"] = str(exc)
    finally:
        st["building"] = False


# ── public API ────────────────────────────────────────────────────────────────

def ensure_price_history(market: str) -> None:
    st = _STATE.get(market)
    if st is None or st["building"]:
        return
    if not st["disk_checked"]:
        st["disk_checked"] = True
        if not st["stocks"]:
            _load_from_disk(market)
    if st["stocks"] and (time.time() - st["ts"] < _TTL):
        return
    st["building"] = True
    threading.Thread(target=_build_cn if market == "cn" else _build_us, daemon=True).start()


def price_history_status(market: str) -> dict:
    _ensure_loaded(market)
    st = _STATE.get(market) or {}
    return {
        "building": st.get("building", False),
        "progress": st.get("progress", 0),
        "total": st.get("total", 0),
        "failed": st.get("failed", 0),
        "ready": bool(st.get("stocks")),
        "built_at": st.get("ts", 0.0),
        "weeks": len(st.get("dates") or []),
        "stocks": len(st.get("stocks") or {}),
        "benchmark": _CN_INDEX if market == "cn" else _US_INDEX,
        "error": st.get("error"),
    }


def _ensure_loaded(market: str) -> None:
    """Lazy disk load on READ. Without this a fresh process returns an empty
    history to any consumer that hasn't first called ensure_price_history() —
    which silently yields zero stocks rather than an error (hit live
    2026-08-19: build_stage_map returned {} on a cold process). Reading must
    never depend on someone else having warmed the cache first."""
    st = _STATE.get(market)
    if st is None or st["stocks"] or st["disk_checked"]:
        return
    st["disk_checked"] = True
    _load_from_disk(market)


def get_history(market: str) -> dict:
    """Raw cache for Phase-2 indicator computation: shared `dates` axis,
    benchmark `index` closes aligned to it, and per-stock `c`/`v` arrays also
    aligned to it (None where a week is missing — never interpolated)."""
    _ensure_loaded(market)
    st = _STATE.get(market) or {}
    return {"dates": st.get("dates") or [], "index": st.get("index") or [],
            "stocks": st.get("stocks") or {}, "groups": st.get("groups") or {},
            "built_at": st.get("ts", 0.0)}

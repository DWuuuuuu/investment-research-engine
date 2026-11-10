"""US market breadth — Weinstein's Momentum Index (MI).

Companion to cn_breadth.py; see that module's header for the full reading of
Chapter 8 ("Using the Best Long Term Indicators to Spot Bull and Bear Markets")
and for why the MI (200-day moving average of daily net advances − declines,
read on its ZERO-LINE CROSSING) is built here while the CUMULATIVE A/D Line
deliberately is not. The reasoning is identical and is not repeated.

UNIVERSE — the one decision that differs from CN, and the one most worth
stating plainly.

Weinstein used ALL NYSE issues. The whole diagnostic value of a breadth
measure is that it can disagree with the cap-weighted index: the broad list
rolls over while a handful of mega-caps keep the index up. Restricting the
universe to a large-cap index's own members largely destroys that property —
the breadth universe and the index become nearly the same names, so the
divergence the indicator exists to detect is structurally suppressed.

So the DEFAULT scope here is `all`: every US-listed common stock in the
NASDAQ-screener universe (~5,700 names), the same universe price_history.py
uses for the weekly Stage cache.

A second scope, `largecap`, is computed from the same download at no extra
cost: the 500 largest US common stocks BY TODAY'S MARKET CAP, held fixed
across the whole window. It is offered because large-cap-only breadth is a
genuinely different and useful read, not because it is more correct.

  It is NOT the S&P 500. Real S&P 500 membership is a committee decision with
  its own history, which this project has no point-in-time source for. Calling
  a top-500-by-cap screen "the S&P 500" would be wrong, so it is not called
  that anywhere in the UI.

CAVEATS (stated, not hidden):
  - SURVIVORSHIP. Both scopes hold TODAY's constituents fixed across the
    window: names delisted during the period are absent, so their declines
    are never counted and breadth reads better than it truly was. This bites
    the `largecap` scope harder — "in the top 500 by cap today" is a much
    stronger selection filter than "listed today". The MI is a 200-day moving
    average, so the distortion cannot ACCUMULATE the way it would in a
    cumulative A/D Line, but it is present in every reading.
  - Prices are yfinance `auto_adjust=True` (split- and dividend-adjusted), so
    ex-dividend days are not miscounted as declines.
  - A stock's first bar, and any bar following a data gap, has no valid prior
    close and is COUNTED IN NEITHER column — gaps are skipped, never filled.
  - The latest session may still be INTRADAY if read before the US close; the
    payload flags this. One partial session barely moves a 200-day average,
    but the "latest session" tile would be provisional.
  - The divergence test is a MECHANISATION of a rule the book states
    qualitatively. Its window and thresholds are choices, not values from
    Weinstein.
  - No backtest has been run.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
_CACHE = _DATA / "us_breadth_history.csv"
_PARTIAL = _DATA / "us_breadth_partial.json"
_TTL = 20 * 3600            # ~daily refresh, same cadence as the CN build
_YEARS_BACK = 2             # ~500 sessions -> ~300 days of MI after warm-up
_MI_WIN = 200               # Weinstein's 200-day moving average
_DIV_WIN = 60               # divergence lookback (unpinned choice)
_DIV_NEAR = 1.0             # index within this % of its window high/low (unpinned)
_DIV_GAP = 15.0             # MI this % off its own extreme to diverge (unpinned)

_US_INDEX = "SPY"           # total-return benchmark, matching price_history.py
_BATCH = 200                # verified: 200 tickers/~26s via yf.download (daily)
_LARGECAP_N = 500

_SCOPES = ("all", "largecap")

_state = {"building": False, "ts": 0.0, "error": None,
          "progress": 0, "total": 0, "failed": 0}


def _index_daily() -> dict[str, float]:
    """SPY daily closes, keyed YYYYMMDD. Same benchmark as the Stage cache."""
    import yfinance as yf
    try:
        df = yf.download(_US_INDEX, period=f"{_YEARS_BACK}y", interval="1d",
                         auto_adjust=True, progress=False, threads=False)
    except Exception:                              # noqa: BLE001
        return {}
    if df is None or df.empty:
        return {}
    close = df["Close"]
    if hasattr(close, "columns"):                  # yfinance may return a frame
        close = close.iloc[:, 0]
    return {d.strftime("%Y%m%d"): float(v)
            for d, v in close.items() if v == v}   # v==v drops NaN


def _universe() -> tuple[list[str], set[str]]:
    """All US common stocks, plus the fixed top-N-by-market-cap subset."""
    from .filter_screener import _fetch_nasdaq_universe
    rows = _fetch_nasdaq_universe()
    syms = [r["symbol"] for r in rows if r.get("symbol")]
    capped = [r for r in rows if r.get("market_cap")]
    capped.sort(key=lambda r: -r["market_cap"])
    large = {r["symbol"] for r in capped[:_LARGECAP_N]}
    return syms, large


def _count_batch(batch: list[str], large: set[str]) -> dict[str, list[int]]:
    """Advance/decline counts for one batch of tickers, per session.

    Returns date -> [adv, dec, unch, lc_adv, lc_dec, lc_unch]. Vectorised: the
    close-to-close diff is NaN on a symbol's first bar and after any gap, and
    NaN compares false in every direction, so those bars fall out of all three
    counts without being interpolated.
    """
    import yfinance as yf
    df = yf.download(batch, period=f"{_YEARS_BACK}y", interval="1d",
                     auto_adjust=True, progress=False, threads=True,
                     group_by="column")
    if df is None or df.empty:
        return {}
    close = df["Close"]
    if not hasattr(close, "columns"):              # single-ticker batch
        close = close.to_frame(batch[0])
    diff = close.diff()
    lc_cols = [c for c in close.columns if c in large]
    lc = diff[lc_cols] if lc_cols else None

    adv, dec, unch = (diff > 0).sum(axis=1), (diff < 0).sum(axis=1), (diff == 0).sum(axis=1)
    if lc is not None:
        la, ld, lu = (lc > 0).sum(axis=1), (lc < 0).sum(axis=1), (lc == 0).sum(axis=1)
    out: dict[str, list[int]] = {}
    for d in close.index:
        k = d.strftime("%Y%m%d")
        out[k] = [int(adv[d]), int(dec[d]), int(unch[d]),
                  int(la[d]) if lc is not None else 0,
                  int(ld[d]) if lc is not None else 0,
                  int(lu[d]) if lc is not None else 0]
    return out


def _load_partial() -> dict:
    if not _PARTIAL.exists():
        return {}
    try:
        return json.loads(_PARTIAL.read_text())
    except Exception:                              # noqa: BLE001
        return {}


def _build_history() -> None:
    """Batched over SYMBOLS (not dates), so counts are only final once every
    batch has landed. Each batch is therefore checkpointed to a PARTIAL file
    with the list of batches already folded in — a crash or restart resumes
    instead of discarding the run, the lesson from the report_rc build."""
    import pandas as pd
    try:
        syms, large = _universe()
        prior = _load_partial()
        # Resume only if the universe still matches; otherwise start clean.
        if prior.get("n_syms") == len(syms) and prior.get("acc"):
            acc = {k: list(v) for k, v in prior["acc"].items()}
            done = set(prior.get("done", []))
            large = set(prior.get("large") or large)
        else:
            acc, done = {}, set()

        batches = [syms[i:i + _BATCH] for i in range(0, len(syms), _BATCH)]
        _state["total"] = len(batches)
        _state["progress"] = len(done)
        _state["failed"] = 0

        for bi, batch in enumerate(batches):
            if bi in done:
                continue
            try:
                got = _count_batch(batch, large)
            except Exception:                      # noqa: BLE001
                _state["failed"] += 1              # per-batch isolation
                _state["progress"] += 1
                continue
            for k, v in got.items():
                cur = acc.setdefault(k, [0, 0, 0, 0, 0, 0])
                for j in range(6):
                    cur[j] += v[j]
            done.add(bi)
            _state["progress"] += 1
            _DATA.mkdir(parents=True, exist_ok=True)
            _PARTIAL.write_text(json.dumps(
                {"n_syms": len(syms), "done": sorted(done),
                 "large": sorted(large), "acc": acc}))

        idx = _index_daily()
        rows = []
        for k in sorted(acc):
            a, d, u, la, ld, lu = acc[k]
            n, ln = a + d + u, la + ld + lu
            # The window's FIRST session has no prior close for any symbol, so
            # nothing is counted. That is an absence of data, not a net-zero
            # breadth day — writing it would feed a spurious 0 into the MI.
            if n == 0:
                continue
            rows.append({
                "date": k, "adv": a, "dec": d, "unch": u, "net": a - d, "n": n,
                "adv_pct": round(a / n * 100, 3) if n else None,
                "lc_adv": la, "lc_dec": ld, "lc_unch": lu, "lc_net": la - ld,
                "lc_n": ln, "lc_adv_pct": round(la / ln * 100, 3) if ln else None,
                "index_close": idx.get(k),
            })
        if rows:
            _DATA.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).set_index("date").to_csv(_CACHE)
            _PARTIAL.unlink(missing_ok=True)       # complete: drop the resume file
        _state["ts"] = time.time()
        _state["error"] = None
    except Exception as exc:                       # noqa: BLE001
        _state["error"] = str(exc)
    finally:
        _state["building"] = False


def ensure(background: bool = True) -> None:
    fresh = _CACHE.exists() and (time.time() - _CACHE.stat().st_mtime) < _TTL
    if fresh or _state["building"]:
        return
    _state["building"] = True
    # Always backgrounded: ~29 batched downloads must never hold a page load.
    threading.Thread(target=_build_history, daemon=True).start()


def status() -> dict:
    return {"building": _state["building"], "progress": _state["progress"],
            "total": _state["total"], "failed": _state["failed"],
            "ready": _CACHE.exists(), "built_at": _state["ts"],
            "error": _state["error"], "mi_window": _MI_WIN,
            "years_back": _YEARS_BACK, "benchmark": _US_INDEX,
            "largecap_n": _LARGECAP_N, "unit": "batches"}


def get_momentum_index(history_days: int = 400, scope: str = "all") -> dict:
    """MI series + the current zero-line read + a divergence check.

    `scope`: "all" (every US common stock — the default and the faithful
    reading) or "largecap" (the fixed top-500-by-cap subset).
    """
    import pandas as pd
    if scope not in _SCOPES:
        scope = "all"
    ensure(background=True)
    if not _CACHE.exists():
        if _state["error"]:
            return {"status": "error", "error": _state["error"], "cache_status": status()}
        return {"status": "building", "cache_status": status()}
    try:
        df = pd.read_csv(_CACHE, index_col=0)
    except Exception as exc:                       # noqa: BLE001
        return {"status": "error", "error": str(exc), "cache_status": status()}
    if df.empty or len(df) < _MI_WIN:
        return {"status": "building", "note": f"need {_MI_WIN} sessions, have {len(df)}",
                "cache_status": status()}

    p = "lc_" if scope == "largecap" else ""
    net_col, adv_col, dec_col = f"{p}net", f"{p}adv", f"{p}dec"

    df = df.sort_index()
    df = df[df["n"] > 0]                           # defensive: see the build note
    df["mi"] = df[net_col].rolling(_MI_WIN).mean()
    mi = df["mi"].dropna()
    if mi.empty:
        return {"status": "building", "cache_status": status()}

    cur = float(mi.iloc[-1])
    sign = "positive" if cur > 0 else "negative" if cur < 0 else "zero"
    since = 0
    for v in reversed(mi.tolist()):
        if (v > 0) == (cur > 0):
            since += 1
        else:
            break
    # Extreme reached WITHIN the current run — signed, since below zero the
    # meaningful extreme is the trough and above zero the peak.
    run = mi.iloc[-since:] if since else mi.iloc[:0]
    extreme = extreme_date = None
    if len(run):
        extreme_date = str((run.idxmin if cur < 0 else run.idxmax)())
        extreme = float(run.min() if cur < 0 else run.max())
    last_cross = str(mi.index[-since]) if since <= len(mi) - 1 else None

    # Divergence (MECHANISED — not a formula from the book).
    div = None
    tail = df.dropna(subset=["mi"]).tail(_DIV_WIN)
    if len(tail) >= _DIV_WIN // 2 and tail["index_close"].notna().sum() >= _DIV_WIN // 2:
        ic, mv = tail["index_close"], tail["mi"]
        hi, lo = float(ic.max()), float(ic.min())
        now_i, now_m = float(ic.iloc[-1]), float(mv.iloc[-1])
        m_hi, m_lo = float(mv.max()), float(mv.min())
        rng = (m_hi - m_lo) or 1.0
        if hi and (now_i >= hi * (1 - _DIV_NEAR / 100)) and (m_hi - now_m) / rng * 100 > _DIV_GAP:
            div = {"type": "bearish",
                   "detail": "index near a %d-session high while MI sits well below its own" % _DIV_WIN}
        elif lo and (now_i <= lo * (1 + _DIV_NEAR / 100)) and (now_m - m_lo) / rng * 100 > _DIV_GAP:
            div = {"type": "bullish",
                   "detail": "index near a %d-session low while MI holds well above its own" % _DIV_WIN}

    # The last bar is provisional if it is today's US session read before the
    # 4pm ET close (20:00 UTC, ignoring DST — deliberately conservative).
    as_of = str(df.index[-1])
    t = time.gmtime()
    intraday = (as_of == time.strftime("%Y%m%d", t) and t.tm_hour < 20)

    hist = df.dropna(subset=["mi"]).tail(history_days)
    return {
        "status": "ready",
        "scope": scope,
        "as_of": as_of,
        "last_session_may_be_intraday": intraday,
        "mi": round(cur, 1),
        "sign": sign,
        "sessions_since_cross": since,
        "last_cross_date": last_cross,
        "extreme_since_cross": None if extreme is None else round(extreme, 1),
        "extreme_since_cross_date": extreme_date,
        "latest": {"adv": int(df[adv_col].iloc[-1]), "dec": int(df[dec_col].iloc[-1]),
                   "net": int(df[net_col].iloc[-1]),
                   "adv_pct": float(df[f"{p}adv_pct"].iloc[-1])},
        "universe_size": int(df[f"{p}n"].iloc[-1]),
        "divergence": div,
        "series": [{"d": str(i), "mi": None if pd.isna(r["mi"]) else round(float(r["mi"]), 1),
                    "net": int(r[net_col]),
                    "idx": None if pd.isna(r["index_close"]) else float(r["index_close"])}
                   for i, r in hist.iterrows()],
        "cache_status": status(),
    }

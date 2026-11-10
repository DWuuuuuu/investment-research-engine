"""A-share market breadth — Weinstein's Momentum Index (MI).

Chapter 8 of "Secrets for Profiting in Bull and Bear Markets" ("Using the Best
Long Term Indicators to Spot Bull and Bear Markets") describes TWO distinct
advance/decline constructs, which are easy to conflate:

  Momentum Index (MI)   200-day moving average of the DAILY NET (advances −
                        declines). Primary rule: the ZERO-LINE CROSSING — up
                        is bullish, down bearish. Signal strength grows the
                        longer it holds one side of zero and the further it
                        travels before crossing back. Weinstein notes it "can
                        be late on bottoms".
  Advance-Decline Line  A CUMULATIVE running total of the same daily net.
                        Primary rule: DIVERGENCE against the index.

This module builds the MI only, deliberately. The cumulative A/D Line is NOT
built here: this project's caches hold current constituents only, and in a
cumulative running total that survivorship bias compounds permanently —
delisted names never contribute their declines, so the line drifts upward
against reality forever. The MI is a 200-day moving average, so it only ever
looks back 200 days and the distortion cannot accumulate. The raw daily `net`
IS stored, so an A/D Line can be derived later if the delisting problem is
solved; it is simply not presented today.

MAPS TO the repo's own register: CN-B1.2 (advance/decline) and CN-B1.3
(breadth divergence), both currently `S:F D:T` — spec frozen, data to build.
Note the register specifies CN-B1.2 as an adv/decl RATIO while Weinstein uses
the net DIFFERENCE; the difference is what is implemented here, matching the
book, and the ratio is stored alongside so either can be used.

UNIVERSE. Every listed A-share is counted, including ST names — A/D is meant
to measure the whole market, and Weinstein used all NYSE issues. This differs
deliberately from cn_limit.py, which excludes ST because those names trade a
different (±5%) price-limit band that would distort a LIMIT-based statistic.
A/D has no such dependency.

CAVEATS (stated, not hidden):
  - Weinstein used NYSE A/D, a curated and relatively stable universe. The
    A-share listing count has grown substantially over any multi-year window,
    so the raw `net` scales partly with listing count, not only with breadth.
    The `adv_pct` share is stored alongside precisely so a scale-free read is
    available.
  - A-share daily price limits (±10%/20%, ±5% for ST) compress the tails of
    the daily move distribution relative to the US market Weinstein studied.
  - The divergence test below is a MECHANISATION of a rule the book states
    qualitatively ("the A/D often rolls over before the index"). The window
    and thresholds are choices, flagged as such — not values from the book.
  - No backtest has been run.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
_CACHE = _DATA / "cn_breadth_history.csv"
# Checkpoints go to a SEPARATE path. Writing them straight to _CACHE meant an
# interrupted rebuild (e.g. a daemon thread dying with its parent process)
# left the live cache overwritten by a partial checkpoint, destroying good
# history and taking the panel down until a full rebuild finished.
_PARTIAL = _DATA / "cn_breadth_partial.csv"
_TTL = 20 * 3600            # ~daily refresh, same cadence as cn_limit
_YEARS_BACK = 2             # ~490 trading days -> ~290 days of MI after warm-up
_MI_WIN = 200               # Weinstein's 200-day moving average
_DIV_WIN = 60               # lookback for the divergence test (unpinned choice)
_DIV_NEAR = 1.0             # index within this % of its window high/low (unpinned)
_DIV_GAP = 15.0             # MI must be this % off its own extreme to diverge (unpinned)

_CN_INDEX = "000985.SH"     # 中证全指 — same benchmark the Stage Analysis cache uses

_state = {"building": False, "ts": 0.0, "error": None, "progress": 0, "total": 0, "failed": 0}


def _index_daily(start: str, end: str) -> dict[str, float]:
    """Benchmark closes keyed by date. Tushare's suffix convention differs per
    endpoint and BOTH variants fail silently with zero rows (index_weekly wants
    .SH, index_daily wants .CSI — verified 2026-08-20), so try each."""
    from . import cn_data
    base = _CN_INDEX.split(".")[0]
    for code in (base + ".CSI", base + ".SH", _CN_INDEX):
        try:
            rows = cn_data.call("index_daily", {"ts_code": code, "start_date": start, "end_date": end},
                                "ts_code,trade_date,close")
        except Exception:
            rows = []
        if rows:
            return {r["trade_date"]: float(r["close"]) for r in rows if r.get("close") is not None}
    return {}


def _count_day(trade_date: str) -> dict | None:
    """Whole-market advances/declines for one session — ONE bulk call."""
    from . import cn_data
    try:
        rows = cn_data.call("daily", {"trade_date": trade_date}, "ts_code,pct_chg")
    except Exception:
        return None
    if not rows:
        return None
    adv = dec = unch = 0
    for r in rows:
        p = r.get("pct_chg")
        if p is None:
            continue
        if p > 0:
            adv += 1
        elif p < 0:
            dec += 1
        else:
            unch += 1
    n = adv + dec + unch
    if n == 0:
        return None
    return {"date": trade_date, "adv": adv, "dec": dec, "unch": unch,
            "net": adv - dec, "n": n,
            "adv_pct": round(adv / n * 100, 3),
            "ad_ratio": round(adv / dec, 4) if dec else None}


def _build_history() -> None:
    import pandas as pd
    from .cn_limit import _trading_dates          # reuse — same trade_cal helper
    try:
        dates = _trading_dates(_YEARS_BACK)
        _state["total"] = len(dates)
        _state["progress"] = 0
        _state["failed"] = 0
        idx = _index_daily(dates[0], dates[-1]) if dates else {}
        rows = []
        for td in dates:
            day = _count_day(td)
            if day:
                day["index_close"] = idx.get(td)
                rows.append(day)
            else:
                _state["failed"] += 1        # per-day isolation; one bad session is not fatal
            _state["progress"] += 1
            if len(rows) % 50 == 0 and rows:
                _DATA.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(rows).set_index("date").to_csv(_PARTIAL)  # checkpoint
        # Repair pass: a bulk call can fail transiently and silently drop a whole
        # session, which would shorten the 200-day window without any visible
        # error. Retry anything the calendar has but the run missed.
        if rows:
            got = {r["date"] for r in rows}
            for td in [d for d in dates if d not in got]:
                day = _count_day(td)
                if day:
                    day["index_close"] = idx.get(td)
                    rows.append(day)
                    _state["failed"] = max(0, _state["failed"] - 1)
            rows.sort(key=lambda r: r["date"])

        if rows:
            _DATA.mkdir(parents=True, exist_ok=True)
            # Only now is the live cache replaced — an aborted run leaves the
            # previous good history untouched.
            pd.DataFrame(rows).set_index("date").to_csv(_CACHE)
            _PARTIAL.unlink(missing_ok=True)
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
    # Always backgrounded: a 2-year backfill is ~490 bulk calls and must never
    # hold a page load open (same rule as cn_limit.py's history build).
    threading.Thread(target=_build_history, daemon=True).start()


def status() -> dict:
    return {"building": _state["building"], "progress": _state["progress"],
            "total": _state["total"], "failed": _state["failed"],
            "ready": _CACHE.exists(), "built_at": _state["ts"], "error": _state["error"],
            "mi_window": _MI_WIN, "years_back": _YEARS_BACK, "benchmark": _CN_INDEX}


def get_momentum_index(history_days: int = 400) -> dict:
    """MI series + the current zero-line read + a divergence check."""
    import pandas as pd
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

    df = df.sort_index()
    df["mi"] = df["net"].rolling(_MI_WIN).mean()
    mi = df["mi"].dropna()
    if mi.empty:
        return {"status": "building", "cache_status": status()}

    cur = float(mi.iloc[-1])
    sign = "positive" if cur > 0 else "negative" if cur < 0 else "zero"
    # Sessions since the last zero crossing — Weinstein: the longer it holds
    # one side, the stronger the reading.
    since = 0
    for v in reversed(mi.tolist()):
        if (v > 0) == (cur > 0):
            since += 1
        else:
            break
    # Extreme reached *within the current run* — Weinstein reads a larger
    # excursion from zero as a stronger move. Signed, not absolute: below zero
    # the meaningful extreme is the trough, above zero the peak.
    run = mi.iloc[-since:] if since else mi.iloc[:0]
    extreme = extreme_date = None
    if len(run):
        idx_fn = run.idxmin if cur < 0 else run.idxmax
        extreme_date = str(idx_fn())
        extreme = float(run.min() if cur < 0 else run.max())
    last_cross = str(mi.index[-since]) if since <= len(mi) - 1 else None

    # Divergence (MECHANISED, see module docstring — not a formula from the book):
    # index at/near a _DIV_WIN extreme while MI is well off its own.
    div = None
    tail = df.dropna(subset=["mi"]).tail(_DIV_WIN)
    if len(tail) >= _DIV_WIN // 2 and tail["index_close"].notna().sum() >= _DIV_WIN // 2:
        ic, mv = tail["index_close"], tail["mi"]
        hi, lo = float(ic.max()), float(ic.min())
        now_i, now_m = float(ic.iloc[-1]), float(mv.iloc[-1])
        m_hi, m_lo = float(mv.max()), float(mv.min())
        rng = (m_hi - m_lo) or 1.0
        if hi and (now_i >= hi * (1 - _DIV_NEAR / 100)) and (m_hi - now_m) / rng * 100 > _DIV_GAP:
            div = {"type": "bearish", "detail": "index near a %d-session high while MI sits well below its own" % _DIV_WIN}
        elif lo and (now_i <= lo * (1 + _DIV_NEAR / 100)) and (now_m - m_lo) / rng * 100 > _DIV_GAP:
            div = {"type": "bullish", "detail": "index near a %d-session low while MI holds well above its own" % _DIV_WIN}

    hist = df.dropna(subset=["mi"]).tail(history_days)
    return {
        "status": "ready",
        "as_of": str(df.index[-1]),
        "mi": round(cur, 1),
        "sign": sign,
        "sessions_since_cross": since,
        "last_cross_date": last_cross,
        "extreme_since_cross": None if extreme is None else round(extreme, 1),
        "extreme_since_cross_date": extreme_date,
        "latest": {"adv": int(df["adv"].iloc[-1]), "dec": int(df["dec"].iloc[-1]),
                   "net": int(df["net"].iloc[-1]), "adv_pct": float(df["adv_pct"].iloc[-1])},
        "divergence": div,
        "series": [{"d": str(i), "mi": None if pd.isna(r["mi"]) else round(float(r["mi"]), 1),
                    "net": int(r["net"]),
                    "idx": None if pd.isna(r["index_close"]) else float(r["index_close"])}
                   for i, r in hist.iterrows()],
        "cache_status": status(),
    }

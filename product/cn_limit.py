"""A-share daily limit-up/limit-down breadth — CN-B3.6/.10/.14 (Indicators Pool
v3.4). Close-based classification only, via Tushare's own daily_basic.limit_status
field (must be requested explicitly via fields= or it's silently omitted). The
touch-based stats (CN-B3.5/.7/.8 — "touched the limit but failed to close there")
need a heavier stk_limit+daily join and aren't built here yet.

Board-conditioning is MANDATORY, not optional. Main/ChiNext/STAR/BSE have
different price-limit bands (10%/20%/20%/30%), and the doc's own live check
found an 8x spread in touch-up rates across boards on one sampled date
(Main 3.47% vs ChiNext 0.43% vs STAR 0.65% vs BSE 0%, 2026-08-10). Every share
here is reported PER BOARD, never pooled into one market-wide number.

Locked (一字, all-day) shares are reported SEPARATELY from the ordinary shares,
never summed — the doc explicitly rejected that composite (CN-R5) as double-
counting, since the "locked" population is already a subset of the "closed at
limit" population.

Rolling historical percentile (this file's own ~2y background-built cache,
data/cn_limit_history.csv) covers the ORDINARY up/down shares only, per board.
The locked-share subset is NOT given a rolling percentile here — it's often a
single-digit count per board, and naively z-scoring/percentiling a count that
small is noise. The doc has a purpose-built method for exactly this (§9c.1,
sparse-count / beta-binomial shrinkage) — that's a deliberately separate,
not-yet-built follow-up, not an oversight.

Known limitation: the historical backfill uses TODAY's stock_basic list (board
+ ST status) for every past date, not each date's own point-in-time roster. A
stock's board assignment is essentially static, but ST status changes over
time — a name that is ST today was excluded from ALL historical days here too,
even dates before it became ST, which slightly understates N on those older
dates. Flagged, not hidden; fixing it properly needs a PIT ST-history table
this product doesn't have yet.

State/diag only — NEVER a directional buy/sell signal. Limit heat is
horizon-dependent (moderate heat can predict continuation, extreme heat can
signal exhaustion) per the doc's own hard rule; a fixed sign would bake in an
assumption the data doesn't support.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

_BOARDS = ["Main", "ChiNext", "STAR", "BSE"]

_DATA = Path(__file__).resolve().parent.parent / "data"
_CACHE = _DATA / "cn_limit_history.csv"
_TTL = 20 * 3600            # rebuild at most ~daily
_YEARS_BACK = 2             # backfill window
_ROLL = 500                 # ~2y rolling window (all available history, since we only keep 2y)
_MINP = 120                 # ~6mo warmup before a percentile is shown at all

_state = {"building": False, "ts": 0.0, "error": None, "progress": 0, "total": 0}
_lock = threading.Lock()


def _board(ts_code: str) -> str | None:
    if not ts_code or "." not in ts_code:
        return None
    sym, ex = ts_code.split(".")
    if ex == "BJ":
        return "BSE"
    if ex == "SH":
        if sym[:3] == "688":
            return "STAR"
        if sym[:3] in ("600", "601", "603", "605"):
            return "Main"
        return None   # B-shares (900) etc.
    if ex == "SZ":
        if sym[:3] in ("300", "301"):
            return "ChiNext"
        if sym[:3] in ("000", "001", "002", "003"):
            return "Main"
        return None   # B-shares (200) etc.
    return None


def _compute_day(trade_date: str, basic: dict) -> dict | None:
    """One day's board-level breadth. Shared by the live read and the historical
    backfill so both are computed identically (apples-to-apples for the percentile)."""
    from . import cn_data
    try:
        rows = cn_data.call("daily_basic", {"trade_date": trade_date}, "ts_code,limit_status")
    except Exception:
        return None
    if not rows:
        return None

    by_board: dict[str, dict] = {b: {"n": 0, "up": 0, "up_locked": 0, "down": 0, "down_locked": 0} for b in _BOARDS}
    for r in rows:
        ts = r.get("ts_code")
        b = basic.get(ts) or {}
        nm = b.get("name") or ""
        if nm.startswith("ST") or nm.startswith("*ST") or nm.startswith("*") or "退" in nm:
            continue   # ST names trade a different (+/-5%) price-limit band — excluded, same convention filter_screener.py uses
        board = _board(ts)
        if board is None:
            continue
        try:
            status = int(r.get("limit_status"))
        except (TypeError, ValueError):
            continue
        slot = by_board[board]
        slot["n"] += 1
        if status in (2, 3):
            slot["up"] += 1
            if status == 3:
                slot["up_locked"] += 1
        elif status in (5, 6):
            slot["down"] += 1
            if status == 6:
                slot["down_locked"] += 1

    return {"date": trade_date, "by_board": by_board}


def _boards_payload(by_board: dict) -> list[dict]:
    boards_out = []
    for b in _BOARDS:
        s = by_board[b]
        n = s["n"]
        if n == 0:
            continue
        boards_out.append({
            "board": b, "n_eligible": n,
            "up_share": round(s["up"] / n * 100, 2),
            "up_locked_share": round(s["up_locked"] / n * 100, 2),
            "down_share": round(s["down"] / n * 100, 2),
            "down_locked_share": round(s["down_locked"] / n * 100, 2),
            "up_count": s["up"], "up_locked_count": s["up_locked"],
            "down_count": s["down"], "down_locked_count": s["down_locked"],
        })
    return boards_out


# ── historical backfill (rolling percentile benchmark) ─────────────────────────

def _trading_dates(years_back: int) -> list[str]:
    from datetime import date, timedelta
    from . import cn_data
    end = date.today()
    start = end - timedelta(days=365 * years_back + 10)
    rows = cn_data.call(
        "trade_cal", {"exchange": "SSE", "start_date": start.strftime("%Y%m%d"),
                      "end_date": end.strftime("%Y%m%d"), "is_open": "1"},
        "cal_date,is_open",
    )
    return sorted(r["cal_date"] for r in rows)


def _build_history() -> None:
    import pandas as pd
    from . import cn_data
    try:
        dates = _trading_dates(_YEARS_BACK)
        _state["total"] = len(dates)
        _state["progress"] = 0
        basic = {b["ts_code"]: b for b in cn_data._stock_basic_list()}
        rows = []
        for td in dates:
            day = _compute_day(td, basic)
            if day:
                rec = {"date": day["date"]}
                for b in _BOARDS:
                    s = day["by_board"][b]
                    n = s["n"]
                    rec[f"{b}_up"] = (s["up"] / n * 100) if n else None
                    rec[f"{b}_down"] = (s["down"] / n * 100) if n else None
                rows.append(rec)
            _state["progress"] += 1
        if rows:
            df = pd.DataFrame(rows).set_index("date")
            _DATA.mkdir(parents=True, exist_ok=True)
            df.to_csv(_CACHE)
        _state["ts"] = time.time()
        _state["error"] = None
    except Exception as exc:                       # noqa: BLE001
        _state["error"] = str(exc)
    finally:
        _state["building"] = False


def _ensure_history(background: bool = True) -> None:
    fresh = _CACHE.exists() and (time.time() - _CACHE.stat().st_mtime) < _TTL
    if fresh or _state["building"]:
        return
    # Always backgrounded, never blocking a request — unlike cn_fear.py's
    # pattern (which blocks on the very first build), a 2-year backfill here is
    # ~500 daily bulk calls (~8-9 minutes), too long to hold a page load open on.
    _state["building"] = True
    threading.Thread(target=_build_history, daemon=True).start()


def _percentile_lookup(board: str, side: str, today_value: float | None) -> dict | None:
    """Where today's live reading falls vs. this board's own history. Returns
    None if there isn't enough history yet (min ~6mo) — never fabricates a
    percentile from a thin sample."""
    import pandas as pd
    if today_value is None or not _CACHE.exists():
        return None
    try:
        df = pd.read_csv(_CACHE, index_col=0)
    except Exception:
        return None
    col = f"{board}_{side}"
    if col not in df.columns:
        return None
    hist = df[col].dropna()
    if len(hist) < _MINP:
        return {"pctile": None, "n_days": len(hist), "warmup_needed": _MINP}
    hist = hist.tail(_ROLL)
    pct = round(float((hist < today_value).mean()) * 100, 1)
    return {"pctile": pct, "n_days": len(hist), "warmup_needed": _MINP}


def history_status() -> dict:
    return {
        "building": _state["building"], "progress": _state["progress"], "total": _state["total"],
        "ready": _CACHE.exists(), "built_at": _state["ts"], "error": _state["error"],
    }


# ── public API ───────────────────────────────────────────────────────────────

def get_limit_breadth() -> dict:
    from . import cn_data

    _ensure_history()   # kick off / keep the background history cache warm

    ref = cn_data.latest_row("daily_basic", "000001.SZ", "ts_code,trade_date")
    trade_date = ref.get("trade_date") if ref else None
    if not trade_date:
        return {"status": "error", "error": "Could not determine latest trade date."}

    basic = {b["ts_code"]: b for b in cn_data._stock_basic_list()}
    day = _compute_day(trade_date, basic)
    if day is None:
        return {"status": "error", "error": "No daily_basic rows for latest trade date."}

    boards_out = _boards_payload(day["by_board"])
    for b in boards_out:
        b["up_pctile"] = _percentile_lookup(b["board"], "up", b["up_share"])
        b["down_pctile"] = _percentile_lookup(b["board"], "down", b["down_share"])

    return {
        "status": "ready",
        "date": trade_date,
        "boards": boards_out,
        "history_status": history_status(),
    }

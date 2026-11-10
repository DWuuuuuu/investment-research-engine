"""A-share sell-side research coverage — Tushare's report_rc (券商研究报告),
purchased 2026-08-12 (¥500/yr, needs its own permission grant, unrelated to
points balance).

IMPORTANT — the live schema does NOT match Tushare's own doc page
(https://tushare.pro/document/2?doc_id=415). Verified by direct calls before
building anything here:
  - No `abstr` (abstract) or `url` field exists at all. There is no way to
    link out to or download the underlying report text/PDF through this
    interface — only structured metadata. Do not build a "download" UI
    affordance; it would point nowhere.
  - Real fields: ts_code, name, report_date, report_title, report_type,
    classify, org_name, author_name, quarter, op_rt, op_pr, tp, np, eps, pe,
    rd, roe, ev_ebitda, rating, max_price, min_price. `quarter` marks which
    fiscal year a forecast row applies to — ONE report typically produces
    2-3 rows (one per forecast year, e.g. 2026Q4/2027Q4/2028Q4), all sharing
    the same report_date/org_name/report_title/rating. Dedupe on that triple
    to count actual reports, not forecast-year rows.
  - `rating` is free text set by each brokerage's own house scale, not a
    single controlled vocabulary — observed mixing Chinese (买入/增持/推荐/
    强烈推荐/跑赢行业/优于大市/买进/中性/持有/卖出/减持/无 ...) and English
    (Buy/Overweight/Neutral/Hold/Sell/Underweight ...) in the same feed
    depending on which broker filed it. Bucketed below into
    positive/neutral/negative/unrated; anything unrecognized falls into
    "other" and is logged, never silently miscategorized.
  - `report_type` includes "非个股" (not stock-specific — a sector/strategy
    piece that happens to tag multiple ts_codes) — excluded from per-stock
    coverage counts, since counting it would overstate how much dedicated
    single-name attention a stock is getting.
  - `classify` flags 首次关注/首份报告/首次评级 (first coverage / first
    report / first rating) — used here as a "just initiated coverage" catalyst
    flag.

Rate limit: nothing like the 500/min the pricing page states, and not stable.
The same account has been told "1次/分钟", "10次/小时" and "10次/天" at
different times, and a REFUSED request still counts against the quota. So the
limit is never probed: the gap between calls is learned from the refusal text
and kept on disk (see _learn_cap), an hourly or daily cap ends the run at once
instead of being retried, and the run resumes by itself when the cooldown is
over. Per-stock consumers (cn_forecasts) read this cache rather than calling
report_rc themselves — ten stock pages would otherwise spend the hour's
allowance.

Consensus forward estimates (np/eps/roe) are only computed with >=2
distinct contributing analysts for the same forecast year — a lone analyst's
number isn't a "consensus," same discipline as every other "don't claim more
than the data supports" cache in this codebase.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_RAW_CACHE_FILE = _DATA_DIR / "cn_reports_raw.json"

_COVERAGE_WINDOW_DAYS = 180   # aggregation window for coverage/rating-mix stats
_HORIZON_DAYS = _COVERAGE_WINDOW_DAYS   # how far back to fetch: exactly as far as anything reads
_GRID_DAYS = 5                # per-call chunk for the backfill (see FETCH PLAN below)
_SETTLE_DAYS = 2              # a window is only final once it has been closed this long
_TTL = 12 * 3600              # refresh cadence — matches Tushare's "twice daily" update note
_ROW_CAP = 5000               # a response of exactly this many rows has been truncated
_MIN_GAP = 61.0               # seconds between calls: the account is held to one a minute
_EPOCH = date(2020, 1, 1)     # fixed origin for the window grid — see _grid_windows

# FETCH PLAN — rewritten 2026-10-12, after the portal had shown nothing for
# seven weeks. Four things were wrong at once, and any one of them was enough:
#
#  1. OLDEST FIRST. The backfill started 365 days ago and worked forward, so
#     the last week — the only thing the digest page displays — arrived last,
#     after some seventy-five calls. Now: the most recent days first, then
#     backwards.
#
#  2. RESUME COULD NOT HAPPEN. A build only ran in "backfill" mode while the
#     cache file did not exist, and only trusted its saved progress while the
#     file DID exist. The first window written therefore ended the backfill
#     for good: every later run was a five-day top-up, and the other 24
#     windows were never fetched. Worse, the windows were measured from
#     "today", so their keys changed every day and yesterday's progress could
#     not have matched anyway. Now: windows sit on a fixed grid, and a run
#     simply fetches whichever grid windows are not yet done.
#
#  3. EVERY WINDOW WASTED A CALL. A 15-day window always holds more than the
#     5,000-row cap (8 days of late October 2025 alone returned 4,634, and
#     the 8 days after that 5,000), so each one was fetched, found truncated,
#     split and fetched twice more. Now: 5-day windows, split only when one
#     really is full — and split without both halves re-fetching the middle
#     day, which they used to.
#
#  4. EVERY CALL WAS REJECTED ONCE FIRST. Calls were fired back to back at an
#     account limited to one a minute, so each was refused, slept on, and
#     retried. Now: the gap is waited out before the call rather than after.
#
# It also fetched a year while nothing in the app reads past 180 days.

# op_rt (forecast revenue), max_price/min_price (target range) are carried for
# the per-stock forecast card. `pe` is deliberately NOT: it is each broker's own
# multiple at its own price on its own day, and has been seen flatly wrong
# (Nomura's CXMT rows show 1.5-4.2x against its own EPS). Multiples are
# recomputed from np and today's market cap instead.
_FIELDS = ("ts_code,name,report_date,report_title,report_type,classify,org_name,author_name,"
           "quarter,op_rt,np,eps,roe,rating,max_price,min_price")

_RATING_BUCKETS: dict[str, str] = {
    "买入": "positive", "增持": "positive", "推荐": "positive", "强烈推荐": "positive",
    "强推": "positive", "跑赢行业": "positive", "优于大市": "positive", "买进": "positive",
    "谨慎增持": "positive", "买入评级": "positive",
    "Buy": "positive", "BUY": "positive", "Overweight": "positive", "Outperform": "positive",
    "中性": "neutral", "持有": "neutral", "区间操作": "neutral",
    "Neutral": "neutral", "Hold": "neutral", "Equal weight": "neutral",
    "卖出": "negative", "减持": "negative", "谨慎减持": "negative", "回避": "negative",
    "Sell": "negative", "SELL": "negative", "Underweight": "negative",
    "无": "unrated", "": "unrated", None: "unrated",
}
_INITIATION_CLASSIFY = {"首次关注", "首份报告", "首次评级"}
_NON_STOCK_TYPE = "非个股"

_state: dict[str, Any] = {"building": False, "ts": 0.0, "error": None, "error_ts": 0.0,
                           "progress": 0, "total": 0, "unmapped_ratings": [],
                           "truncated_days": []}
_lock = threading.Lock()

# error/error_ts must survive a process restart, not just live in _state —
# otherwise every dev-server restart (routine during active development)
# resets the in-memory cooldown clock to zero and the very next status poll
# fires a brand new attempt, silently defeating _FAILURE_COOLDOWN entirely.
# Caught live 2026-08-12: restarted the server to ship the cooldown fix, then
# a single status-check curl immediately kicked off a fresh build because the
# fresh process had no memory of the prior failure.
_ERROR_STATE_FILE = _DATA_DIR / "cn_reports_error_state.json"


def _load_error_state() -> None:
    try:
        if _ERROR_STATE_FILE.exists():
            data = json.loads(_ERROR_STATE_FILE.read_text())
            if isinstance(data, dict):
                _state["error"] = data.get("error")
                _state["error_ts"] = float(data.get("error_ts") or 0)
    except Exception:
        pass


def _save_error_state() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _ERROR_STATE_FILE.write_text(json.dumps({"error": _state["error"], "error_ts": _state["error_ts"]}))
    except Exception:
        pass


_load_error_state()   # runs once at import time, so a fresh process inherits the cooldown clock

# After a cap is hit, the background job leaves the API alone for a while, so
# the penalty can expire instead of being reset by another refused request.
#
# Tushare's 40203 error text names WHICH window was exceeded, and it has been
# seen changing for the same account: "1次/分钟" -> "10次/小时" -> "10次/天".
# The wait is scaled to what the message says, and defaults to the longest
# (daily) tier when the wording is unrecognised: guessing short and re-hitting
# a real daily cap is worse than waiting a few hours more than necessary.
_DEFAULT_COOLDOWN = 24 * 3600


def _cooldown_for_error(msg: str | None) -> int:
    if not msg:
        return _DEFAULT_COOLDOWN
    if "次/天" in msg:
        return 24 * 3600
    if "次/小时" in msg:
        return 65 * 60        # a rolling hour is clear an hour after the last counted request
    if "次/分钟" in msg:
        return 15 * 60
    return _DEFAULT_COOLDOWN


def _rating_bucket(raw: str | None) -> str:
    b = _RATING_BUCKETS.get(raw)
    if b is not None:
        return b
    if raw not in _state["unmapped_ratings"]:
        _state["unmapped_ratings"].append(raw)   # surfaced in reports_status(), never silently dropped
    return "other"


# ── fetching, rate-limit-adaptive ──────────────────────────────────────────────

def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_ymd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# The account's limits are not what the pricing page says, and they are not
# stable: "1次/分钟", "10次/小时" and "10次/天" have all been returned for it. A
# refused request still counts against the quota (11 requests on 2026-10-12 —
# five answered, six refused — exhausted a ten-an-hour allowance), so finding
# the limit by running into it is the one approach guaranteed to make it
# worse. Instead the gap between calls is LEARNED from the refusal text and
# kept on disk: told "10次/小时", every later call waits six minutes, and the
# limit is not hit again. The last call time is on disk too, because a dev
# server restarts constantly and each fresh process used to fire at once.
_PACE_FILE = _DATA_DIR / "cn_reports_pace.json"
_PACE_MEMORY = 7 * 86400      # a learned limit is forgotten after a week, in case it was lifted
_CAP_RE = re.compile(r"(\d+)\s*次/(分钟|小时|天)")
_UNIT_SECONDS = {"分钟": 60, "小时": 3600}      # a daily cap is a cooldown, not a gap


def _load_pace() -> dict[str, float]:
    pace = {"gap": _MIN_GAP, "gap_ts": 0.0, "last_call": 0.0}
    try:
        data = json.loads(_PACE_FILE.read_text())
        if isinstance(data, dict):
            pace.update({k: float(data.get(k) or pace[k]) for k in pace})
    except Exception:
        pass
    if pace["gap"] > _MIN_GAP and time.time() - pace["gap_ts"] > _PACE_MEMORY:
        pace["gap"] = _MIN_GAP
    return pace


def _save_pace(pace: dict[str, float]) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _PACE_FILE.write_text(json.dumps(pace))
    except Exception:
        pass


def _learn_cap(msg: str, when: float | None = None) -> None:
    """Widen the gap to fit whatever limit the refusal names."""
    m = _CAP_RE.search(msg or "")
    if not m or m.group(2) not in _UNIT_SECONDS:
        return
    when = time.time() if when is None else when
    if time.time() - when > _PACE_MEMORY:
        return
    gap = _UNIT_SECONDS[m.group(2)] / max(1, int(m.group(1))) + (1.0 if m.group(2) == "分钟" else 5.0)
    pace = _load_pace()
    if gap > pace["gap"]:
        pace.update({"gap": gap, "gap_ts": when})
        _save_pace(pace)
        print(f"[cn_reports] limit is {m.group(0)} — spacing calls {gap:.0f}s apart from now on", flush=True)


# A refusal recorded by an earlier process is a lesson this one has not had.
_learn_cap(_state.get("error") or "", _state.get("error_ts") or 0.0)


def _pace() -> None:
    """Wait out the limit BEFORE calling, not after being refused."""
    pace = _load_pace()
    wait = pace["gap"] - (time.time() - pace["last_call"])
    if wait > 0:
        time.sleep(wait)


def _fetch(start: str, end: str) -> list[dict]:
    """One vendor call, paced. A per-minute refusal is retried, because waiting
    fixes it. An hourly or daily cap is not: a retry is itself a counted
    request, so it is raised at once and the run stops until the cooldown."""
    from . import cn_data
    for attempt in range(3):
        _pace()
        print(f"[cn_reports] fetch {start}-{end} attempt {attempt + 1}/3", flush=True)
        pace = _load_pace()
        pace["last_call"] = time.time()
        _save_pace(pace)
        try:
            rows = cn_data.call("report_rc", {"start_date": start, "end_date": end}, _FIELDS)
            print(f"[cn_reports] {start}-{end} OK: {len(rows)} rows", flush=True)
            return rows
        except cn_data.TushareError as e:
            msg = str(e)
            print(f"[cn_reports] {start}-{end} FAILED attempt {attempt + 1}: {msg}", flush=True)
            if "频率超限" in msg:
                _learn_cap(msg)
                if "次/分钟" in msg and attempt < 2:
                    continue                           # _pace() waits before the retry
            raise
    return []                                          # unreachable; keeps the type honest


def _call_rc(start: str, end: str, depth: int = 0) -> list[dict]:
    """A date range, split in two whenever the vendor's row cap cut it short.

    A response of exactly _ROW_CAP rows is a truncated one — seen on a 31-day
    pull, a 15-day pull and an 8-day pull. The halves do not overlap: the old
    split gave both of them the middle day.
    """
    rows = _fetch(start, end)
    if len(rows) < _ROW_CAP:
        return rows
    if start == end:
        # One day, and still full. It cannot be split further by date, so say
        # so rather than pass a partial day off as a whole one.
        if start not in _state["truncated_days"]:
            _state["truncated_days"].append(start)
        print(f"[cn_reports] {start} alone hit the {_ROW_CAP}-row cap — partial day kept, flagged", flush=True)
        return rows
    a, b = _parse_ymd(start), _parse_ymd(end)
    mid = a + (b - a) // 2
    return (_call_rc(start, _ymd(mid), depth + 1)
            + _call_rc(_ymd(mid + timedelta(days=1)), end, depth + 1))


def _grid_windows(today: date) -> list[tuple[str, str]]:
    """Closed, settled windows inside the horizon — NEWEST FIRST.

    Windows sit on a fixed grid counted from _EPOCH, not from today. That is
    what makes progress survive: "20260928_20261002" is the same window
    tomorrow, so a run that is cut off by a quota resumes where it stopped. A
    window measured back from today has a different name every morning.

    A window is offered only once it has been closed for _SETTLE_DAYS, since
    brokers file reports dated a day or two back. Until then its days belong
    to the rolling top-up.
    """
    last = (today - timedelta(days=_SETTLE_DAYS + 1) - _EPOCH).days     # latest day a window may end on
    k_hi = (last - (_GRID_DAYS - 1)) // _GRID_DAYS
    k_lo = (today - timedelta(days=_HORIZON_DAYS) - _EPOCH).days // _GRID_DAYS
    out = []
    for k in range(k_hi, k_lo - 1, -1):
        s = _EPOCH + timedelta(days=k * _GRID_DAYS)
        out.append((_ymd(s), _ymd(s + timedelta(days=_GRID_DAYS - 1))))
    return out


def _topup_range(today: date) -> tuple[str, str]:
    """From the day after the newest settled window through today, so the
    top-up and the grid meet with no day between them (3 to 7 days)."""
    grid = _grid_windows(today)
    start = (_parse_ymd(grid[0][1]) + timedelta(days=1)) if grid else today - timedelta(days=_GRID_DAYS + _SETTLE_DAYS)
    return _ymd(start), _ymd(today)


_RAW_MEM: dict[str, Any] = {"mtime": None, "rows": []}


def _load_raw() -> list[dict]:
    """The cache, parsed once per version of the file. The research page polls
    every eight seconds while a build runs; re-parsing tens of megabytes of
    JSON on each poll is how a status check becomes the slowest thing here."""
    try:
        mtime = _RAW_CACHE_FILE.stat().st_mtime
    except OSError:
        return []
    if _RAW_MEM["mtime"] == mtime:
        return _RAW_MEM["rows"]
    try:
        data = json.loads(_RAW_CACHE_FILE.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    _RAW_MEM.update({"mtime": mtime, "rows": data})
    return data


def _save_raw(rows: list[dict]) -> None:
    """Written beside the file and moved into place. A crash halfway through a
    direct write would leave a truncated cache that reads back as empty —
    while the progress file still said every window was done."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _RAW_CACHE_FILE.with_suffix(".partial")
        tmp.write_text(json.dumps(rows))
        tmp.replace(_RAW_CACHE_FILE)
        _RAW_MEM.update({"mtime": _RAW_CACHE_FILE.stat().st_mtime, "rows": rows})
    except Exception:
        pass


def _merge_rows(existing: list[dict], fresh: list[dict]) -> list[dict]:
    seen = {(r.get("report_date"), r.get("org_name"), r.get("report_title"), r.get("quarter"), r.get("ts_code"))
            for r in existing}
    merged = list(existing)
    for r in fresh:
        key = (r.get("report_date"), r.get("org_name"), r.get("report_title"), r.get("quarter"), r.get("ts_code"))
        if key not in seen:
            merged.append(r)
            seen.add(key)
    return merged


def _trim(rows: list[dict], today: date) -> list[dict]:
    cutoff = _ymd(today - timedelta(days=_HORIZON_DAYS))
    return [r for r in rows if (r.get("report_date") or "") >= cutoff]


# Progress, kept on disk: which grid windows are in, and when the recent days
# were last refreshed. Saved after EVERY window, so a quota that runs out
# mid-backfill costs the window in flight and nothing else.
_BACKFILL_STATE_FILE = _DATA_DIR / "cn_reports_backfill_state.json"


def _load_plan() -> dict[str, Any]:
    blank = {"done": [], "topup_ts": 0.0, "topup_from": None}
    try:
        data = json.loads(_BACKFILL_STATE_FILE.read_text())
    except Exception:
        return blank
    # The previous format was a bare list of today-relative 15-day window keys.
    # None of them names a grid window, so there is nothing in it to keep.
    if not isinstance(data, dict):
        return blank
    return {"done": list(data.get("done") or []),
            "topup_ts": float(data.get("topup_ts") or 0.0),
            "topup_from": data.get("topup_from")}


def _save_plan(plan: dict[str, Any]) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _BACKFILL_STATE_FILE.write_text(json.dumps(plan))
    except Exception:
        pass


def _pending(today: date, plan: dict[str, Any], now: float) -> tuple[bool, list[tuple[str, str]]]:
    """(top-up due?, grid windows still to fetch, newest first).

    Progress is only believed while the cache it describes exists. If the data
    file is gone, "done" describes nothing and everything is fetched again.
    """
    have_cache = _RAW_CACHE_FILE.exists()
    done = set(plan["done"]) if have_cache else set()
    todo = [(s, e) for s, e in _grid_windows(today) if f"{s}_{e}" not in done]
    topup_due = (not have_cache) or (now - plan["topup_ts"] >= _TTL)
    return topup_due, todo


def _build() -> None:
    try:
        today = date.today()
        plan = _load_plan()
        if not _RAW_CACHE_FILE.exists():
            plan = {"done": [], "topup_ts": 0.0, "topup_from": None}
        grid = _grid_windows(today)
        keys = {f"{s}_{e}" for s, e in grid}
        done = set(plan["done"]) & keys                      # forget windows that left the horizon
        topup_due, todo = _pending(today, plan, time.time())
        _state["total"] = len(grid)
        _state["progress"] = len(done)

        # 1. The recent days, first. This one call is everything the digest shows.
        if topup_due:
            s, e = _topup_range(today)
            rows = _call_rc(s, e)
            _save_raw(_trim(_merge_rows(_load_raw(), rows), today))
            plan.update({"done": sorted(done), "topup_ts": time.time(), "topup_from": s})
            _save_plan(plan)
            _state["ts"] = time.time()

        # 2. Then backwards through the grid until the horizon is covered or the
        #    vendor's quota says stop.
        for s, e in todo:
            rows = _call_rc(s, e)
            _save_raw(_trim(_merge_rows(_load_raw(), rows), today))
            done.add(f"{s}_{e}")
            plan["done"] = sorted(done)
            _save_plan(plan)
            _state["progress"] = len(done)
            _state["ts"] = time.time()

        _state["error"] = None
        _save_error_state()
    except Exception as exc:                      # noqa: BLE001
        _state["error"] = str(exc)
        _state["error_ts"] = time.time()
        _save_error_state()
        _resume_after(_cooldown_for_error(_state["error"]))
    finally:
        _state["building"] = False


def _resume_after(seconds: float) -> None:
    """Pick the backfill up again once the cooldown has run out.

    The page has always said a paused run "will retry automatically". That was
    only true if someone happened to load a page after the cooldown: nothing on
    the server was waiting. Now something is.
    """
    t = threading.Timer(seconds + 5, ensure_reports_cache)
    t.daemon = True
    t.start()


def resume_on_startup() -> None:
    """Called once by the web server. A restart forgets the timer that was
    waiting out a cooldown — and a dev server restarts constantly — so the
    wait is picked up again from the error time on disk. With no cooldown
    running, it simply carries on with whatever is still to fetch."""
    remaining = 0.0
    if _state["error"]:
        remaining = _cooldown_for_error(_state["error"]) - (time.time() - _state["error_ts"])
    if remaining > 0:
        _resume_after(remaining)
    else:
        ensure_reports_cache()


def ensure_reports_cache() -> None:
    with _lock:
        if _state["building"]:
            return
        in_cooldown = _state["error"] and (time.time() - _state["error_ts"]) < _cooldown_for_error(_state["error"])
        if in_cooldown:
            return
        topup_due, todo = _pending(date.today(), _load_plan(), time.time())
        if not topup_due and not todo:
            return
        _state["building"] = True
    threading.Thread(target=_build, daemon=True).start()


def _coverage(today: date, plan: dict[str, Any]) -> dict[str, Any]:
    """How far back the cache really reaches, without a gap, from today.

    Fetching is newest-first, so coverage is one unbroken run backwards. The
    180-day counts are only true once that run reaches the horizon; until then
    they undercount, and every consumer needs to be able to say so.
    """
    grid = _grid_windows(today)
    if not _RAW_CACHE_FILE.exists() or not plan["topup_ts"]:
        return {"covered_from": None, "covered_days": 0, "complete": False,
                "done": 0, "total": len(grid)}
    done = set(plan["done"])
    covered_from = plan.get("topup_from") or _ymd(today)
    n = 0
    for s, e in grid:
        if f"{s}_{e}" not in done:
            break
        covered_from, n = s, n + 1
    in_horizon = sum(1 for s, e in grid if f"{s}_{e}" in done)
    return {"covered_from": covered_from,
            "covered_days": (today - _parse_ymd(covered_from)).days + 1,
            "complete": n == len(grid), "done": in_horizon, "total": len(grid)}


def reports_status() -> dict:
    cooldown_remaining = None
    if _state["error"] and not _state["building"]:
        remaining = _cooldown_for_error(_state["error"]) - (time.time() - _state["error_ts"])
        cooldown_remaining = max(0, round(remaining / 60))
    plan = _load_plan()
    cov = _coverage(date.today(), plan)
    # A cooldown that has run out is history, not a current fault: the next
    # request starts a fresh attempt. Left in place it reads as "still broken".
    stale_error = bool(_state["error"]) and (_state["building"] or cooldown_remaining == 0)
    return {
        "building": _state["building"],
        "progress": cov["done"], "total": cov["total"],
        "ready": _RAW_CACHE_FILE.exists() and bool(plan["topup_ts"]),
        "complete": cov["complete"],
        "covered_from": cov["covered_from"], "covered_days": cov["covered_days"],
        "horizon_days": _HORIZON_DAYS,
        "built_at": plan["topup_ts"] or _state["ts"],
        "error": None if stale_error else _state["error"],
        "cooldown_remaining_min": cooldown_remaining,
        "request_gap_s": round(_load_pace()["gap"]),
        "truncated_days": list(_state["truncated_days"]),
        "unmapped_ratings": _state["unmapped_ratings"],
    }


# ── aggregation (per-stock, for the filter/detail card) ────────────────────────

def _unique_reports(rows: list[dict]) -> list[dict]:
    """Collapse the 2-3 forecast-year rows a single report produces down to one
    row per actual report (rating/org/title/date are identical across a
    report's own forecast-year rows, verified live before relying on it)."""
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("report_date"), r.get("org_name"), r.get("report_title"), r.get("ts_code"))
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def build_reports_map() -> dict[str, dict]:
    """Per-stock aggregate: coverage counts, rating mix, initiation flag,
    nearest-year consensus estimate. Computed fresh from the raw cache each
    call — a few tens of thousands of small rows, cheap to scan in memory."""
    rows = _load_raw()
    if not rows:
        return {}
    cutoff = (date.today() - timedelta(days=_COVERAGE_WINDOW_DAYS)).strftime("%Y%m%d")

    by_stock: dict[str, list[dict]] = {}
    for r in rows:
        ts = r.get("ts_code")
        if not ts:
            continue
        by_stock.setdefault(ts, []).append(r)

    out: dict[str, dict] = {}
    for ts, stock_rows in by_stock.items():
        uniq = _unique_reports(stock_rows)
        uniq_in_window = [r for r in uniq
                           if (r.get("report_date") or "") >= cutoff and r.get("report_type") != _NON_STOCK_TYPE]
        if not uniq_in_window:
            continue
        uniq_in_window.sort(key=lambda r: r.get("report_date") or "", reverse=True)

        latest = uniq_in_window[0]
        latest_bucket = _rating_bucket(latest.get("rating"))

        mix = {"positive": 0, "neutral": 0, "negative": 0, "unrated": 0, "other": 0}
        for r in uniq_in_window:
            mix[_rating_bucket(r.get("rating"))] += 1

        insts = {r.get("org_name") for r in uniq_in_window if r.get("org_name")}

        initiated = [r for r in uniq_in_window if r.get("classify") in _INITIATION_CLASSIFY]
        initiated_date = max((r.get("report_date") for r in initiated), default=None)

        # nearest-future-year consensus: group ALL forecast rows (not deduped —
        # each analyst's own row per fiscal year) by `quarter`, take the
        # soonest year with >=2 distinct contributing institutions.
        by_quarter: dict[str, list[dict]] = {}
        for r in stock_rows:
            q = r.get("quarter")
            rd = r.get("report_date") or ""
            if q and rd >= cutoff and r.get("report_type") != _NON_STOCK_TYPE:
                by_quarter.setdefault(q, []).append(r)
        consensus = None
        for q in sorted(by_quarter):
            contributors = by_quarter[q]
            distinct_orgs = {r.get("org_name") for r in contributors if r.get("org_name")}
            if len(distinct_orgs) < 2:
                continue
            eps_vals = sorted(v for v in (r.get("eps") for r in contributors) if v is not None)
            np_vals = sorted(v for v in (r.get("np") for r in contributors) if v is not None)
            roe_vals = sorted(v for v in (r.get("roe") for r in contributors) if v is not None)
            if not eps_vals:
                continue
            consensus = {
                "fiscal_year": q, "n_analysts": len(distinct_orgs),
                "eps_median": eps_vals[len(eps_vals) // 2],
                "np_median": np_vals[len(np_vals) // 2] if np_vals else None,
                "roe_median": roe_vals[len(roe_vals) // 2] if roe_vals else None,
            }
            break   # soonest qualifying year only

        out[ts] = {
            "coverage_count": len(uniq_in_window),
            "coverage_inst_count": len(insts),
            "latest_report_date": latest.get("report_date"),
            "latest_org": latest.get("org_name"),
            "latest_rating": latest.get("rating"),
            "latest_rating_bucket": latest_bucket,
            "rating_mix": mix,
            "initiated_recently": bool(initiated),
            "initiated_date": initiated_date,
            "consensus": consensus,
            "window_days": _COVERAGE_WINDOW_DAYS,
        }
    return out


# ── digest (for the "read it myself daily" view) ────────────────────────────────

def get_recent_reports(days: int = 3, ts_code: str | None = None, limit: int = 300) -> dict:
    """Newest-first digest of individual reports (deduped across forecast-year
    rows). No `url`/full-text field exists in this Tushare interface — this
    is metadata (who covered what, with what rating and forecast), not a
    link to the underlying PDF."""
    ensure_reports_cache()
    rows = _load_raw()
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    filtered = [r for r in rows if (r.get("report_date") or "") >= cutoff]
    if ts_code:
        filtered = [r for r in filtered if r.get("ts_code") == ts_code]
    uniq = _unique_reports(filtered)
    uniq.sort(key=lambda r: r.get("report_date") or "", reverse=True)
    out = []
    for r in uniq[:limit]:
        out.append({
            "report_date": r.get("report_date"), "ts_code": r.get("ts_code"), "name": r.get("name"),
            "title": r.get("report_title"), "org": r.get("org_name"), "author": r.get("author_name"),
            "report_type": r.get("report_type"), "classify": r.get("classify"),
            "rating": r.get("rating"), "rating_bucket": _rating_bucket(r.get("rating")),
        })
    # `count` is how many reports there were, not how many rows are returned:
    # a whole-market week runs to thousands, and reporting the cap as the count
    # ("300 reports in the last 7 days") understated it by an order of magnitude.
    return {"status": "ready", "days": days, "count": len(uniq), "reports": out,
            "truncated": len(uniq) > len(out), "cache_status": reports_status()}

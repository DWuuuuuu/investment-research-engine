"""CP3 — distribution / effort-vs-result counter.  DISPLAY ONLY.

Implements section 5 of research/market_top_spec.md (v3).  Read that first; the
short version is that this counts the days on which an index went down, or went
nowhere, on heavier volume than the session before — the footprint institutions
leave when they sell into a rally.

WHY THIS IS DISPLAY ONLY, AND MUST STAY THAT WAY UNTIL TWO THINGS PASS
----------------------------------------------------------------------
1. Volume semantics are now EVIDENCED, not merely plausible.  Yahoo's index
   Volume series carries the triple-witching fingerprint: on the 3rd Friday of
   Mar/Jun/Sep/Dec, volume runs 1.6-1.8x its own 50-day median against 1.00x on
   ordinary sessions, across 38 events on all three US indexes.  A synthetic or
   constituent-sum proxy would not reproduce that.  Half-day sessions show the
   mirror image.  See tests/test_cp3_data_integrity.py -- those tests are
   network-marked and skippable, but they are saved and rerunnable.

   This is a structural fingerprint, not a reconciliation against an
   exchange-published figure.  It is strong evidence the series is real tape
   volume; it is not proof the aggregation basis matches IBD's.

2. CP3 must never move exposure on its own.  Tested in isolation, distribution
   clusters were a NEGATIVE-edge sell signal: shorting them lost money and
   fading them made money.  The spec gates CP3 behind CP1/CP2, and neither of
   those is implementable on the current data stack.  So this module exposes a
   reading, not an instruction, and `advisory` is hard-coded True.

DESIGN NOTES THAT MATTER
------------------------
Cash indexes only.  SPY/QQQ/DIA volume includes ETF creation-redemption
activity, which is not directional flow.  ^DJI is excluded for a different
reason: its "volume" (~0.5bn) is a 30-name aggregate, not composite tape.

Completed bars only.  Volume accumulates through a session while price does
not, so an in-progress bar always reads as low volume — which here would
SUPPRESS a distribution flag and make a selling day look calm.  `_last_completed`
drops it.

Missing volume is None, never 0.  `market_context.index_history` writes
`f(vol, i) or 0`; a zero makes `V_t > V_{t-1}` false and silently suppresses the
flag.  Bars with unknown volume are classified "unknown" and reported, not
counted as clear.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# ---- parameters (spec section 13) ---------------------------------------
DIST_RET = -0.002          # SRC   close <= -0.2%
CHURN_RET = 0.002          # AUTHORED  |r| < 0.2% -- closes the v1 dead zone
CHURN_LOC = 0.5            # ADAPTED   close STRICTLY in the lower half (rho < 0.5)
DIST_EXPIRE_N = 25         # SRC   sessions
DIST_EXPIRE_PCT = 0.05     # SRC   index trades 5% above that day's close
DIST_WARN = 4              # SRC
CHURN_WARN = 3             # SRC   standalone stalling trigger
DIST_HARD = 6              # AUTHORED
DIVERGENCE_MIN = 3         # AUTHORED
CLMF_WINDOW = 65           # AUTHORED  ~13 weeks
EFF_MIN = 1.15             # AUTHORED
RES_FLAT = 0.03            # AUTHORED

_MIN_BARS = 2 * CLMF_WINDOW          # 130 -- effort/result needs two 65d blocks
VOL_MA_N = 25                        # SRC: the source's stalling-day volume bar

# Volume bar for BOTH dist and churn.  "prior_day" is the published rule for
# distribution days; the source uses SMA_25 for stalling only.  Unifying them is
# an AUTHORED choice (spec 5.1), so the alternative is a first-class parameter
# rather than a hard-coded assumption -- spec section 11 requires both variants
# to be carried through any evaluation.
VOL_BAR_MODES = ("prior_day", "sma25")
_VOL_BAR_DEFAULT = "prior_day"

INSTRUMENTS: list[dict] = [
    {"t": "^GSPC",      "name": "S&P 500",            "short": "SPX",  "role": "broad",
     "market": "us", "src": "yf"},
    {"t": "^IXIC",      "name": "Nasdaq Composite",   "short": "COMP", "role": "growth",
     "market": "us", "src": "yf"},
    {"t": "^NYA",       "name": "NYSE Composite",     "short": "NYA",  "role": "broad",
     "market": "us", "src": "yf"},
    # A-shares.  Tushare index_daily is fussy about the suffix: 000300 wants .SH,
    # 000985 wants .CSI (same quirk cn_breadth.py documents).  `vol` is in lots,
    # which is irrelevant here -- every test is a ratio.
    {"t": "000300.SH",  "name": "CSI 300",            "short": "CSI300", "role": "broad",
     "market": "cn", "src": "ts"},
    {"t": "000985.CSI", "name": "CSI All-Share",      "short": "CSIALL", "role": "broad",
     "market": "cn", "src": "ts"},
    {"t": "399006.SZ",  "name": "ChiNext",            "short": "CHINEXT", "role": "growth",
     "market": "cn", "src": "ts"},
]
_BY_T = {i["t"]: i for i in INSTRUMENTS}

_DATA = Path(__file__).resolve().parent.parent / "data" / "cp3_cache"
_TTL = 15 * 60
_NY = ZoneInfo("America/New_York")


# ---------------------------------------------------------------- helpers
def _last_completed(rows: list[dict], now: datetime | None = None) -> int:
    """Index of the last COMPLETED daily bar (spec 0.1, `T_d`).

    A bar dated today is in progress until the 16:00 ET close.  Returns -1 when
    nothing is complete.
    """
    if not rows:
        return -1
    now = (now or datetime.now(_NY)).astimezone(_NY)
    session_over = (now.hour, now.minute) >= (16, 0)
    today = now.date().isoformat()
    last = len(rows) - 1
    if rows[last]["d"] == today and not session_over:
        return last - 1
    return last


def _staleness(as_of: str, peer_latest: str | None) -> dict[str, Any]:
    """Freshness, judged against this index's PEERS rather than the calendar.

    A calendar test cannot tell a market holiday from a dropped session without
    an exchange calendar.  Peers can: if every index in a market ends on the same
    date, that is the market's last session (or a uniform source lag).  If ONE
    index lags its peers, that index is missing a session -- which silently
    shortens the 25-day window and understates the count, the same failure that
    once cost this project a Tushare trading day.

    Observed 2026-08-30: all three US indexes ended 08-27 while the CN indexes
    had 08-28, i.e. a uniform Yahoo lag, not a holiday and not a dropped bar.
    """
    if not peer_latest or as_of == peer_latest:
        return {"behind_peers": 0, "note": None}
    behind = 1 if as_of < peer_latest else 0
    return {"behind_peers": behind,
            "note": (f"last bar {as_of}, peers have {peer_latest} — this index is "
                     f"missing a session, which shortens the 25-day window")
            if behind else None}


def _loc(bar: dict) -> float | None:
    """rho_t -- where in the range the close landed.  0.5 when H == L."""
    h, l, c = bar.get("h"), bar.get("l"), bar.get("c")
    if h is None or l is None or c is None:
        return None
    if h <= l:
        return 0.5
    return (c - l) / (h - l)


def classify(prev: dict, cur: dict, vol_ref: float | None = None) -> str:
    """One bar -> 'dist' | 'churn' | 'clear' | 'unknown'  (spec 5.1).

    `vol_ref` is the bar volume must exceed.  None means use the prior session
    (the published distribution-day rule); pass SMA_25 to run the source's
    stalling-day bar instead.
    """
    pc, cc = prev.get("c"), cur.get("c")
    pv, cv = prev.get("v"), cur.get("v")
    if pc is None or cc is None or not pc:
        return "unknown"
    if cv is None:
        return "unknown"                       # never treat missing volume as clear
    ref = pv if vol_ref is None else vol_ref
    if ref is None:
        return "unknown"
    r = cc / pc - 1.0
    heavier = cv > ref
    if not heavier:
        return "clear"
    if r <= DIST_RET:
        return "dist"
    if abs(r) < CHURN_RET:
        rho = _loc(cur)
        # Strict `<`.  rho == 0.5 is a mid-range close -- neutral, in neither
        # half -- and it is also what the H == L guard returns, so `<=` would
        # classify every zero-range bar with rising volume as churn.
        if rho is not None and rho < CHURN_LOC:
            return "churn"
    return "clear"


def _alive(rows: list[dict], t: int, td: int) -> tuple[bool, str | None]:
    """Expiry, spec 5.1.  BOTH rules; dropping the 5% rule makes the count
    monotone in a choppy uptrend and the indicator permanently bearish."""
    if td - t >= DIST_EXPIRE_N:
        return False, "aged_out"
    ct = rows[t]["c"]
    for i in range(t + 1, td + 1):
        h = rows[i].get("h")
        ref = h if h is not None else rows[i].get("c")
        if ref is not None and ref >= ct * (1.0 + DIST_EXPIRE_PCT):
            return False, "rallied_5pct"
    return True, None


# ------------------------------------------------------------- evaluation
def _flag_series(rows: list[dict], n: int,
                 vol_bar: str = _VOL_BAR_DEFAULT) -> list[str | None]:
    """Classify every bar once.  O(n)."""
    ma = None
    if vol_bar == "sma25":
        ma, run, q = [None] * n, 0.0, []
        for i in range(n):
            v = rows[i].get("v")
            q.append(v if v else 0.0)
            run += q[-1]
            if len(q) > VOL_MA_N:
                run -= q.pop(0)
            ma[i] = (run / len(q)) if len(q) == VOL_MA_N else None
    out: list[str | None] = [None] * n
    for t in range(1, n):
        out[t] = classify(rows[t - 1], rows[t], None if ma is None else ma[t])
    return out


def _retire_index(rows: list[dict], n: int) -> list[int]:
    """For every bar t, the first index > t at which the series traded 5% above
    C_t.  Precomputed so the expiry test is O(1) per lookup instead of O(25).

    Only the 5% rule needs this; the 25-session rule is arithmetic.
    """
    out = [n] * n
    for t in range(n):
        ct = rows[t].get("c")
        if not ct:
            continue
        target = ct * (1.0 + DIST_EXPIRE_PCT)
        hi = n
        for i in range(t + 1, min(n, t + DIST_EXPIRE_N + 1)):
            ref = rows[i].get("h") or rows[i].get("c")
            if ref is not None and ref >= target:
                hi = i
                break
        out[t] = hi
    return out


def _d_series(rows: list[dict], n: int,
              vol_bar: str = _VOL_BAR_DEFAULT) -> tuple[list[int], list[int], list[str | None]]:
    """(D, D_churn) as they stood at EVERY bar.  Prefix-stable by construction:
    the value at index i uses only rows[:i+1]."""
    flags = _flag_series(rows, n, vol_bar)
    retire = _retire_index(rows, n)
    d_tot, d_ch = [0] * n, [0] * n
    for td in range(n):
        a = b = 0
        for t in range(max(1, td - DIST_EXPIRE_N + 1), td + 1):
            k = flags[t]
            if k not in ("dist", "churn"):
                continue
            if retire[t] <= td:                # rallied 5% above that close
                continue
            a += 1
            if k == "churn":
                b += 1
        d_tot[td], d_ch[td] = a, b
    return d_tot, d_ch, flags


def evaluate(rows: list[dict], now: datetime | None = None,
             vol_bar: str = _VOL_BAR_DEFAULT,
             panel_latest: str | None = None) -> dict[str, Any]:
    """Pure function: bars in, CP3 reading out.  No network, no cache.

    `rows` are ascending dicts {d,o,h,l,c,v}; `v` may be None.
    """
    out: dict[str, Any] = {"advisory": True, "exposure_effect": None}
    td = _last_completed(rows, now)
    if td < 1:
        return {**out, "state": "insufficient_history",
                "note": "no completed bars", "bars": len(rows)}
    if td + 1 < _MIN_BARS:
        return {**out, "state": "insufficient_history",
                "note": f"needs {_MIN_BARS} completed bars, has {td + 1}",
                "bars": td + 1}

    partial_dropped = td < len(rows) - 1

    # --- discrete flags: one pass over the whole series --------------------
    d_tot_s, d_ch_s, flags = _d_series(rows, td + 1, vol_bar)
    d_total, d_churn = d_tot_s[td], d_ch_s[td]

    events, unknown = [], 0
    for t in range(max(1, td - DIST_EXPIRE_N + 1), td + 1):
        kind = flags[t]
        if kind == "unknown":
            unknown += 1
            continue
        if kind in ("dist", "churn"):
            ok, why = _alive(rows, t, td)
            events.append({"d": rows[t]["d"], "kind": kind, "alive": ok, "retired": why,
                           "ret_pct": round((rows[t]["c"] / rows[t - 1]["c"] - 1) * 100, 2),
                           "rho": None if _loc(rows[t]) is None else round(_loc(rows[t]), 2),
                           "vol_ratio": round(rows[t]["v"] / rows[t - 1]["v"], 2)
                           if rows[t - 1].get("v") else None})

    # --- CLMF: close-location money flow (spec 5.2) -----------------------
    # NOT O'Neil's A/D Rating -- that formula is proprietary and unpublished.
    # This is Chaikin's close-location construction and is an experimental proxy.
    lo = td - CLMF_WINDOW + 1
    mf_sum = v_sum = 0.0
    clmf_missing = 0
    for i in range(lo, td + 1):
        rho, v = _loc(rows[i]), rows[i].get("v")
        if rho is None or v is None:
            clmf_missing += 1
            continue
        mf_sum += (2 * rho - 1) * v
        v_sum += v
    clmf = (mf_sum / v_sum) if v_sum > 0 else None

    # --- effort vs result (spec 5.2) --------------------------------------
    # SIGNED result.  v1 used |res|/eff, which rated a crash as the healthiest
    # possible reading and a quiet flat tape as churn.  Exactly inverted.
    rec = [rows[i]["v"] for i in range(lo, td + 1) if rows[i].get("v")]
    pri = [rows[i]["v"] for i in range(lo - CLMF_WINDOW, lo) if rows[i].get("v")]
    eff = (sum(rec) / len(rec)) / (sum(pri) / len(pri)) if rec and pri else None
    res = rows[td]["c"] / rows[td - CLMF_WINDOW + 1]["c"] - 1.0
    if eff is None:
        er_state = "insufficient_history"
    elif eff >= EFF_MIN and abs(res) <= RES_FLAT:
        er_state = "CHURN"
    elif res < -RES_FLAT:
        er_state = "DECLINE"
    else:
        er_state = "CLEAR"

    if d_total >= DIST_WARN * 2:
        level = "elevated"
    elif d_total >= DIST_HARD:
        level = "pressure"
    elif d_total >= DIST_WARN:
        level = "warning"
    else:
        level = "noise"

    fires = d_total >= DIST_WARN or d_churn >= CHURN_WARN or er_state == "CHURN"

    # Rank the count against this index's own behaviour.  The absolute
    # thresholds are the published ones and are kept, but on live data they fire
    # far too often to stand alone -- the percentile is the honest reading.
    hist = d_tot_s[DIST_EXPIRE_N + _MIN_BARS: td + 1]
    d_pctl = (sum(1 for x in hist if x <= d_total) / len(hist)) if hist else None
    d_median = sorted(hist)[len(hist) // 2] if hist else None
    unusual = d_pctl is not None and d_pctl >= 0.90

    stale = _staleness(rows[td]["d"], panel_latest)
    return {**out,
            "state": "ok",
            "as_of": rows[td]["d"],
            "behind_peers": stale["behind_peers"],
            "staleness_note": stale["note"],
            "partial_bar_dropped": partial_dropped,
            "bars": td + 1,
            "d_total": d_total, "d_churn": d_churn, "level": level, "fires": fires,
            "d_pctl": None if d_pctl is None else round(d_pctl, 3),
            "d_median_own_history": d_median,
            "d_history_n": len(hist),
            "d_history_from": rows[DIST_EXPIRE_N + _MIN_BARS]["d"] if hist else None,
            "vol_bar": vol_bar,
            "unusual_for_this_index": unusual,
            "unknown_volume_bars": unknown,
            "events": events,
            "clmf": None if clmf is None else round(clmf, 4),
            "clmf_missing_bars": clmf_missing,
            "effort": None if eff is None else round(eff, 3),
            "result_pct": round(res * 100, 2),
            "er_state": er_state,
            "thresholds": {"dist_warn": DIST_WARN, "churn_warn": CHURN_WARN,
                           "dist_hard": DIST_HARD, "expire_n": DIST_EXPIRE_N,
                           "expire_pct": DIST_EXPIRE_PCT, "eff_min": EFF_MIN,
                           "res_flat": RES_FLAT, "vol_bar": vol_bar}}


# ------------------------------------------------------------------ fetch
def _fetch_yf(symbol: str) -> dict[str, Any]:
    """Full available history.  ^GSPC carries volume from 1950, ^IXIC from 1984,
    ^NYA from 2001 -- so the percentile baseline spans many regimes rather than
    the single rising market a 2-year window gave.  Bars before volume exists are
    dropped, since every rule here needs it."""
    import yfinance as yf
    df = yf.Ticker(symbol).history(period="max", auto_adjust=True)
    if df is None or df.empty:
        return {"status": "error", "error": f"no data for {symbol}"}

    def g(row, k):
        v = row.get(k)
        return None if (v is None or v != v) else float(v)

    rows, seen_vol = [], False
    for ts, row in df.iterrows():
        c = g(row, "Close")
        if c is None:
            continue                               # gaps skipped, never filled
        v = g(row, "Volume")
        v = None if (v is None or v <= 0) else v    # 0 -> None, never "clear"
        if v is not None:
            seen_vol = True
        if not seen_vol:
            continue                               # pre-volume era is unusable
        rows.append({"d": ts.strftime("%Y-%m-%d"), "o": g(row, "Open"),
                     "h": g(row, "High"), "l": g(row, "Low"), "c": c, "v": v})
    return {"status": "ready", "symbol": symbol, "rows": rows}


def _fetch_ts(symbol: str, years: int = 12) -> dict[str, Any]:
    """A-share indexes via Tushare index_daily.  `vol` is in lots; irrelevant,
    every test here is a ratio."""
    from datetime import date
    from . import cn_data
    end = date.today().strftime("%Y%m%d")
    start = f"{date.today().year - years}0101"
    try:
        raw = cn_data.call("index_daily", {"ts_code": symbol, "start_date": start,
                                           "end_date": end},
                           "trade_date,open,high,low,close,vol")
    except Exception as exc:                       # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if not raw:
        return {"status": "error", "error": f"no data for {symbol}"}
    rows = []
    for r in sorted(raw, key=lambda x: x["trade_date"]):
        c = r.get("close")
        if c is None:
            continue
        v = r.get("vol")
        d = r["trade_date"]
        rows.append({"d": f"{d[:4]}-{d[4:6]}-{d[6:]}", "o": r.get("open"),
                     "h": r.get("high"), "l": r.get("low"), "c": float(c),
                     "v": None if (v is None or v <= 0) else float(v)})
    return {"status": "ready", "symbol": symbol, "rows": rows}


def _fetch(symbol: str) -> dict[str, Any]:
    meta = _BY_T.get(symbol) or {}
    cache = _DATA / (symbol.replace("^", "_").replace(".", "_") + ".json")
    if cache.exists() and (time.time() - cache.stat().st_mtime) < _TTL:
        try:
            return json.loads(cache.read_text())
        except Exception:                          # noqa: BLE001
            pass
    try:
        out = _fetch_ts(symbol) if meta.get("src") == "ts" else _fetch_yf(symbol)
    except Exception as exc:                       # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if out.get("status") != "ready":
        return out
    out["built_at"] = time.time()
    try:
        _DATA.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".partial")         # never truncate a live cache
        tmp.write_text(json.dumps(out))
        tmp.replace(cache)
    except Exception:                              # noqa: BLE001
        pass
    return out


def cp3_for(symbol: str, vol_bar: str = _VOL_BAR_DEFAULT,
            panel_latest: str | None = None) -> dict[str, Any]:
    meta = _BY_T.get(symbol)
    if not meta:
        return {"status": "error", "error": f"unknown index {symbol!r}"}
    raw = _fetch(symbol)
    if raw.get("status") != "ready":
        return {"status": "error", "state": "unavailable", "symbol": symbol,
                "name": meta["name"], "short": meta["short"], "market": meta["market"],
                "error": raw.get("error", "fetch failed")}
    r = evaluate(raw["rows"], vol_bar=vol_bar, panel_latest=panel_latest)
    return {"status": "ready", "symbol": symbol, "name": meta["name"],
            "short": meta["short"], "role": meta["role"], "market": meta["market"],
            **r}


def _latest_by_market() -> dict[str, str]:
    """Last completed bar per market, so staleness is judged against peers."""
    out: dict[str, str] = {}
    for meta in INSTRUMENTS:
        raw = _fetch(meta["t"])
        if raw.get("status") != "ready":
            continue
        rows = raw["rows"]
        td = _last_completed(rows)
        if td < 0:
            continue
        m = meta["market"]
        out[m] = max(out.get(m, ""), rows[td]["d"])
    return out


def cp3_all(vol_bar: str = _VOL_BAR_DEFAULT) -> dict[str, Any]:
    """Every tracked index, NEVER averaged -- disagreement is the information."""
    if vol_bar not in VOL_BAR_MODES:
        vol_bar = _VOL_BAR_DEFAULT
    latest = _latest_by_market()
    items = [cp3_for(i["t"], vol_bar, latest.get(i["market"])) for i in INSTRUMENTS]
    ok = {i["symbol"]: i for i in items if i.get("state") == "ok"}

    divergences = []
    for mkt, g_sym, b_sym in (("us", "^IXIC", "^NYA"), ("cn", "399006.SZ", "000300.SH")):
        g, b = ok.get(g_sym), ok.get(b_sym)
        if not (g and b):
            continue
        gap = g["d_total"] - b["d_total"]
        divergences.append({"market": mkt, "gap": gap, "fires": gap >= DIVERGENCE_MIN,
                            "growth": g["short"], "broad": b["short"]})

    hard = sum(1 for i in ok.values() if i["d_total"] >= DIST_HARD) >= 2
    return {"status": "ready", "built_at": time.time(),
            "advisory": True, "exposure_effect": None,
            "vol_bar": vol_bar, "vol_bar_modes": list(VOL_BAR_MODES),
            "items": items,
            "markets": [{"k": "us", "label": "US"}, {"k": "cn", "label": "China (A-Share)"}],
            "any_fires": any(i.get("fires") for i in ok.values()),
            "fires_hard": hard,
            "divergences": divergences,
            "divergence_note": "Growth distributing while the broad tape holds is the "
                               "classic topping sequence.",
            "caveats": [
                "DISPLAY ONLY — CP3 never moves exposure on its own. Tested in "
                "isolation, distribution clusters were a negative-edge sell signal.",
                "Index volume carries the triple-witching fingerprint (1.6–1.8× its own "
                "50-day median vs 1.00× on ordinary sessions, 38 events), which is strong "
                "evidence it is real tape volume — but it is not a reconciliation against "
                "an exchange-published figure, and the aggregation basis may still differ "
                "from IBD's.",
                "Cash indexes only. ETF volume includes creation/redemption flow; "
                "^DJI 'volume' is a 30-name aggregate, not composite tape.",
                "A-share volume is in lots rather than shares. Every test here is a ratio, "
                "so the unit does not matter.",
            ]}

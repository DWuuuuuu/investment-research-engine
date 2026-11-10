"""Universe-wide stock filter (bulk screener).

US universe + sector/industry/market-cap come from the NASDAQ screener API
(~7,000 names in one call); valuation fields (P/E, forward P/E, P/B, dividend
yield, EPS) come from the Yahoo batch-quote endpoint (chunked). The combined
snapshot is built lazily in a background thread and cached, so filter queries
run in-memory. A-share uses Tushare daily_basic (whole market in one call) +
stock_basic for industry — fast enough to build synchronously.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(v: Any) -> float | None:
    try:
        if v in (None, "", "N/A", "--"):
            return None
        if isinstance(v, str):
            v = v.replace("$", "").replace(",", "").replace("%", "").strip()
            if not v:
                return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _clip(v: float | None, lo: float, hi: float) -> float | None:
    return None if v is None else max(lo, min(hi, v))


_Q_PREV = {"0630": "0331", "0930": "0630", "1231": "0930"}


def _cn_discrete_quarters(inc_rows: list[dict], field: str) -> dict[str, float]:
    """A-share interim disclosures are CUMULATIVE-YTD, not discrete quarters
    (verified live: Moutai's n_income_attr_p went 26.85亿(Q1) -> 45.40亿(H1)
    -> 64.63亿(9M) -> 82.32亿(FY) -> resets to 27.24亿 at the next Q1).
    Differences consecutive same-year periods to recover discrete-quarter
    values for `field` (e.g. n_income_attr_p or total_revenue): Q1 is
    already discrete; Q2/Q3/Q4 = this period's cumulative minus the LITERAL
    immediately-prior same-year period, only when that exact predecessor is
    present — a missing report is skipped, never inferred across a gap.
    Returns {end_date: discrete_value} — callers needing two series aligned
    (e.g. margin = NI/revenue) should intersect keys, not zip independently
    sorted lists, since either series can have its own gaps."""
    by_period: dict[str, float] = {}
    for r in inc_rows:
        ed = r.get("end_date") or ""
        v = _to_float(r.get(field))
        if ed and v is not None and ed not in by_period:
            by_period[ed] = v
    discrete: dict[str, float] = {}
    for ed, cum in by_period.items():
        year, md = ed[:4], ed[4:]
        if md == "0331":
            discrete[ed] = cum
        elif md in _Q_PREV:
            prev_key = year + _Q_PREV[md]
            if prev_key in by_period:
                discrete[ed] = cum - by_period[prev_key]
    return discrete


# yfinance labels the net-income row differently across companies — some use
# bare "Net Income", others only have a more qualified variant. An exact
# match on "Net Income" alone silently dropped 30.8% of the US universe
# (1,772/5,759 tickers, e.g. AEE/Ameren), not just illiquid names. Tried in
# order of preference: bare "Net Income" first, then "...Common Stockholders"
# (matches the "attributable to shareholders" convention CN's n_income_attr_p
# uses), then the broader continuing-operations/including-NCI variants.
_NI_LABELS = ("Net Income", "Net Income Common Stockholders",
              "Net Income From Continuing Operation Net Minority Interest",
              "Net Income Including Noncontrolling Interests")
_REV_LABELS = ("Total Revenue", "Operating Revenue")


def _clean_nums(vals: list) -> list[float]:
    """_to_float + drop both None and NaN — a bare `v is not None` filter
    lets NaN through (NaN != None), which silently corrupts downstream
    growth-chain math (caught while investigating a UBER fetch anomaly:
    yfinance's Net Income series ends in a trailing NaN column every time)."""
    out = [_to_float(v) for v in vals]
    return [v for v in out if v is not None and v == v]


# MAGNET-lite ACCEL proxy winsorization band. Growth-RATE deltas (not the
# spec's log-growth-off-levels form) blow up without bound near a small or
# negative earnings base — e.g. a stock going from -¥0.1M to +¥1.9M profit
# shows as "+1900% growth," a base-effect artifact, not genuine acceleration.
# Caught live 2026-08-13: accel_min=50 on CN returned 1,200/4,537 stocks
# (26% of the universe), the top matches all loss-making micro-caps with
# quadruple-digit accel values (one hit 1883.9pp) and deeply negative ROE —
# exactly the population the spec's §1.1 turnaround exclusion exists to gate
# out before ACCEL is even computed, which this proxy had skipped entirely.
_ACCEL_CLIP = 150.0


def _percentile_ranks(rows: list[dict], field: str) -> dict[str, float]:
    """MAGNET-lite Phase 2 (§2/§3 of the spec): 'percentiles, not raw z-scores
    — more robust to fat-tailed accounting ratios.' Computed ONLY among rows
    that actually have a value for this field — a stock the profitability/PE
    gate excluded isn't ranked at the bottom, it's simply not ranked (None),
    same as the rest of this codebase's 'don't claim what you can't support'
    convention. 100 = highest raw value, 0 = lowest, ties get the same rank
    (average-rank method, so a cluster of identical values doesn't arbitrarily
    favor whichever happened to sort first)."""
    have = [(r["symbol"], r[field]) for r in rows if r.get(field) is not None]
    if len(have) < 2:
        return {}
    have.sort(key=lambda x: x[1])
    n = len(have)
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and have[j + 1][1] == have[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = round(avg_rank / (n - 1) * 100, 1) if n > 1 else 100.0
        for k in range(i, j + 1):
            out[have[k][0]] = pct
        i = j + 1
    return out


# ══════════════════════════════════════════════════════════════════════════════
# US universe
# ══════════════════════════════════════════════════════════════════════════════

_NASDAQ_URL = ("https://api.nasdaq.com/api/screener/stocks"
               "?tableonly=true&limit=10000&offset=0&download=true")
_NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

_QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
_QUOTE_FIELDS = (
    "symbol,trailingPE,forwardPE,priceToBook,dividendYield,"
    "epsTrailingTwelveMonths,marketCap,regularMarketPrice,"
    "fiftyTwoWeekHighChangePercent,fiftyTwoWeekChangePercent,averageDailyVolume3Month,"
    # no true 1m/3m point-to-point return in this bulk endpoint — these are the
    # closest free proxies (price vs its own N-day moving average, not vs price
    # N days ago). Same request, no extra API calls.
    "fiftyDayAverageChangePercent,twoHundredDayAverageChangePercent"
)

_US: dict[str, Any] = {
    "ts": 0.0, "rows": [], "meta_ready": False, "ready": False,
    "building": False, "progress": 0, "total": 0, "error": None,
}
_US_TTL = 6 * 3600           # 6 hours
_LOCK = threading.Lock()


# Instruments that are not common equity. Deliberately does NOT match a bare
# "depositary" — "American Depositary Shares" is an ordinary ADR.
_DEBT_RE = re.compile(
    r"\bnotes?\s+(due|of)\b|\bsenior\s+notes?\b|\bsubordinated\b|\bdebenture"
    r"|\bpreferred\b|\bpfd\b|\bcorts\b|\bbaby\s+bond"
    r"|\btrust\s+(certificate|securities|ctf)|\bcapital\s+trust\b"
    r"|\d+(\.\d+)?\s*%|\bdue\s+20\d\d",
    re.I)
_ADS_RE = re.compile(r"american\s+depositary", re.I)


def _fetch_nasdaq_universe() -> list[dict]:
    req = urllib.request.Request(_NASDAQ_URL, headers=_NASDAQ_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    rows = (data.get("data") or {}).get("rows") or []
    out: list[dict] = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        sector = (r.get("sector") or "").strip()
        # blank sector ≈ ETF/fund/warrant/test issue; odd symbols aren't common stock
        if not sym or not sector:
            continue
        if any(c in sym for c in ("^", "/", "~", ".", "$")):
            continue
        # drop warrants / units / rights (5-char tickers ending W, U, or R)
        if len(sym) == 5 and sym[-1] in ("W", "U", "R"):
            continue
        nm_l = (r.get("name") or "").lower()
        if " fund" in nm_l or "etf" in nm_l:        # closed-end funds / ETFs aren't stocks
            continue
        # NOT COMMON EQUITY. The NASDAQ screener carries baby bonds, preferred
        # shares, trust certificates and pre-deal SPAC shells alongside real
        # stocks. They pass the sector/ticker checks above but are meaningless
        # to analyse: a $25-par note oscillating in a $24-26 band trivially
        # reads as a rising 30-week average, so Stage 2 screens filled up with
        # things like "Adamas Trust 9.125% Senior Notes Due 2030".
        #
        # Two rules, in this order:
        #   1. An ADR WITH a market cap is ordinary equity and is kept even when
        #      its name says "Preferred" — ITUB and CIB are ADRs over the
        #      issuer's preferred shares and are perfectly real $24-82bn stocks.
        #   2. Otherwise drop anything named like debt/preferred, and drop
        #      anything with NO market cap at all. The second rule is what makes
        #      this robust: shells such as "Aperture AC" or "BTC Development
        #      Corp" carry no give-away word, but nothing without a market cap
        #      can be screened as equity anyway.
        _cap = _to_float(r.get("marketCap"))
        if not (_ADS_RE.search(nm_l) and _cap):
            if _DEBT_RE.search(nm_l) or not _cap:
                continue
        out.append({
            "symbol":     sym,
            "name":       (r.get("name") or sym).replace(" Common Stock", "").strip(),
            "sector":     sector,
            "industry":   (r.get("industry") or "").strip(),
            "market_cap": _to_float(r.get("marketCap")),
            "price":      _to_float(r.get("lastsale")),
            "country":    (r.get("country") or "").strip(),
            # valuation fields populated by _enrich_valuation
            "pe": None, "fpe": None, "pb": None, "div_yield": None,
            "eps": None, "off_52w_high": None, "avg_vol": None,
            "impl_growth": None, "peg": None, "ps": None, "mom_52w": None,
            "mom_50d": None, "mom_200d": None,
        })
    return out


def _enrich_valuation(rows: list[dict]) -> None:
    from yfinance.data import YfData
    yd = YfData()
    by_sym = {r["symbol"]: r for r in rows}
    syms = list(by_sym)
    total = len(syms)
    for i in range(0, total, 100):
        chunk = syms[i:i + 100]
        try:
            resp = yd.get(_QUOTE_URL, params={"symbols": ",".join(chunk), "fields": _QUOTE_FIELDS})
            for x in resp.json().get("quoteResponse", {}).get("result", []):
                row = by_sym.get((x.get("symbol") or "").upper())
                if not row:
                    continue
                row["pe"]        = _to_float(x.get("trailingPE"))
                row["fpe"]       = _to_float(x.get("forwardPE"))
                row["pb"]        = _to_float(x.get("priceToBook"))
                row["div_yield"] = _to_float(x.get("dividendYield"))          # already %
                row["eps"]       = _to_float(x.get("epsTrailingTwelveMonths"))
                mc = _to_float(x.get("marketCap"))
                if mc:
                    row["market_cap"] = mc
                rp = _to_float(x.get("regularMarketPrice"))
                if rp:
                    row["price"] = rp
                ch = _to_float(x.get("fiftyTwoWeekHighChangePercent"))
                row["off_52w_high"] = round(ch * 100, 1) if ch is not None else None
                row["avg_vol"]   = _to_float(x.get("averageDailyVolume3Month"))
                mom = _to_float(x.get("fiftyTwoWeekChangePercent"))   # 52-wk total return %
                row["mom_52w"] = round(mom, 1) if mom is not None else None
                # both are fractions (like fiftyTwoWeekHighChangePercent above), not %
                m50 = _to_float(x.get("fiftyDayAverageChangePercent"))
                row["mom_50d"] = round(m50 * 100, 1) if m50 is not None else None
                m200 = _to_float(x.get("twoHundredDayAverageChangePercent"))
                row["mom_200d"] = round(m200 * 100, 1) if m200 is not None else None
                # implied forward EPS growth = trailingPE/forwardPE − 1 (both must be +)
                pe, fpe = row["pe"], row["fpe"]
                if pe and fpe and pe > 0 and fpe > 0:
                    ig = (pe / fpe - 1) * 100
                    row["impl_growth"] = round(ig, 1)
                    row["peg"] = round(pe / ig, 2) if ig > 0 else None
        except Exception:
            pass
        _US["progress"] = min(i + 100, total)
        time.sleep(0.05)


def _build_us() -> None:
    try:
        rows = _fetch_nasdaq_universe()
        with _LOCK:
            _US["rows"] = rows
            _US["meta_ready"] = True
            _US["total"] = len(rows)
            _US["progress"] = 0
        _enrich_valuation(rows)
        _US["ts"] = time.time()
        _US["ready"] = True
    except Exception as exc:                       # noqa: BLE001
        _US["error"] = str(exc)
    finally:
        _US["building"] = False


def ensure_us_snapshot() -> None:
    """Kick off a background build if the snapshot is stale/missing."""
    now = time.time()
    with _LOCK:
        if _US["building"]:
            return
        if _US["ready"] and (now - _US["ts"] < _US_TTL):
            return
        _US["building"] = True
        _US["ready"] = False
        _US["meta_ready"] = False
        _US["error"] = None
        _US["progress"] = 0
    threading.Thread(target=_build_us, daemon=True).start()


def us_status() -> dict:
    return {
        "meta_ready": _US["meta_ready"],
        "ready":      _US["ready"],
        "building":   _US["building"],
        "progress":   _US["progress"],
        "total":      _US["total"],
        "universe":   len(_US["rows"]),
        "error":      _US["error"],
        "built_at":   _US["ts"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# A-share universe (Tushare)
# ══════════════════════════════════════════════════════════════════════════════

_CN: dict[str, Any] = {"ts": 0.0, "rows": [], "date": None}
_CN_TTL = 3600            # 1 hour (EOD data)


def _is_main_a_share(ts_code: str) -> bool:
    """Main-board / STAR / ChiNext A-shares only — excludes B-shares (900/200) & BJ."""
    if not ts_code or "." not in ts_code:
        return False
    sym, ex = ts_code.split(".")
    if ex == "SH":
        return sym[:3] in ("600", "601", "603", "605", "688")
    if ex == "SZ":
        return sym[:3] in ("000", "001", "002", "003", "300")
    return False


def _build_cn_snapshot() -> None:
    from . import cn_data
    # latest trade date from a liquid name
    ref = cn_data.latest_row("daily_basic", "000001.SZ", "ts_code,trade_date,pe_ttm")
    td = ref.get("trade_date") if ref else None
    if not td:
        return
    db = cn_data.call(
        "daily_basic", {"trade_date": td},
        "ts_code,close,pe_ttm,pb,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv,turnover_rate",
    )
    # momentum: whole-market close at several lookbacks (each a single bulk call —
    # Tushare's daily_basic snapshots the WHOLE market at one past date per call,
    # so adding more horizons costs a few extra calls total, not one per stock).
    from datetime import datetime, timedelta
    d0 = datetime.strptime(td, "%Y%m%d")

    def _price_map_at(back_days: int, search_span: int = 8) -> dict[str, float]:
        for back in range(back_days, back_days + search_span):
            rows_ago = cn_data.call("daily_basic",
                                    {"trade_date": (d0 - timedelta(days=back)).strftime("%Y%m%d")},
                                    "ts_code,close")
            if rows_ago:
                return {r["ts_code"]: _to_float(r.get("close")) for r in rows_ago}
        return {}

    price_1m  = _price_map_at(30)
    price_3m  = _price_map_at(91)
    price_6m  = _price_map_at(182)
    price_ago = _price_map_at(365)
    basic = {b["ts_code"]: b for b in cn_data._stock_basic_list()}
    rows: list[dict] = []
    for r in db:
        ts = r.get("ts_code")
        if not _is_main_a_share(ts):
            continue
        b = basic.get(ts) or {}
        nm = b.get("name") or ""
        if nm.startswith("ST") or nm.startswith("*ST") or nm.startswith("*") or "退" in nm:
            continue                                   # ST/distressed/delisting
        mv = _to_float(r.get("total_mv"))
        close = _to_float(r.get("close"))

        def _mom(price_map: dict) -> float | None:
            p_ago = price_map.get(ts)
            return round((close / p_ago - 1) * 100, 1) if (close and p_ago and p_ago > 0) else None

        mom = _mom(price_ago)
        rows.append({
            "symbol":     ts,
            "name":       b.get("name") or ts,
            "sector":     b.get("industry") or "",
            "industry":   b.get("industry") or "",
            "area":       b.get("area") or "",
            "market_cap": mv * 10_000 if mv else None,        # 万元 → 元
            "price":      close,
            "pe":         _to_float(r.get("pe_ttm")),
            "fpe":        None,
            "pb":         _to_float(r.get("pb")),
            "ps":         _to_float(r.get("ps_ttm")),
            # dv_ratio = latest annual dividend yield; dv_ttm double-counts when a
            # firm adds a mid-year interim dividend, so prefer dv_ratio.
            "div_yield":  _to_float(r.get("dv_ratio")) if r.get("dv_ratio") not in (None, "") else _to_float(r.get("dv_ttm")),
            "eps":        None,
            "turnover":   _to_float(r.get("turnover_rate")),
            "mom_52w":    mom,
            "mom_1m":     _mom(price_1m),
            "mom_3m":     _mom(price_3m),
            "mom_6m":     _mom(price_6m),
            "off_52w_high": None, "avg_vol": None,
        })
    _CN["rows"] = rows
    _CN["ts"] = time.time()
    _CN["date"] = td


def ensure_cn_snapshot() -> None:
    now = time.time()
    if _CN["rows"] and now - _CN["ts"] < _CN_TTL:
        return
    _build_cn_snapshot()


# ── ROE cache (whole A-share universe) ────────────────────────────────────────
# Tushare's bulk "whole market in one call" fundamentals endpoint
# (fina_indicator_vip) needs a paid tier this account doesn't have, so ROE has
# to come from the per-symbol fina_indicator call ashare_screener.py already
# uses for its shortlist — too slow to run inline per request (~5,600 names).
# Instead we background-build it once and cache it; ROE only changes quarterly
# so a stale-for-a-few-days cache is fine.
_CN_ROE: dict[str, Any] = {"ts": 0.0, "map": {}, "building": False, "progress": 0, "total": 0,
                            "disk_checked": False}
_CN_ROE_TTL = 7 * 24 * 3600  # 1 week
_CN_ROE_CACHE_FILE = _DATA_DIR / "cn_roe_cache.json"


def _load_cn_roe_from_disk() -> None:
    """Load a previously-built cache from disk, if present — this is what makes
    the cache survive a server restart instead of rebuilding from empty every
    time (previously in-memory only, so the 7-day TTL never actually applied in
    practice across dev restarts). Runs once per process; safe to fail quietly
    (missing/corrupt file just means "build from scratch", same as before)."""
    try:
        if _CN_ROE_CACHE_FILE.exists():
            data = json.loads(_CN_ROE_CACHE_FILE.read_text())
            if isinstance(data, dict) and isinstance(data.get("map"), dict):
                _CN_ROE["map"] = data["map"]
                _CN_ROE["ts"] = float(data.get("ts") or 0)
    except Exception:
        pass


def _save_cn_roe_to_disk() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CN_ROE_CACHE_FILE.write_text(json.dumps({"ts": _CN_ROE["ts"], "map": _CN_ROE["map"]}))
    except Exception:
        pass   # cache still works in-memory for this process even if the disk write fails


_CN_ROE_LOOKBACK_YEARS = 5


def _fetch_cn_roe_one(ts_code: str) -> dict | None:
    """Per-stock fundamentals for the whole-universe background cache: ROE (latest +
    5-yr annual minimum), latest-year revenue growth, latest-year operating profit,
    and net-profit-growth consistency (min YoY growth over the trailing 3/4 annual
    reports). All four share one fina_indicator call (wide date range, Tushare
    returns every report in range in one shot — free) except operating profit,
    which needs a second call to the `income` statement endpoint since
    fina_indicator only carries ratios, not raw income-statement figures — that
    one doubles the per-ticker cost, the rest are free additions to the same call."""
    from datetime import date, timedelta
    from . import cn_data
    start = (date.today() - timedelta(days=365 * _CN_ROE_LOOKBACK_YEARS + 90)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    try:
        rows = cn_data.call(
            "fina_indicator", {"ts_code": ts_code, "start_date": start, "end_date": end},
            "ts_code,end_date,roe,or_yoy,netprofit_yoy",
        )
    except Exception:
        return None
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("end_date", ""), reverse=True)
    annual = [r for r in rows if (r.get("end_date") or "").endswith("1231")]
    latest_row = annual[0] if annual else rows[0]
    latest_val = _to_float(latest_row.get("roe"))
    if latest_val is None:
        return None
    annual_vals = [v for v in (_to_float(r.get("roe")) for r in annual[:_CN_ROE_LOOKBACK_YEARS]) if v is not None]
    # only claim a 5-yr-consistent figure once we actually have 5 annual reports —
    # a recent IPO or one with gaps shouldn't silently pass a 5-yr screen
    min_5y = min(annual_vals) if len(annual_vals) >= _CN_ROE_LOOKBACK_YEARS else None
    rev_growth = _to_float(latest_row.get("or_yoy"))

    # net-profit YoY growth consistency — same "only claim it with full data" rule
    # as ROE-5Y above. netprofit_yoy is already a growth RATE per annual report
    # (not a level), so N years of reports directly give N growth-consistency
    # points (unlike a raw-level series, which needs N+1 points for N deltas).
    npg_vals = [v for v in (_to_float(r.get("netprofit_yoy")) for r in annual[:_CN_ROE_LOOKBACK_YEARS]) if v is not None]
    earngrowth_min_3y = min(npg_vals[:3]) if len(npg_vals) >= 3 else None
    earngrowth_min_4y = min(npg_vals[:4]) if len(npg_vals) >= 4 else None

    # Quarterly-cadence consistency: unlike the annual-only check above, this uses
    # EVERY reported period (Q1/H1/9M/FY, e.g. 0331/0630/0930/1231) at whatever
    # cadence the company actually files. netprofit_yoy on an interim row is
    # already Tushare's clean YoY comparison (e.g. H1'26 cumulative vs H1'25
    # cumulative) — NOT a discrete single-quarter figure requiring differencing,
    # since A-share interim disclosures are cumulative-YTD by convention. So
    # "past N reports" mixes report types (a recent 4 could be Q1/FY/9M/H1) but
    # each entry is individually apples-to-apples YoY — just not 4 discrete
    # quarters in the US GAAP sense.
    dedup: dict[str, dict] = {}
    for r in rows:
        ed = r.get("end_date") or ""
        if ed and ed not in dedup:
            dedup[ed] = r
    quarterly = [dedup[ed] for ed in sorted(dedup, reverse=True)]
    qpg_vals = [v for v in (_to_float(r.get("netprofit_yoy")) for r in quarterly) if v is not None]
    earngrowth_min_q4 = min(qpg_vals[:4]) if len(qpg_vals) >= 4 else None
    earngrowth_min_q8 = min(qpg_vals[:8]) if len(qpg_vals) >= 8 else None

    operate_profit = None
    turnaround_accel = False
    turnaround_accel_q = False
    margin_turnaround_accel = False
    margin_turnaround_accel_q = False
    revenue_accel = False
    revenue_accel_q = False
    profit_accel = False
    profit_accel_q = False
    margin_accel = False
    margin_accel_q = False
    try:
        inc_rows = cn_data.call(
            "income", {"ts_code": ts_code, "start_date": start, "end_date": end},
            "ts_code,end_date,operate_profit,n_income_attr_p,total_revenue",
        )
        inc_dedup: dict[str, dict] = {}
        for r in inc_rows:
            ed = r.get("end_date") or ""
            if ed.endswith("1231") and ed not in inc_dedup:
                inc_dedup[ed] = r
        inc_annual = [inc_dedup[ed] for ed in sorted(inc_dedup, reverse=True)]
        if inc_annual:
            operate_profit = _to_float(inc_annual[0].get("operate_profit"))
        # Turnaround-then-accelerate (§1.1's TURNAROUND sleeve, kept SEPARATE
        # from ACCEL, per user request 2026-08-13): loss two years ago, turned
        # profitable last year, grew further this year. Confirmed with real
        # examples (US side) before building — CRDO, RIGL, ACAD etc. all show
        # this exact shape and are genuine strong-momentum names, not noise.
        # Uses raw n_income_attr_p levels (not netprofit_yoy ratios, which
        # can't reliably signal a sign flip), from the same `income` call
        # already made for operate_profit — no extra API cost.
        # Tightened 2026-08-13 (user-specified): N(t)-N(t-1) > N(t-1)-N(t-2)
        # — the latest improvement must exceed the initial turnaround swing
        # itself, not just be positive. Stricter than "still growing":
        # excludes a turnaround that's decelerating even while still
        # technically growing (e.g. a name whose swing out of the loss was
        # +1.45 but only improved +1.10 the period after — real CN example
        # that qualified under the old rule, correctly excluded under this one).
        ni_vals = [_to_float(r.get("n_income_attr_p")) for r in inc_annual[:3]]
        if len(ni_vals) == 3 and None not in ni_vals:
            t, t1, t2 = ni_vals
            turnaround_accel = (t2 < 0) and (t1 > 0) and (t - t1 > t1 - t2)
            # Profit acceleration, no turnaround (2026-08-13, user request):
            # same accelerating shape, but for a company that was NEVER in a
            # loss over this window — all three points positive. A separate,
            # non-overlapping case from turnaround_accel above (that one
            # requires t-2 < 0; this one requires all three > 0 — mutually
            # exclusive by construction, same as ACCEL vs turnaround_accel).
            #
            # Bug found and fixed 2026-08-13, same day: "all three positive"
            # is not enough — the second-difference test (t-t1 > t1-t2) is
            # satisfied just as well by a DECELERATING DECLINE as by genuine
            # acceleration (e.g. NI -¥266.6M then -¥4.8M YoY is still
            # shrinking every year, but the shrink itself is smaller, so the
            # inequality holds). Caught live on 002242.SZ (Joyoung): NI
            # ¥389.0M -> ¥122.4M -> ¥117.6M, revenue also declining every
            # year, yet this flagged True. Now also requires genuine
            # period-over-period growth (t > t1 AND t1 > t2), not just
            # positive levels — matches ACCEL's own growth-rate-positive gate.
            # Boundary fixed 2026-08-13 (user-specified): the gate on the
            # OLDEST point is t2 >= 0, not t2 > 0 — a company with exactly
            # zero profit two periods ago belongs in this "non-negative"
            # bucket, not excluded from both this and the turnaround bucket
            # (which requires t2 < 0 strictly). t1/t are left as > 0 for
            # clarity but are actually implied once t2>=0 and t>t1>t2 hold.
            profit_accel = (t2 >= 0) and (t1 > 0) and (t > 0)
            profit_accel = profit_accel and (t > t1) and (t1 > t2) and (t - t1 > t1 - t2)

        # Revenue acceleration (2026-08-13, corrected per user: revenue is
        # essentially never negative, so unlike NI/margin there's no
        # turnaround/sign-flip gate at all — just a direct acceleration
        # check on the LEVEL itself: R(t)-R(t-1) > R(t-1)-R(t-2). Same
        # decelerating-decline bug/fix as profit_accel above — also requires
        # R(t) > R(t-1) > R(t-2), not just a smaller decline. Explicit
        # R(t-2) >= 0 gate added (user-specified) — revenue is virtually
        # never negative in practice, but this makes that an explicit check
        # rather than an unstated assumption.
        rev_vals = [_to_float(r.get("total_revenue")) for r in inc_annual[:3]]
        revenue_accel = False
        if len(rev_vals) == 3 and None not in rev_vals:
            rv, rv1, rv2 = rev_vals
            revenue_accel = (rv2 >= 0) and (rv > rv1) and (rv1 > rv2) and (rv - rv1 > rv1 - rv2)

        # Margin turnaround-then-accelerate. Unlike revenue, margin genuinely
        # can go negative (a net loss), so the LEVEL-based turnaround formula
        # (with the sign-flip gate) applies directly, same as NI. NI and
        # revenue pulled from the SAME row per period (inc_annual), so
        # guaranteed period-aligned. Overlaps heavily with turnaround_accel
        # by construction (margin's sign tracks NI's sign whenever revenue
        # is positive) — the incremental value is the percentage-point-
        # normalized acceleration reading, not a different qualifying set.
        margin_vals: list[float | None] = []
        for r in inc_annual[:3]:
            ni_r = _to_float(r.get("n_income_attr_p"))
            rev_r = _to_float(r.get("total_revenue"))
            margin_vals.append(ni_r / rev_r * 100 if (ni_r is not None and rev_r) else None)
        if len(margin_vals) == 3 and None not in margin_vals:
            m, m1, m2 = margin_vals
            margin_turnaround_accel = (m2 < 0) and (m1 > 0) and (m - m1 > m1 - m2)
            # Margin acceleration, no turnaround — same "never negative in
            # this window" parallel as profit_accel above, and same
            # decelerating-decline fix: also requires m > m1 > m2, not just
            # positive levels with a shrinking decline.
            margin_accel = (m2 >= 0) and (m1 > 0) and (m > 0)
            margin_accel = margin_accel and (m > m1) and (m1 > m2) and (m - m1 > m1 - m2)

        # Quarterly versions of all three (2026-08-13, user request) — reuse
        # inc_rows already fetched above, no extra API cost. See
        # _cn_discrete_quarters for the cumulative-YTD differencing logic.
        dq_ni = _cn_discrete_quarters(inc_rows, "n_income_attr_p")
        dq_rev = _cn_discrete_quarters(inc_rows, "total_revenue")
        ni_q_sorted = [dq_ni[k] for k in sorted(dq_ni)]
        if len(ni_q_sorted) >= 3:
            qt, qt1, qt2 = ni_q_sorted[-1], ni_q_sorted[-2], ni_q_sorted[-3]
            turnaround_accel_q = (qt2 < 0) and (qt1 > 0) and (qt - qt1 > qt1 - qt2)
            profit_accel_q = (qt2 >= 0) and (qt1 > 0) and (qt > 0)
            profit_accel_q = profit_accel_q and (qt > qt1) and (qt1 > qt2) and (qt - qt1 > qt1 - qt2)
        rev_q_sorted = [dq_rev[k] for k in sorted(dq_rev)]
        if len(rev_q_sorted) >= 3:
            qr, qr1, qr2 = rev_q_sorted[-1], rev_q_sorted[-2], rev_q_sorted[-3]
            revenue_accel_q = (qr2 >= 0) and (qr > qr1) and (qr1 > qr2) and (qr - qr1 > qr1 - qr2)
        # margin needs NI and revenue from the SAME quarter — intersect keys,
        # never zip two independently-sorted lists (either can have gaps).
        shared_keys = sorted(k for k in dq_ni if k in dq_rev and dq_rev[k])
        margin_q_sorted = [dq_ni[k] / dq_rev[k] * 100 for k in shared_keys]
        if len(margin_q_sorted) >= 3:
            qm, qm1, qm2 = margin_q_sorted[-1], margin_q_sorted[-2], margin_q_sorted[-3]
            margin_turnaround_accel_q = (qm2 < 0) and (qm1 > 0) and (qm - qm1 > qm1 - qm2)
            margin_accel_q = (qm2 >= 0) and (qm1 > 0) and (qm > 0)
            margin_accel_q = margin_accel_q and (qm > qm1) and (qm1 > qm2) and (qm - qm1 > qm1 - qm2)
    except Exception:
        pass

    # MAGNET-lite ACCEL proxy (§2.1 of MAGNET-A_US_Strategy_Spec_v1.md).
    # Phase-1 fix (2026-08-13): gated on current profitability (operate_profit
    # > 0) — a 1-point proxy for the spec's 3-point turnaround exclusion
    # (t, t-4q, t-8q all positive), and winsorized — see _ACCEL_CLIP's comment
    # for why unbounded deltas are meaningless here.
    #
    # Second fix, same day: profitability alone wasn't enough. A stock with a
    # bad year followed by a partial rebound (e.g. NI -70% then +115%) scores
    # a huge ACCEL purely from recovering off its own decline, even while its
    # earnings LEVEL is still well below where it was two years ago — caught
    # live on LPG (Dorian LPG): NI $307.4M -> $90.2M (-70.7%) -> $193.7M
    # (+114.7%), ACCEL=+185pp (clipped to 150), despite FY26 profit still
    # being ~37% BELOW FY24. Rewarding "recovered from a bad year" is the
    # opposite of what an acceleration signal should do. Now requires BOTH of
    # the last two YoY growth rates to be positive — genuine consecutive
    # growth, not a decline-then-rebound pattern — before ACCEL is computed
    # at all.
    accel_proxy = None
    if (len(npg_vals) >= 2 and operate_profit is not None and operate_profit > 0
            and npg_vals[0] > 0 and npg_vals[1] > 0):
        accel_proxy = _clip(round(npg_vals[0] - npg_vals[1], 1), -_ACCEL_CLIP, _ACCEL_CLIP)

    return {
        "latest": latest_val, "min_5y": min_5y, "years": len(annual_vals),
        "rev_growth": rev_growth, "operate_profit": operate_profit,
        "earngrowth_min_3y": earngrowth_min_3y, "earngrowth_min_4y": earngrowth_min_4y,
        "earngrowth_min_q4": earngrowth_min_q4, "earngrowth_min_q8": earngrowth_min_q8,
        "accel_proxy": accel_proxy, "turnaround_accel": turnaround_accel,
        "turnaround_accel_q": turnaround_accel_q,
        "revenue_accel": revenue_accel, "revenue_accel_q": revenue_accel_q,
        "margin_turnaround_accel": margin_turnaround_accel,
        "margin_turnaround_accel_q": margin_turnaround_accel_q,
        "profit_accel": profit_accel, "profit_accel_q": profit_accel_q,
        "margin_accel": margin_accel, "margin_accel_q": margin_accel_q,
    }


def _build_cn_roe() -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        tickers = [r["symbol"] for r in _CN["rows"]]
        _CN_ROE["total"] = len(tickers)
        _CN_ROE["progress"] = 0
        result: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_fetch_cn_roe_one, ts): ts for ts in tickers}
            for fut in as_completed(futs):
                ts = futs[fut]
                try:
                    val = fut.result()
                except Exception:
                    val = None
                if val is not None:
                    result[ts] = val
                _CN_ROE["progress"] += 1
        _CN_ROE["map"] = result
        _CN_ROE["ts"] = time.time()
        _save_cn_roe_to_disk()
    finally:
        _CN_ROE["building"] = False


def ensure_cn_roe_cache() -> None:
    if not _CN["rows"]:
        return  # need the price snapshot's ticker list first
    if not _CN_ROE["disk_checked"]:
        _CN_ROE["disk_checked"] = True
        if not _CN_ROE["map"]:
            _load_cn_roe_from_disk()
    now = time.time()
    if _CN_ROE["building"]:
        return
    if _CN_ROE["map"] and (now - _CN_ROE["ts"] < _CN_ROE_TTL):
        return
    _CN_ROE["building"] = True
    threading.Thread(target=_build_cn_roe, daemon=True).start()


def cn_roe_status() -> dict:
    return {
        "building": _CN_ROE["building"],
        "progress": _CN_ROE["progress"],
        "total": _CN_ROE["total"],
        "ready": bool(_CN_ROE["map"]),
        "built_at": _CN_ROE["ts"],
    }
# ══════════════════════════════════════════════════════════════════════════════
# US institutional/insider ownership cache — same problem class as CN ROE: not
# in the bulk quote endpoint (confirmed live — none of institutionsPercentHeld/
# insidersPercentHeld/institutionsFloatPercentHeld/institutionsCount come back
# from the quoteResponse fields, matching the CN Filter-vs-Rank asymmetry), only
# via yfinance's per-symbol major_holders/institutional_holders (~3s/ticker, one
# fetch covers both — institutional_holders comes back instantly once
# major_holders has been read, confirmed live). Disk-persisted from day one
# (unlike the CN ROE cache's first version, which was in-memory-only until a
# later fix) — a ~6,400-ticker build takes on the order of an hour even with
# concurrent workers, so surviving restarts matters even more here than for ROE.
# 21-day TTL — much longer than ROE's 7 days — because 13F filings are quarterly
# with up to a 45-day lag; refreshing weekly would just re-fetch the same numbers.
_US_OWN: dict[str, Any] = {"ts": 0.0, "map": {}, "building": False, "progress": 0, "total": 0,
                           "disk_checked": False}
_US_OWN_TTL = 21 * 24 * 3600
_US_OWN_CACHE_FILE = _DATA_DIR / "us_ownership_cache.json"


def _load_us_own_from_disk() -> None:
    try:
        if _US_OWN_CACHE_FILE.exists():
            data = json.loads(_US_OWN_CACHE_FILE.read_text())
            if isinstance(data, dict) and isinstance(data.get("map"), dict):
                _US_OWN["map"] = data["map"]
                _US_OWN["ts"] = float(data.get("ts") or 0)
    except Exception:
        pass


def _save_us_own_to_disk() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _US_OWN_CACHE_FILE.write_text(json.dumps({"ts": _US_OWN["ts"], "map": _US_OWN["map"]}))
    except Exception:
        pass


def _fetch_us_ownership_one(symbol: str) -> dict | None:
    import yfinance as yf
    try:
        t = yf.Ticker(symbol)
        mh = t.major_holders
        if mh is None or mh.empty:
            return None
        mh_dict = mh["Value"].to_dict()
        inst_pct    = _to_float(mh_dict.get("institutionsPercentHeld"))
        insider_pct = _to_float(mh_dict.get("insidersPercentHeld"))
        inst_float  = _to_float(mh_dict.get("institutionsFloatPercentHeld"))
        inst_count  = mh_dict.get("institutionsCount")
        inst_count  = int(inst_count) if inst_count is not None else None

        top3 = None
        try:
            ih = t.institutional_holders
            if ih is not None and not ih.empty:
                top3 = round(float(ih.head(3)["pctHeld"].sum()) * 100, 2)
        except Exception:
            pass

        if inst_pct is None and insider_pct is None:
            return None
        return {
            "inst_pct": round(inst_pct * 100, 2) if inst_pct is not None else None,
            "insider_pct": round(insider_pct * 100, 2) if insider_pct is not None else None,
            "inst_float": round(inst_float * 100, 2) if inst_float is not None else None,
            "inst_count": inst_count,
            "top3_conc": top3,
        }
    except Exception:
        return None


def _build_us_ownership() -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        tickers = [r["symbol"] for r in _US["rows"]]
        _US_OWN["total"] = len(tickers)
        _US_OWN["progress"] = 0
        result: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_fetch_us_ownership_one, s): s for s in tickers}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    val = fut.result()
                except Exception:
                    val = None
                if val is not None:
                    result[s] = val
                _US_OWN["progress"] += 1
        _US_OWN["map"] = result
        _US_OWN["ts"] = time.time()
        _save_us_own_to_disk()
    finally:
        _US_OWN["building"] = False


def ensure_us_ownership_cache() -> None:
    if not _US["rows"] or not _US["meta_ready"]:
        return  # need the universe ticker list first
    if not _US_OWN["disk_checked"]:
        _US_OWN["disk_checked"] = True
        if not _US_OWN["map"]:
            _load_us_own_from_disk()
    now = time.time()
    if _US_OWN["building"]:
        return
    if _US_OWN["map"] and (now - _US_OWN["ts"] < _US_OWN_TTL):
        return
    _US_OWN["building"] = True
    threading.Thread(target=_build_us_ownership, daemon=True).start()


def us_ownership_status() -> dict:
    return {
        "building": _US_OWN["building"],
        "progress": _US_OWN["progress"],
        "total": _US_OWN["total"],
        "ready": bool(_US_OWN["map"]),
        "built_at": _US_OWN["ts"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# US net-income growth consistency cache — same problem class as ownership: not
# in the bulk quote endpoint, needs t.financials per symbol (~3.4s/ticker,
# confirmed live). yfinance's free tier only returns 4 ANNUAL columns, so the
# ceiling here is "consistent for 3Y" (3 YoY deltas from 4 data points) — there
# is no 4Y variant for US, unlike CN which already fetches 5 years and can offer
# both. Net INCOME growth (not EPS) — matches what the CN side uses (netprofit_yoy),
# apples-to-apples across markets rather than mixing an EPS basis into one and a
# net-income basis into the other.
_US_EARN: dict[str, Any] = {"ts": 0.0, "map": {}, "building": False, "progress": 0, "total": 0,
                            "disk_checked": False}
_US_EARN_TTL = 21 * 24 * 3600   # annual filings — even less frequent than 13F
_US_EARN_CACHE_FILE = _DATA_DIR / "us_earnings_cache.json"


def _load_us_earn_from_disk() -> None:
    try:
        if _US_EARN_CACHE_FILE.exists():
            data = json.loads(_US_EARN_CACHE_FILE.read_text())
            if isinstance(data, dict) and isinstance(data.get("map"), dict):
                _US_EARN["map"] = data["map"]
                _US_EARN["ts"] = float(data.get("ts") or 0)
    except Exception:
        pass


def _save_us_earn_to_disk() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _US_EARN_CACHE_FILE.write_text(json.dumps({"ts": _US_EARN["ts"], "map": _US_EARN["map"]}))
    except Exception:
        pass


def _fetch_us_earnings_one(symbol: str) -> dict | None:
    import time as _time
    import yfinance as yf
    try:
        # Bug found 2026-08-13, second one: a whole-market 8-worker parallel
        # build against yfinance loses ~30% of the universe to what LOOKS
        # like "no financials" but usually isn't — confirmed by resampling 40
        # of the "missing" tickers with calm, single-threaded, gently-paced
        # retries: 34/40 (85%) succeeded immediately, including large caps
        # like UBER ($155B mcap) that obviously have real data. Only 6/40 had
        # genuinely empty financials in yfinance (checked directly). This is
        # Yahoo's rate limiter reacting to the concurrent load, not a data
        # gap — so a couple of short-backoff retries here recovers most of
        # it, cheaper than serializing or slowing down the whole build.
        fin = None
        for attempt in range(3):
            t = yf.Ticker(symbol)
            fin = t.financials
            if fin is not None and not fin.empty:
                break
            if attempt < 2:
                _time.sleep(0.8 * (attempt + 1))
        if fin is None or fin.empty:
            return None
        ni_label = next((n for n in _NI_LABELS if n in fin.index), None)
        if ni_label is None:
            return None
        vals = _clean_nums(fin.loc[ni_label].tolist())
        if len(vals) < 2:
            return None
        # vals is newest-first (yfinance convention) — YoY growth between each
        # adjacent pair, using the prior year as the denominator
        growth = []
        for i in range(len(vals) - 1):
            prev = vals[i + 1]
            if prev and prev > 0:
                growth.append((vals[i] / prev - 1) * 100)
            else:
                break   # a loss-making or missing prior year breaks the consistency chain
        earngrowth_min_3y = round(min(growth[:3]), 1) if len(growth) >= 3 else None
        # MAGNET-lite ACCEL proxy (see the matching CN comment in
        # _fetch_cn_roe_one) — simple YoY-growth-rate delta, not the spec's
        # log-growth-off-raw-levels form. yfinance's free tier caps annual
        # data at 4 columns, so at most 3 YoY growth points exist here anyway.
        # Phase-1 fix (2026-08-13): the `growth` chain above already requires
        # positive PRIOR-year bases (denominators), but never checked the
        # most recent year (vals[0], point t) itself is positive — gated on
        # that here, plus the same winsorization as CN, for the same reason
        # (unbounded near a small/negative base is noise, not signal).
        #
        # Second fix, same day (see matching CN comment for the live LPG
        # example that caught this): profitability alone rewards a
        # decline-then-rebound pattern as if it were genuine acceleration.
        # Now requires BOTH of the last two YoY growth rates to be positive.
        accel_proxy = None
        if len(growth) >= 2 and vals[0] > 0 and growth[0] > 0 and growth[1] > 0:
            accel_proxy = _clip(round(growth[0] - growth[1], 1), -_ACCEL_CLIP, _ACCEL_CLIP)
        # Turnaround-then-accelerate (§1.1's TURNAROUND sleeve, kept SEPARATE
        # from ACCEL) — loss two years ago, turned profitable last year, grew
        # further this year. Confirmed live 2026-08-13 against real names
        # (CRDO, RIGL, ACAD, CDE, SGHC, ...) before building. Uses vals
        # already fetched above — no extra API call. Tightened (see matching
        # CN comment): N(t)-N(t-1) > N(t-1)-N(t-2), not just N(t)>N(t-1).
        turnaround_accel = (len(vals) >= 3 and vals[2] < 0 and vals[1] > 0
                             and (vals[0] - vals[1]) > (vals[1] - vals[2]))
        # Profit acceleration, no turnaround (2026-08-13, user request): same
        # accelerating shape for a company that was NEVER in a loss over
        # this window — all three points positive. Mutually exclusive with
        # turnaround_accel by construction (that one requires vals[2] < 0).
        #
        # Bug found and fixed 2026-08-13, same day (see matching CN comment
        # for the live 002242.SZ/Joyoung example): "all three positive" lets
        # a DECELERATING DECLINE through just as easily as genuine
        # acceleration, since the second-difference test alone can't tell
        # them apart. Now also requires genuine period-over-period growth
        # (vals[0] > vals[1] > vals[2]), same gate ACCEL already has via its
        # growth-rate-positive check.
        profit_accel = len(vals) >= 3 and vals[2] >= 0 and vals[1] > 0 and vals[0] > 0
        profit_accel = (profit_accel and vals[0] > vals[1] and vals[1] > vals[2]
                         and (vals[0] - vals[1]) > (vals[1] - vals[2]))

        # Revenue acceleration and margin turnaround (2026-08-13, user
        # request; revenue formula corrected same day). Both reuse `fin` —
        # no new API call. Revenue is essentially never negative, so unlike
        # NI/margin there's no turnaround/sign-flip gate — just a direct
        # acceleration check on the LEVEL: R(t)-R(t-1) > R(t-1)-R(t-2).
        # Margin genuinely can go negative, so the level-based turnaround
        # formula (with the sign-flip gate) applies directly, same as NI.
        # NI and revenue are pulled column-index-aligned (not from
        # independently-.tolist()-cleaned series, which could silently
        # misalign if one series has a NaN the other doesn't).
        revenue_accel = False
        margin_turnaround_accel = False
        margin_accel = False
        rev_label = next((n for n in _REV_LABELS if n in fin.index), None)
        if rev_label:
            cols = fin.columns
            ni_row, rev_row = fin.loc[ni_label], fin.loc[rev_label]
            rev_lvl: list[float | None] = []
            margin_vals: list[float | None] = []
            for i in range(min(3, len(cols))):
                ni_i, rev_i = _to_float(ni_row.iloc[i]), _to_float(rev_row.iloc[i])
                rev_ok = rev_i is not None and rev_i == rev_i
                rev_lvl.append(rev_i if rev_ok else None)
                ok = ni_i is not None and ni_i == ni_i and rev_ok and rev_i > 0
                margin_vals.append(ni_i / rev_i * 100 if ok else None)
            if len(rev_lvl) == 3 and None not in rev_lvl:
                rv, rv1, rv2 = rev_lvl
                revenue_accel = (rv2 >= 0) and (rv > rv1) and (rv1 > rv2) and (rv - rv1 > rv1 - rv2)
            if len(margin_vals) == 3 and None not in margin_vals:
                m, m1, m2 = margin_vals
                margin_turnaround_accel = (m2 < 0) and (m1 > 0) and (m - m1 > m1 - m2)
                margin_accel = (m2 >= 0) and (m1 > 0) and (m > 0)
                margin_accel = margin_accel and (m > m1) and (m1 > m2) and (m - m1 > m1 - m2)

        # Quarterly versions of all three (2026-08-13, user request). Unlike
        # CN's cumulative-YTD interim disclosures, yfinance's
        # quarterly_financials are genuinely discrete quarters (verified live
        # on CRDO: 169M/157M/83M/63M/37M, each independent — not a running
        # cumulative total), so no differencing needed, just a second fetch,
        # reused for all three metrics here. Single attempt, no retry
        # (2026-08-13): this is a bonus signal, not the core annual fetch —
        # its retry budget was part of what pushed total per-ticker request
        # volume up enough to strain Yahoo's rate limiter (see
        # _build_us_earnings comment for the concurrency reduction that also
        # addresses this). Local try/except: a quarterly failure should only
        # mean "we don't know the quarterly answer," never discard the
        # already-successful ANNUAL result (bug found and fixed same day —
        # missing-ticker rate went 6.2% -> 17.1% before this isolation).
        turnaround_accel_q = False
        revenue_accel_q = False
        margin_turnaround_accel_q = False
        profit_accel_q = False
        margin_accel_q = False
        try:
            qfin = t.quarterly_financials
            if qfin is not None and not qfin.empty:
                q_label = next((n for n in _NI_LABELS if n in qfin.index), None)
                q_rev_label = next((n for n in _REV_LABELS if n in qfin.index), None)
                if q_label:
                    qvals = _clean_nums(qfin.loc[q_label].tolist())
                    if len(qvals) >= 3:
                        qt, qt1, qt2 = qvals[0], qvals[1], qvals[2]
                        turnaround_accel_q = (qt2 < 0) and (qt1 > 0) and (qt - qt1 > qt1 - qt2)
                        profit_accel_q = qt2 >= 0 and qt1 > 0 and qt > 0
                        profit_accel_q = (profit_accel_q and qt > qt1 and qt1 > qt2
                                           and (qt - qt1) > (qt1 - qt2))
                if q_rev_label:
                    qcols = qfin.columns
                    qni_row, qrev_row = (qfin.loc[q_label] if q_label else None), qfin.loc[q_rev_label]
                    qrev_lvl: list[float | None] = []
                    qmargin_vals: list[float | None] = []
                    for i in range(min(3, len(qcols))):
                        qni_i = _to_float(qni_row.iloc[i]) if qni_row is not None else None
                        qrev_i = _to_float(qrev_row.iloc[i])
                        qrev_ok = qrev_i is not None and qrev_i == qrev_i
                        qrev_lvl.append(qrev_i if qrev_ok else None)
                        qok = qni_i is not None and qni_i == qni_i and qrev_ok and qrev_i > 0
                        qmargin_vals.append(qni_i / qrev_i * 100 if qok else None)
                    if len(qrev_lvl) == 3 and None not in qrev_lvl:
                        qrv, qrv1, qrv2 = qrev_lvl
                        revenue_accel_q = qrv2 >= 0 and qrv > qrv1 and qrv1 > qrv2 and (qrv - qrv1) > (qrv1 - qrv2)
                    if len(qmargin_vals) == 3 and None not in qmargin_vals:
                        qm, qm1, qm2 = qmargin_vals
                        margin_turnaround_accel_q = (qm2 < 0) and (qm1 > 0) and (qm - qm1 > qm1 - qm2)
                        margin_accel_q = qm2 >= 0 and qm1 > 0 and qm > 0
                        margin_accel_q = (margin_accel_q and qm > qm1 and qm1 > qm2
                                           and (qm - qm1) > (qm1 - qm2))
        except Exception:
            pass

        return {"earngrowth_min_3y": earngrowth_min_3y, "years": len(vals),
                "accel_proxy": accel_proxy, "turnaround_accel": turnaround_accel,
                "turnaround_accel_q": turnaround_accel_q,
                "revenue_accel": revenue_accel, "revenue_accel_q": revenue_accel_q,
                "margin_turnaround_accel": margin_turnaround_accel,
                "margin_turnaround_accel_q": margin_turnaround_accel_q,
                "profit_accel": profit_accel, "profit_accel_q": profit_accel_q,
                "margin_accel": margin_accel, "margin_accel_q": margin_accel_q}
    except Exception:
        return None


def _build_us_earnings() -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        tickers = [r["symbol"] for r in _US["rows"]]
        _US_EARN["total"] = len(tickers)
        _US_EARN["progress"] = 0
        result: dict[str, dict] = {}
        # Reduced from 8 -> 6 (2026-08-13): adding the quarterly_financials
        # fetch roughly doubled per-ticker request volume (up to 5 yfinance
        # calls now vs up to 3 before, across annual+quarterly retries),
        # which pushed the missing-ticker rate from 6.2% back up to 25%
        # under 8-way concurrency even with the exception-isolation fix —
        # the build was just making more total requests per minute against
        # Yahoo's rate limiter than before, not a code bug. Less concurrency
        # trades a slower build for fewer requests-per-minute.
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_fetch_us_earnings_one, s): s for s in tickers}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    val = fut.result()
                except Exception:
                    val = None
                if val is not None:
                    result[s] = val
                _US_EARN["progress"] += 1
        _US_EARN["map"] = result
        _US_EARN["ts"] = time.time()
        _save_us_earn_to_disk()
    finally:
        _US_EARN["building"] = False


def ensure_us_earnings_cache() -> None:
    if not _US["rows"] or not _US["meta_ready"]:
        return
    if not _US_EARN["disk_checked"]:
        _US_EARN["disk_checked"] = True
        if not _US_EARN["map"]:
            _load_us_earn_from_disk()
    now = time.time()
    if _US_EARN["building"]:
        return
    if _US_EARN["map"] and (now - _US_EARN["ts"] < _US_EARN_TTL):
        return
    _US_EARN["building"] = True
    threading.Thread(target=_build_us_earnings, daemon=True).start()


def us_earnings_status() -> dict:
    return {
        "building": _US_EARN["building"],
        "progress": _US_EARN["progress"],
        "total": _US_EARN["total"],
        "ready": bool(_US_EARN["map"]),
        "built_at": _US_EARN["ts"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# CN institutional/strategic ownership cache — top10_floatholders, NOT the same
# thing as the US 13F-based field: A-share disclosure gives the top-10 float
# shareholders per report, not a full-market aggregate "% institutionally held"
# the way 13F rollups do for the US. So "inst_pct"/"top3_conc" here mean "share
# of the TOP-10 FLOAT HOLDERS that are institutional," narrower than the US
# version — labeled as such in the UI, not silently treated as equivalent.
#
# Cheaper to build than the US cache: top10_floatholders supports whole-market
# pagination (offset/limit, no ts_code needed) — confirmed live, ~8-10 bulk
# calls cover the entire market for one report date, vs ~5,700 per-symbol calls
# for the US version. holder_type gives a real taxonomy (funds/insurers/banks/
# trusts/asset managers = institutional; 一般企业/国资局 = strategic/corporate,
# i.e. controlling-shareholder-like, not a portfolio institutional investor;
# 自然人 = individual, excluded from both — this is NOT the same concept as a US
# "insider," so it isn't labeled that way).
_CN_OWN: dict[str, Any] = {"ts": 0.0, "map": {}, "building": False, "progress": 0, "total": 0,
                           "disk_checked": False, "report_date": None, "error": None}
_CN_OWN_TTL = 21 * 24 * 3600   # quarterly report cadence — same reasoning as the US cache's TTL
_CN_OWN_CACHE_FILE = _DATA_DIR / "cn_ownership_cache.json"
_CN_OWN_PAGE = 6000

_CN_OWN_INSTITUTIONAL_TYPES = {
    "其他金融产品", "投资公司", "风险投资公司", "开放式投资基金", "社保基金、社保机构",
    "资产管理公司", "保险投资组合", "金融机构—证券公司", "基金专户理财", "金融机构—银行",
    "券商集合资产管理计划", "投资、咨询公司", "基本养老保险基金", "金融机构—保险公司",
    "企业年金", "金融机构—信托公司", "信托公司集合信托计划", "信托公司单一证券信托",
    "保险资管产品", "财务公司",
}
_CN_OWN_STRATEGIC_TYPES = {"一般企业", "国资局"}


def _load_cn_own_from_disk() -> None:
    try:
        if _CN_OWN_CACHE_FILE.exists():
            data = json.loads(_CN_OWN_CACHE_FILE.read_text())
            if isinstance(data, dict) and isinstance(data.get("map"), dict):
                _CN_OWN["map"] = data["map"]
                _CN_OWN["ts"] = float(data.get("ts") or 0)
                _CN_OWN["report_date"] = data.get("report_date")
    except Exception:
        pass


def _save_cn_own_to_disk() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CN_OWN_CACHE_FILE.write_text(json.dumps({
            "ts": _CN_OWN["ts"], "map": _CN_OWN["map"], "report_date": _CN_OWN["report_date"],
        }))
    except Exception:
        pass


def _cn_own_quarter_candidates(n: int = 4) -> list[str]:
    from datetime import date
    today = date.today()
    md = [(3, 31), (6, 30), (9, 30), (12, 31)]
    dates = [date(y, m, d) for y in (today.year, today.year - 1) for m, d in md if date(y, m, d) <= today]
    dates.sort(reverse=True)
    return [d.strftime("%Y%m%d") for d in dates[:n]]


def _fetch_top10_page(end_date: str, offset: int) -> list[dict]:
    # start_date=end_date pins this to exactly ONE report period. Confirmed live:
    # end_date alone is an upper bound, not an exact filter — a single stock's
    # end_date-only query returned 702 rows spanning many historical periods
    # intermingled, which would have silently corrupted the whole-market build
    # (mixing quarters together per stock, and letting a handful of long-history
    # names crowd other stocks out of the paginated window). Caught by testing
    # before shipping, not by inspection.
    from . import cn_data
    params = {"start_date": end_date, "end_date": end_date, "offset": str(offset), "limit": str(_CN_OWN_PAGE)}
    try:
        return cn_data.call("top10_floatholders", params, "ts_code,holder_name,hold_ratio,holder_type")
    except Exception:
        time.sleep(2)   # one retry — a transient read-timeout was observed live during testing
        return cn_data.call("top10_floatholders", params, "ts_code,holder_name,hold_ratio,holder_type")


def _pick_cn_own_report_date() -> str | None:
    """Just checks the date has real data — NOT total market coverage (a single
    6,000-row page only ever holds ~700-900 distinct stocks at ~8 holders each,
    so a coverage threshold in the thousands could never pass off one page —
    that was a real bug here, caught by testing before shipping)."""
    for cand in _cn_own_quarter_candidates():
        try:
            rows = _fetch_top10_page(cand, 0)
        except Exception:
            continue
        if len({r["ts_code"] for r in rows}) > 200:
            return cand
    return None


def _build_cn_ownership() -> None:
    try:
        report_date = _pick_cn_own_report_date()
        if not report_date:
            _CN_OWN["error"] = "Could not find a report date with adequate market coverage."
            return
        _CN_OWN["report_date"] = report_date
        by_stock: dict[str, list] = {}
        offset = 0
        while offset < 60_000:   # safety cap — whole A-share market fits well under this
            rows = _fetch_top10_page(report_date, offset)
            if not rows:
                break
            for r in rows:
                by_stock.setdefault(r["ts_code"], []).append(r)
            _CN_OWN["progress"] = len(by_stock)
            _CN_OWN["total"] = len(by_stock)
            offset += _CN_OWN_PAGE

        result: dict[str, dict] = {}
        for ts, holders in by_stock.items():
            ranked = sorted(holders, key=lambda r: -(r.get("hold_ratio") or 0))
            inst = sum((r.get("hold_ratio") or 0) for r in ranked if r.get("holder_type") in _CN_OWN_INSTITUTIONAL_TYPES)
            strat = sum((r.get("hold_ratio") or 0) for r in ranked if r.get("holder_type") in _CN_OWN_STRATEGIC_TYPES)
            top3 = sum((r.get("hold_ratio") or 0) for r in ranked[:3])
            result[ts] = {
                "inst_pct": round(inst, 2), "strategic_pct": round(strat, 2),
                "top3_conc": round(top3, 2), "n_holders": len(ranked),
            }
        _CN_OWN["map"] = result
        _CN_OWN["ts"] = time.time()
        _CN_OWN["error"] = None
        _save_cn_own_to_disk()
    except Exception as exc:
        _CN_OWN["error"] = str(exc)
    finally:
        _CN_OWN["building"] = False


def ensure_cn_ownership_cache() -> None:
    if not _CN["rows"]:
        return
    if not _CN_OWN["disk_checked"]:
        _CN_OWN["disk_checked"] = True
        if not _CN_OWN["map"]:
            _load_cn_own_from_disk()
    now = time.time()
    if _CN_OWN["building"]:
        return
    if _CN_OWN["map"] and (now - _CN_OWN["ts"] < _CN_OWN_TTL):
        return
    _CN_OWN["building"] = True
    threading.Thread(target=_build_cn_ownership, daemon=True).start()


def cn_ownership_status() -> dict:
    return {
        "building": _CN_OWN["building"],
        "progress": _CN_OWN["progress"],
        "total": _CN_OWN["total"],
        "ready": bool(_CN_OWN["map"]),
        "built_at": _CN_OWN["ts"],
        "report_date": _CN_OWN["report_date"],
        "error": _CN_OWN["error"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Filtering
# ══════════════════════════════════════════════════════════════════════════════

def _match(r: dict, c: dict) -> bool:
    def ge(val, key):
        t = c.get(key)
        if t in (None, ""):
            return True
        return val is not None and val >= float(t)

    def le(val, key):
        t = c.get(key)
        if t in (None, ""):
            return True
        return val is not None and val <= float(t)

    def top_pct(pctile_val, key):
        """MAGNET-lite Phase 2: key holds a 'top N%' entry (e.g. 20 means
        keep only the top 20% by this metric). Converts to the equivalent
        percentile-rank floor. A stock with no percentile (metric was gated
        out — unprofitable, thin PE, etc.) never passes, same as any other
        None-valued threshold check here."""
        t = c.get(key)
        if t in (None, ""):
            return True
        try:
            t = float(t)
        except (TypeError, ValueError):
            return True
        return pctile_val is not None and pctile_val >= (100.0 - t)

    if not ge(r.get("market_cap"), "mcap_min"): return False
    if not le(r.get("market_cap"), "mcap_max"): return False
    if not ge(r.get("pe"), "pe_min"):           return False
    if not le(r.get("pe"), "pe_max"):           return False
    if not le(r.get("fpe"), "fpe_max"):         return False
    if not le(r.get("pb"), "pb_max"):           return False
    if not le(r.get("ps"), "ps_max"):           return False
    if not ge(r.get("div_yield"), "div_yield_min"): return False
    if not ge(r.get("impl_growth"), "growth_min"): return False
    if not le(r.get("impl_growth"), "growth_max"): return False
    if not le(r.get("peg"), "peg_max"):         return False
    if not ge(r.get("eps"), "eps_min"):         return False
    if not ge(r.get("price"), "price_min"):     return False
    if not le(r.get("price"), "price_max"):     return False
    if not ge(r.get("avg_vol"), "vol_min"):     return False
    if not ge(r.get("mom_52w"), "mom_min"):     return False   # 12-mo return floor (momentum)
    if not le(r.get("mom_52w"), "mom_max"):     return False   # 12-mo return cap (contrarian)
    if not ge(r.get("roe"), "roe_min"):         return False
    if not ge(r.get("roe_5y_min"), "roe5y_min"): return False   # every one of the last 5 annual ROEs clears the bar
    if not ge(r.get("rev_growth"), "revgrowth_min"): return False
    if not ge(r.get("operate_profit"), "opprofit_min"): return False
    if not ge(r.get("earngrowth_min_3y"), "earngrowth3y_min"): return False
    if not ge(r.get("earngrowth_min_4y"), "earngrowth4y_min"): return False
    if not ge(r.get("earngrowth_min_q4"), "earngrowthq4_min"): return False   # CN-only: last 4 reported periods (Q1/H1/9M/FY cadence)
    if not ge(r.get("earngrowth_min_q8"), "earngrowthq8_min"): return False   # CN-only: last 8 reported periods (~2yr)
    # MAGNET-lite Phase 2: percentile-rank "top N%" cuts, not raw thresholds —
    # matches the spec's own cross-sectional-percentile design (§2/§3),
    # robust to the fat-tailed raw values Phase 1 could only clip/gate.
    if not top_pct(r.get("accel_pctile"), "accel_top_pct"): return False
    if not top_pct(r.get("garp_pctile"), "garp_top_pct"): return False
    if not top_pct(r.get("timing_pctile"), "timing_top_pct"): return False
    if not top_pct(r.get("h52_pctile"), "h52_top_pct"): return False
    if c.get("turnaround_only") and not r.get("turnaround_accel"):   # MAGNET-lite TURNAROUND sleeve — kept separate from ACCEL
        return False
    if c.get("turnaround_only_q") and not r.get("turnaround_accel_q"):   # same test, quarterly cadence
        return False
    if c.get("revenue_accel_only") and not r.get("revenue_accel"):   # revenue level acceleration, no turnaround gate (revenue is never negative)
        return False
    if c.get("revenue_accel_only_q") and not r.get("revenue_accel_q"):   # same test, quarterly cadence
        return False
    if c.get("margin_turnaround_only") and not r.get("margin_turnaround_accel"):   # same test as profit turnaround, margin level
        return False
    if c.get("margin_turnaround_only_q") and not r.get("margin_turnaround_accel_q"):   # same test, quarterly cadence
        return False
    if c.get("profit_accel_only") and not r.get("profit_accel"):   # profit accelerating, never negative in-window (no turnaround gate)
        return False
    if c.get("profit_accel_only_q") and not r.get("profit_accel_q"):   # same test, quarterly cadence
        return False
    if c.get("margin_accel_only") and not r.get("margin_accel"):   # margin accelerating, never negative in-window
        return False
    if c.get("margin_accel_only_q") and not r.get("margin_accel_q"):   # same test, quarterly cadence
        return False

    # ── Weinstein Stage Analysis ─────────────────────────────────────────────
    # `stage_basis` picks which moving-average variant every stage-related
    # test reads: "sma" is Weinstein canonical, "ema" the faster alternative.
    # A stock with no stage (insufficient history) never matches a stage
    # filter — it is unknown, not "not Stage 2".
    _sb = "ema" if c.get("stage_basis") == "ema" else "sma"
    _stages = c.get("stages")
    if _stages:
        _st = r.get("stage_ema") if _sb == "ema" else r.get("stage")
        if _st not in _stages:
            return False
    if not ge(r.get("mansfield_rs"), "mansfield_min"): return False
    # Percentile RS — cross-sectional rank 1-99. `rs_basis` picks the
    # construction: "ibd" (recent quarter double-weighted) or "jt" (academic
    # 12-1, skipping the most recent period). They disagree meaningfully
    # (rank correlation 0.83), so the choice is exposed rather than assumed.
    if c.get("rs_pct_min") is not None:
        _rk = "rs_pct_jt" if c.get("rs_basis") == "jt" else "rs_pct"
        _rv = r.get(_rk)
        if _rv is None or _rv < c["rs_pct_min"]:
            return False
    if not ge(r.get("price_vs_ema30" if _sb == "ema" else "price_vs_sma30"), "price_vs_ma_min"): return False
    if not ge(r.get("ema30_slope" if _sb == "ema" else "sma30_slope"), "ma_slope_min"): return False
    # ── Volume: breakout (single-week surge) and sustained (quarter vs quarter) ──
    # Either can be used alone. When BOTH are set the combine mode decides:
    # "all" (default) demands both, "any" accepts either. Left implicit this was
    # a silent AND, which quietly excluded stocks that had one signal but not
    # the other — the exact case a user setting two volume tests may want.
    _vb, _vs = c.get("volratio_min"), c.get("adv_vol_mult_min")
    if _vb is not None or _vs is not None:
        _pb = ge(r.get("vol_ratio"), "volratio_min") if _vb is not None else None
        _ps = ge(r.get("adv_vol_mult"), "adv_vol_mult_min") if _vs is not None else None
        _set = [x for x in (_pb, _ps) if x is not None]
        if c.get("vol_match") == "any":
            if not any(_set):
                return False
        elif not all(_set):
            return False

    if c.get("above_resistance_only") and not r.get("above_resistance"):
        return False

    # ── Stage checklists (Stage 1 base / 2 advance / 3 top / 4 decline) ──────
    # An UNTESTABLE checklist (too little history) never matches a checklist
    # filter — same rule as the stage label itself: unknown is not "fails".
    if c.get("base_score_min") is not None:
        if not r.get("base_testable") or not ge(r.get("base_score"), "base_score_min"):
            return False
    if c.get("base_textbook_only") and not r.get("base_textbook"):
        return False
    if c.get("base_volume_dryup_only") and not r.get("base_c3_volume_dryup"):
        return False

    if c.get("adv_score_min") is not None:
        if not r.get("adv_testable") or not ge(r.get("adv_score"), "adv_score_min"):
            return False
    if c.get("adv_textbook_only") and not r.get("adv_textbook"):
        return False
    if c.get("adv_volume_expanding_only") and not r.get("adv_c1_volume_expanding"):
        return False
    if c.get("adv_higher_lows_only") and not r.get("adv_c3_higher_lows"):
        return False
    if c.get("adv_price_above_only") and not r.get("adv_c4_price_above"):
        return False

    if c.get("top_score_min") is not None:
        if not r.get("top_testable") or not ge(r.get("top_score"), "top_score_min"):
            return False
    if c.get("top_textbook_only") and not r.get("top_textbook"):
        return False

    if c.get("decl_confirmed_only") and not r.get("decl_confirmed"):
        return False
    if c.get("decl_volume_rising_only") and not r.get("decl_opt_volume_rising"):
        return False
    # Top-down (Phase 4): the stock's own industry group must qualify too.
    _sec = c.get("sector_stages")
    if _sec and r.get("sector_stage") not in _sec:
        return False
    if not ge(r.get("sector_rs"), "sector_rs_min"): return False
    if not le(r.get("sector_rs_rank"), "sector_rank_max"): return False
    if not ge(r.get("report_coverage"), "reportcov_min"): return False   # CN-only: # distinct sell-side reports, trailing 180d
    if not ge(r.get("report_inst_count"), "reportinst_min"): return False   # CN-only: # distinct brokerages covering
    if c.get("report_positive_only") and r.get("report_rating_bucket") != "positive":
        return False
    if c.get("report_initiated_only") and not r.get("report_initiated"):
        return False
    if not ge(r.get("inst_pct"), "inst_min"):   return False
    if not le(r.get("inst_pct"), "inst_max"):   return False
    if not ge(r.get("insider_pct"), "insider_min"): return False
    if not le(r.get("insider_pct"), "insider_max"): return False
    if not ge(r.get("inst_count"), "inst_holders_min"): return False
    if not le(r.get("top3_conc"), "top3_max"):  return False
    if not le(r.get("strategic_pct"), "strategic_max"): return False   # CN-only (一般企业/国资局 concentration)

    if c.get("eps_positive"):
        eps, pe = r.get("eps"), r.get("pe")
        profitable = (eps is not None and eps > 0) or (pe is not None and pe > 0)
        if not profitable:
            return False

    # "at least N% below 52-wk high" → off_52w_high is negative
    bh = c.get("below_high_min")
    if bh not in (None, ""):
        off = r.get("off_52w_high")
        if off is None or off > -float(bh):
            return False

    sectors = c.get("sectors")
    if sectors and r.get("sector") not in sectors:
        return False

    industries = c.get("industries")
    if industries:
        ind = (r.get("industry") or "").lower()
        if not any(q.lower() in ind for q in industries):
            return False

    return True


_SORT_KEYS = {"market_cap", "pe", "fpe", "pb", "ps", "div_yield", "eps", "price",
              "impl_growth", "peg"}


def _apply(rows: list[dict], criteria: dict) -> dict:
    out = [r for r in rows if _match(r, criteria)]
    sort_key = criteria.get("sort") or "market_cap"
    if sort_key not in _SORT_KEYS:
        sort_key = "market_cap"
    reverse = (criteria.get("sort_dir") or "desc") != "asc"
    out.sort(key=lambda r: (r.get(sort_key) is None, r.get(sort_key) or 0), reverse=reverse)
    try:
        limit = max(1, min(500, int(criteria.get("limit") or 100)))
    except (TypeError, ValueError):
        limit = 100
    return {"status": "ready", "count": len(out), "universe": len(rows),
            "results": out[:limit]}


# Valuation-dependent keys — if any is set, we need the enriched snapshot
_VAL_KEYS = ("pe_min", "pe_max", "fpe_max", "pb_max", "ps_max",
             "div_yield_min", "eps_min", "eps_positive", "below_high_min", "vol_min",
             "growth_min", "growth_max", "peg_max")


_OWN_KEYS = ("inst_min", "inst_max", "insider_min", "insider_max", "inst_holders_min", "top3_max")



_STAGE_FIELDS = ("stage", "stage_label", "stage_ema", "stage_ema_label", "mansfield_rs",
                 "rs_pct", "rs_pct_jt",
                 "vol_ratio", "price_vs_sma30", "price_vs_ema30", "sma30_slope", "ema30_slope",
                 "sma30", "ema30", "resistance", "dist_to_resistance", "above_resistance",
                 "weeks_available", "insufficient_history")
# Stage-checklist fields. A deliberately FOCUSED set: the scores and verdicts
# that get filtered on, plus the underlying metric behind each criterion so a
# result can be judged without opening the stock page. The raw per-criterion
# booleans for tests with no separate filter are left out to keep the row from
# ballooning across thousands of stocks.
_STAGE_CHECKLIST_FIELDS = (
    # Stage 1 — base
    "base_testable", "base_score", "base_textbook",
    "base_decline_pct", "base_band_pct", "base_vol_trend_pct", "base_ma_drift_pct",
    "base_c3_volume_dryup",
    # Stage 2 — advance
    "adv_testable", "adv_score", "adv_textbook",
    "adv_vol_mult", "adv_above_pct", "adv_higher_lows", "adv_swing_lows",
    "adv_c1_volume_expanding", "adv_c3_higher_lows", "adv_c4_price_above",
    # Stage 3 — top
    "top_testable", "top_score", "top_textbook",
    "top_vol_mult", "top_vola_ratio", "top_weekly_vol_pct",
    # Stage 4 — decline
    "decl_testable", "decl_score", "decl_confirmed",
    "decl_vol_mult", "decl_opt_volume_rising",
)

_STAGE_KEYS = ("stages", "rs_pct_min", "rs_basis", "mansfield_min", "volratio_min", "price_vs_ma_min",
               "ma_slope_min", "above_resistance_only",
               "sector_stages", "sector_rs_min", "sector_rank_max",
               # stage-checklist filters
               "base_score_min", "base_textbook_only", "base_volume_dryup_only",
               "adv_score_min", "adv_textbook_only", "adv_volume_expanding_only",
               "adv_higher_lows_only", "adv_price_above_only", "adv_vol_mult_min",
               "vol_match",
               "top_score_min", "top_textbook_only",
               "decl_confirmed_only", "decl_volume_rising_only")


def _merge_stage(market: str, rows: list[dict], criteria: dict) -> dict | None:
    """Attach Weinstein Stage Analysis fields to the filter rows.

    Returns a "building" payload when a stage criterion is set but the weekly
    price cache has not been built yet — never silently returns an empty or
    wrong result, matching how every other cache in this module behaves.
    """
    from . import price_history, stage_analysis
    price_history.ensure_price_history(market)
    smap = stage_analysis.build_stage_map(market)
    if smap:
        # Phase 4: attach the stock's INDUSTRY-GROUP stage alongside its own,
        # so Weinstein's top-down "is the group leading too?" test is filterable.
        gs = stage_analysis.build_group_stages(market)
        groups = gs.get("groups") or {}
        for r in rows:
            info = smap.get(r["symbol"])
            # A symbol ABSENT from the stage map (too new to have entered the
            # weekly cache) and one PRESENT but short of the 56-week minimum
            # both end up with stage=None, and rendered identically as "—" a
            # brand-new listing was indistinguishable from a data gap. Flag
            # which it is: SKHY and SPCX are July/June 2026 listings sitting in
            # the market-cap top 20 with no analytics at all.
            r["stage_data"] = ("missing" if info is None
                               else "insufficient" if info.get("insufficient_history")
                               else "ok")
            info = info or {}
            for k in _STAGE_FIELDS:
                r[k] = info.get(k)
            for k in _STAGE_CHECKLIST_FIELDS:
                r[k] = info.get(k)
            g = (r.get("industry") or "").strip()
            gv = groups.get(g) or {}
            r["sector_name"] = g or None
            r["sector_stage"] = gv.get("stage")
            r["sector_stage_label"] = gv.get("stage_label")
            r["sector_rs"] = gv.get("mansfield_rs")
            r["sector_rs_rank"] = gv.get("rs_rank")
            r["sector_rs_rank_of"] = gv.get("rs_rank_of")
            r["sector_pct_stage2"] = gv.get("pct_stage2")
            r["sector_members"] = gv.get("members")
        return None
    if any(criteria.get(k) not in (None, "", False, []) for k in _STAGE_KEYS):
        return {"status": "building", "stage_status": price_history.price_history_status(market)}
    return None


def filter_us(criteria: dict) -> dict:
    ensure_us_snapshot()
    if not _US["meta_ready"]:
        return {"status": "building", **us_status()}
    needs_val = any(criteria.get(k) not in (None, "", False, []) for k in _VAL_KEYS)
    if needs_val and not _US["ready"]:
        return {"status": "building", **us_status()}

    ensure_us_ownership_cache()
    own_map = _US_OWN["map"]
    if own_map:
        for r in _US["rows"]:
            info = own_map.get(r["symbol"]) or {}
            r["inst_pct"] = info.get("inst_pct")
            r["insider_pct"] = info.get("insider_pct")
            r["inst_float"] = info.get("inst_float")
            r["inst_count"] = info.get("inst_count")
            r["top3_conc"] = info.get("top3_conc")
    elif any(criteria.get(k) not in (None, "") for k in _OWN_KEYS):
        return {"status": "building", "own_status": us_ownership_status()}

    ensure_us_earnings_cache()
    earn_map = _US_EARN["map"]
    if earn_map:
        for r in _US["rows"]:
            info = earn_map.get(r["symbol"]) or {}
            r["earngrowth_min_3y"] = info.get("earngrowth_min_3y")
            r["accel_proxy"] = info.get("accel_proxy")
            r["turnaround_accel"] = info.get("turnaround_accel")
            r["turnaround_accel_q"] = info.get("turnaround_accel_q")
            r["revenue_accel"] = info.get("revenue_accel")
            r["revenue_accel_q"] = info.get("revenue_accel_q")
            r["margin_turnaround_accel"] = info.get("margin_turnaround_accel")
            r["margin_turnaround_accel_q"] = info.get("margin_turnaround_accel_q")
            r["profit_accel"] = info.get("profit_accel")
            r["profit_accel_q"] = info.get("profit_accel_q")
            r["margin_accel"] = info.get("margin_accel")
            r["margin_accel_q"] = info.get("margin_accel_q")
    elif (criteria.get("earngrowth3y_min") not in (None, "") or criteria.get("accel_top_pct") not in (None, "")
          or criteria.get("turnaround_only") or criteria.get("turnaround_only_q")
          or criteria.get("revenue_accel_only") or criteria.get("revenue_accel_only_q")
          or criteria.get("margin_turnaround_only") or criteria.get("margin_turnaround_only_q")
          or criteria.get("profit_accel_only") or criteria.get("profit_accel_only_q")
          or criteria.get("margin_accel_only") or criteria.get("margin_accel_only_q")):
        return {"status": "building", "earn_status": us_earnings_status()}
    # GARP/TIMING/H52 MAGNET-lite legs are pure derivations of fields already
    # in the row (pe, mom_200d, off_52w_high) — no new fetch, computed inline.
    for r in _US["rows"]:
        pe = r.get("pe")
        # Phase-1 fix (2026-08-13): a near-zero PE (distressed/data-quality
        # names) blows up 100/PE into a nonsense "yield" that reads as deep
        # value but usually isn't — floor at PE>=2 rather than cap the output,
        # since capping would still let the name through looking like a
        # normal moderate-yield stock instead of excluding it outright.
        r["garp_ey"] = round(100.0 / pe, 2) if pe and pe >= 2 else None
        r["timing_proxy"] = r.get("mom_200d")   # true price-vs-MA200 change %, from Yahoo bulk quote
        r["h52_proxy"] = (100 + r["off_52w_high"]) if r.get("off_52w_high") is not None else None
    # Phase 2 (2026-08-13): cross-sectional percentile ranks, computed only
    # among stocks that actually have a value for each field — matches the
    # spec's own robustness mechanism (§2/§3), replaces raw-threshold
    # min-value filtering, which Phase 1's gating/winsorizing only partially
    # substituted for.
    for field, pfield in (("accel_proxy", "accel_pctile"), ("garp_ey", "garp_pctile"),
                          ("timing_proxy", "timing_pctile"), ("h52_proxy", "h52_pctile")):
        ranks = _percentile_ranks(_US["rows"], field)
        for r in _US["rows"]:
            r[pfield] = ranks.get(r["symbol"])

    _sb = _merge_stage("us", _US["rows"], criteria)
    if _sb is not None:
        return _sb

    res = _apply(_US["rows"], criteria)
    res["currency"] = "$"
    res["built_at"] = _US["ts"]
    res["fully_ready"] = _US["ready"]
    res["own_status"] = us_ownership_status()
    res["earn_status"] = us_earnings_status()
    from . import price_history as _ph
    res["stage_status"] = _ph.price_history_status("us")
    return res


def filter_cn(criteria: dict) -> dict:
    ensure_cn_snapshot()
    if not _CN["rows"]:
        return {"status": "error", "error": "Could not load A-share market snapshot."}
    ensure_cn_roe_cache()
    roe_map = _CN_ROE["map"]
    _fund_keys = ("roe_min", "roe5y_min", "revgrowth_min", "opprofit_min", "earngrowth3y_min", "earngrowth4y_min",
                  "earngrowthq4_min", "earngrowthq8_min")
    if roe_map:
        for r in _CN["rows"]:
            info = roe_map.get(r["symbol"]) or {}
            r["roe"] = info.get("latest")
            r["roe_5y_min"] = info.get("min_5y")
            r["roe_5y_years"] = info.get("years")
            r["rev_growth"] = info.get("rev_growth")
            r["operate_profit"] = info.get("operate_profit")
            r["earngrowth_min_3y"] = info.get("earngrowth_min_3y")
            r["earngrowth_min_4y"] = info.get("earngrowth_min_4y")
            r["earngrowth_min_q4"] = info.get("earngrowth_min_q4")
            r["earngrowth_min_q8"] = info.get("earngrowth_min_q8")
            r["accel_proxy"] = info.get("accel_proxy")
            r["turnaround_accel"] = info.get("turnaround_accel")
            r["turnaround_accel_q"] = info.get("turnaround_accel_q")
            r["revenue_accel"] = info.get("revenue_accel")
            r["revenue_accel_q"] = info.get("revenue_accel_q")
            r["margin_turnaround_accel"] = info.get("margin_turnaround_accel")
            r["margin_turnaround_accel_q"] = info.get("margin_turnaround_accel_q")
            r["profit_accel"] = info.get("profit_accel")
            r["profit_accel_q"] = info.get("profit_accel_q")
            r["margin_accel"] = info.get("margin_accel")
            r["margin_accel_q"] = info.get("margin_accel_q")
    elif (any(criteria.get(k) not in (None, "") for k in _fund_keys) or criteria.get("accel_top_pct") not in (None, "")
          or criteria.get("turnaround_only") or criteria.get("turnaround_only_q")
          or criteria.get("revenue_accel_only") or criteria.get("revenue_accel_only_q")
          or criteria.get("margin_turnaround_only") or criteria.get("margin_turnaround_only_q")
          or criteria.get("profit_accel_only") or criteria.get("profit_accel_only_q")
          or criteria.get("margin_accel_only") or criteria.get("margin_accel_only_q")):
        # cache not built yet — don't silently return an empty/wrong result
        return {"status": "building", "roe_status": cn_roe_status()}
    # GARP/TIMING MAGNET-lite legs, derived inline from fields already present
    # (pe, mom_6m). No H52 leg for CN — a true 52-week-high needs a per-stock
    # daily-close history archive this product doesn't have; Tushare's bulk
    # snapshot pattern used elsewhere here only gives ONE past date's price
    # per call, not a rolling max, so it can't be built cheaply. Omitted
    # rather than faked with a wrong proxy.
    for r in _CN["rows"]:
        pe = r.get("pe")
        # Phase-1 fix (2026-08-13): same PE floor as the US side — see that
        # comment for why a near-zero PE is excluded, not capped.
        r["garp_ey"] = round(100.0 / pe, 2) if pe and pe >= 2 else None
        # mom_6m is a REAL point-to-point 6-month return (Tushare bulk snapshot,
        # not a moving average) — a directional trend proxy for TIMING, not the
        # spec's price-vs-MA200 form.
        r["timing_proxy"] = r.get("mom_6m")
    # Phase 2 (2026-08-13): see the matching US comment — cross-sectional
    # percentile ranks, only among stocks with a value for each field.
    for field, pfield in (("accel_proxy", "accel_pctile"), ("garp_ey", "garp_pctile"),
                          ("timing_proxy", "timing_pctile")):
        ranks = _percentile_ranks(_CN["rows"], field)
        for r in _CN["rows"]:
            r[pfield] = ranks.get(r["symbol"])

    ensure_cn_ownership_cache()
    own_map = _CN_OWN["map"]
    _own_keys = ("inst_min", "inst_max", "top3_max", "strategic_max")
    if own_map:
        for r in _CN["rows"]:
            info = own_map.get(r["symbol"]) or {}
            r["inst_pct"] = info.get("inst_pct")
            r["strategic_pct"] = info.get("strategic_pct")
            r["top3_conc"] = info.get("top3_conc")
            r["own_n_holders"] = info.get("n_holders")
    elif any(criteria.get(k) not in (None, "") for k in _own_keys):
        return {"status": "building", "own_status": cn_ownership_status()}

    from . import cn_reports
    cn_reports.ensure_reports_cache()
    # Only once the whole 180-day window is in. The backfill lands newest-first
    # over hours or days; counts taken off a partial window are labelled "180d"
    # and are simply too low.
    reports_map = cn_reports.build_reports_map() if cn_reports.reports_status().get("complete") else {}
    _report_keys = ("reportcov_min", "reportinst_min", "report_positive_only", "report_initiated_only")
    if reports_map:
        for r in _CN["rows"]:
            info = reports_map.get(r["symbol"]) or {}
            r["report_coverage"] = info.get("coverage_count")
            r["report_inst_count"] = info.get("coverage_inst_count")
            r["report_rating_bucket"] = info.get("latest_rating_bucket")
            r["report_initiated"] = info.get("initiated_recently")
            r["report_consensus"] = info.get("consensus")
    elif any(criteria.get(k) not in (None, "", False) for k in _report_keys):
        return {"status": "building", "report_status": cn_reports.reports_status()}

    _sb = _merge_stage("cn", _CN["rows"], criteria)
    if _sb is not None:
        return _sb

    res = _apply(_CN["rows"], criteria)
    res["currency"] = "¥"
    res["built_at"] = _CN["ts"]
    res["trade_date"] = _CN["date"]
    res["fully_ready"] = True
    res["roe_status"] = cn_roe_status()
    res["own_status"] = cn_ownership_status()
    res["report_status"] = cn_reports.reports_status()
    from . import price_history as _ph
    res["stage_status"] = _ph.price_history_status("cn")
    return res


def filter_stocks(market: str, criteria: dict) -> dict:
    return filter_cn(criteria) if market == "cn" else filter_us(criteria)


# ── Meta (sectors / industries for the UI) ────────────────────────────────────


def _stage_meta_for(market: str) -> dict:
    """Kick off the weekly price-history build when the Filter page opens (same
    warm-on-open behaviour as the ROE/ownership caches) and report progress."""
    from . import price_history, stage_analysis
    price_history.ensure_price_history(market)
    st = price_history.price_history_status(market)
    st.update(stage_analysis.stage_meta(market))
    return st


def meta(market: str) -> dict:
    if market == "cn":
        ensure_cn_snapshot()
        rows = _CN["rows"]
        ensure_cn_roe_cache()   # kick off the background ROE build as soon as the page opens
        ensure_cn_ownership_cache()
        from . import cn_reports
        cn_reports.ensure_reports_cache()
        industries = sorted({r["industry"] for r in rows if r["industry"]})
        return {"status": "ready", "ready": bool(rows),
                "sectors": [], "industries": industries,
                "universe": len(rows), "currency": "¥",
                "roe_status": cn_roe_status(), "own_status": cn_ownership_status(),
                "report_status": cn_reports.reports_status(),
                "stage_status": _stage_meta_for("cn")}
    ensure_us_snapshot()
    rows = _US["rows"]
    if _US["meta_ready"]:
        ensure_us_ownership_cache()   # kick off the background ownership build as soon as the page opens
        ensure_us_earnings_cache()
    sectors = sorted({r["sector"] for r in rows if r["sector"]})
    industries = sorted({r["industry"] for r in rows if r["industry"]})
    return {"status": "ready" if _US["meta_ready"] else "building",
            "ready": _US["ready"], "meta_ready": _US["meta_ready"],
            "sectors": sectors, "industries": industries,
            "universe": len(rows), "currency": "$",
            "own_status": us_ownership_status(), "earn_status": us_earnings_status(),
            "stage_status": _stage_meta_for("us"),
            **us_status()}


# ══════════════════════════════════════════════════════════════════════════════
# Momentum concentration — "is the momentum trade crowded into one sector?"
# Phase-1 proxy for the Indicators Pool's MS-R3 (theme concentration). Reuses
# the sector/industry + 12-month-momentum fields the Filter snapshot already
# carries for the whole universe, so this costs nothing extra to compute — no
# new data source, no background job. NOT CoMomentum (MS-R2): that needs
# weekly return history + a per-stock factor regression this product doesn't
# have yet. This only answers "how concentrated is the winner/loser leg by
# sector," an HHI over today's snapshot — a single-day read, no trend history.
# ══════════════════════════════════════════════════════════════════════════════

_MOM_CONC_MCAP_FLOOR = {"us": 0.3e9, "cn": 5e9}   # same floor as the Fast Grower preset

# Horizon → row field, per market. CN horizons are true point-to-point returns
# (cheap: Tushare snapshots the whole market at one past date per call). US has
# no free true 1m/3m point-to-point return; "50d"/"200d" are price-vs-own-moving-
# -average proxies (≈2.5mo / ≈9-10mo), not point-to-point returns — labeled as
# such in the API response, not disguised as a real calendar-month figure.
_MOM_CONC_HORIZONS = {
    "us": {"50d": "mom_50d", "200d": "mom_200d", "52w": "mom_52w"},
    "cn": {"1m": "mom_1m", "3m": "mom_3m", "6m": "mom_6m", "12m": "mom_52w"},
}
_MOM_CONC_DEFAULT_HORIZON = {"us": "52w", "cn": "12m"}


def _leg_concentration(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        ind = r.get("industry") or r.get("sector") or "Unclassified"
        counts[ind] = counts.get(ind, 0) + 1
    n = len(rows)
    breakdown = sorted(
        ({"industry": k, "count": v, "share": round(v / n * 100, 1)} for k, v in counts.items()),
        key=lambda x: -x["share"],
    )
    hhi = sum((v / n) ** 2 for v in counts.values())
    if hhi < 0.15:
        status = "positive"
    elif hhi < 0.25:
        status = "caution"
    else:
        status = "danger"
    top = breakdown[0] if breakdown else {"industry": "—", "share": 0, "count": 0}
    return {
        "n": n, "hhi": round(hhi, 3), "status": status,
        "top_industry": top["industry"], "top_share": top["share"],
        "breakdown": breakdown,   # full list — callers slice for display as needed
    }


def momentum_concentration(market: str, horizon: str | None = None) -> dict:
    horizons = _MOM_CONC_HORIZONS.get(market, _MOM_CONC_HORIZONS["us"])
    horizon = horizon if horizon in horizons else _MOM_CONC_DEFAULT_HORIZON.get(market, "52w")
    mom_field = horizons[horizon]

    if market == "cn":
        ensure_cn_snapshot()
        rows, currency, built_at = _CN["rows"], "¥", _CN["ts"]
    else:
        ensure_us_snapshot()
        rows, currency, built_at = _US["rows"], "$", _US["ts"]
        if not _US["ready"]:
            return {"status": "building", **us_status()}
    if not rows:
        return {"status": "error", "error": "Market snapshot not available yet."}

    floor = _MOM_CONC_MCAP_FLOOR.get(market, 0)
    valid = [r for r in rows
             if r.get(mom_field) is not None and (r.get("market_cap") or 0) >= floor
             and (r.get("industry") or r.get("sector"))]
    decile = max(10, round(len(valid) * 0.10))
    if len(valid) < decile * 2:
        return {"status": "error", "error": "Not enough scored stocks yet to form deciles.",
                "horizon": horizon, "available_horizons": list(horizons)}
    ranked = sorted(valid, key=lambda r: r[mom_field], reverse=True)
    winner, loser = ranked[:decile], ranked[-decile:]
    winner_leg, loser_leg = _leg_concentration(winner), _leg_concentration(loser)
    winner_leg["breakdown"] = winner_leg["breakdown"][:6]
    loser_leg["breakdown"] = loser_leg["breakdown"][:6]
    return {
        "horizon": horizon, "available_horizons": list(horizons),
        "status": "ready", "currency": currency, "built_at": built_at,
        "universe_n": len(valid), "decile_n": decile,
        "winner": winner_leg,
        "loser": loser_leg,
    }


def momentum_concentration_series(market: str, top_n: int = 6) -> dict:
    """All horizons at once (for the line chart) — reuses the same in-memory
    snapshot, so this is one extra aggregation pass, not new network calls."""
    horizons = _MOM_CONC_HORIZONS.get(market, _MOM_CONC_HORIZONS["us"])

    if market == "cn":
        ensure_cn_snapshot()
        rows, currency, built_at = _CN["rows"], "¥", _CN["ts"]
    else:
        ensure_us_snapshot()
        rows, currency, built_at = _US["rows"], "$", _US["ts"]
        if not _US["ready"]:
            return {"status": "building", **us_status()}
    if not rows:
        return {"status": "error", "error": "Market snapshot not available yet."}

    floor = _MOM_CONC_MCAP_FLOOR.get(market, 0)
    per_horizon: dict[str, dict | None] = {}
    for hz, field in horizons.items():
        valid = [r for r in rows
                 if r.get(field) is not None and (r.get("market_cap") or 0) >= floor
                 and (r.get("industry") or r.get("sector"))]
        decile = max(10, round(len(valid) * 0.10))
        if len(valid) < decile * 2:
            per_horizon[hz] = None
            continue
        ranked = sorted(valid, key=lambda r: r[field], reverse=True)
        per_horizon[hz] = {
            "winner": _leg_concentration(ranked[:decile]),
            "loser": _leg_concentration(ranked[-decile:]),
        }

    hz_list = list(horizons)

    def _series_for(leg_key: str) -> dict:
        shares: dict[str, dict[str, float]] = {}
        for hz in hz_list:
            leg = per_horizon.get(hz)
            if not leg:
                continue
            for item in leg[leg_key]["breakdown"]:
                shares.setdefault(item["industry"], {})[hz] = item["share"]
        top_industries = sorted(shares.items(), key=lambda kv: -max(kv[1].values()))[:top_n]
        series = [{"industry": ind, "values": [hz_map.get(hz, 0) for hz in hz_list]}
                  for ind, hz_map in top_industries]
        hhi_line = [(per_horizon[hz][leg_key]["hhi"] if per_horizon.get(hz) else None) for hz in hz_list]
        return {"series": series, "hhi": hhi_line}

    return {
        "status": "ready", "currency": currency, "built_at": built_at,
        "horizons": hz_list,
        "winner": _series_for("winner"),
        "loser": _series_for("loser"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — "confirm real growth" (real trailing EPS CAGR for a shortlist)
# ══════════════════════════════════════════════════════════════════════════════

def _cagr(a: float | None, b: float | None, n: int) -> float | None:
    if a is None or b is None or n <= 0 or a <= 0 or b <= 0:
        return None
    return round(((b / a) ** (1.0 / n) - 1.0) * 100, 1)


def _eps_growth_from_series(vals: list[float]) -> dict:
    """3Y & 5Y EPS CAGR from an oldest→newest annual EPS list."""
    g3 = _cagr(vals[-4], vals[-1], 3) if len(vals) >= 4 else (
         _cagr(vals[0], vals[-1], len(vals) - 1) if len(vals) >= 2 else None)
    g5 = _cagr(vals[-5], vals[-1], 4) if len(vals) >= 5 else None
    return {"real_growth_3y": g3, "real_growth_5y": g5, "eps_years": len(vals)}


def _confirm_us(tickers: list[str]) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf
    from .screener import _annual_eps_series

    def one(sym: str):
        try:
            eps = _annual_eps_series(yf.Ticker(sym))
            vals = [e["eps"] for e in eps]
            return sym, _eps_growth_from_series(vals)
        except Exception:
            return sym, {"real_growth_3y": None, "real_growth_5y": None, "eps_years": 0}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, res in ex.map(one, tickers):
            out[sym] = res
    return {"status": "ready", "growth": out}


def _confirm_cn(tickers: list[str]) -> dict:
    from datetime import date, timedelta
    from . import cn_data
    start = (date.today() - timedelta(days=365 * 6)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    out: dict[str, dict] = {}
    for ts in tickers:
        try:
            rows = cn_data.call("fina_indicator",
                                {"ts_code": ts, "start_date": start, "end_date": end},
                                "ts_code,end_date,eps")
            ann: dict[str, float] = {}
            for r in rows:
                ed = r.get("end_date") or ""
                v = r.get("eps")
                if ed.endswith("1231") and ed not in ann and v not in (None, ""):
                    try:
                        ann[ed] = float(v)
                    except (TypeError, ValueError):
                        pass
            vals = [ann[y] for y in sorted(ann)]
            out[ts] = _eps_growth_from_series(vals)
        except Exception:
            out[ts] = {"real_growth_3y": None, "real_growth_5y": None, "eps_years": 0}
    return {"status": "ready", "growth": out}


def confirm_growth(market: str, tickers: list[str]) -> dict:
    """Fetch REAL trailing EPS CAGR for a shortlist (capped at 60)."""
    tickers = [t.strip() for t in tickers if t.strip()][:60]
    if not tickers:
        return {"status": "ready", "growth": {}}
    return _confirm_cn(tickers) if market == "cn" else _confirm_us(tickers)


# ── Fundamental ranking (F-Score, Magic Formula) for a shortlist ──────────────

def _magic_rank(scored: dict[str, dict]) -> dict[str, int]:
    """Greenblatt combined rank: rank by earnings yield + by return on capital,
    sum the two ranks, lowest total = best (rank 1)."""
    eys = [(s, d.get("earnings_yield")) for s, d in scored.items() if d.get("earnings_yield") is not None]
    rocs = [(s, d.get("roc")) for s, d in scored.items() if d.get("roc") is not None]
    ey_rank = {s: i for i, (s, _) in enumerate(sorted(eys, key=lambda x: -x[1]))}
    roc_rank = {s: i for i, (s, _) in enumerate(sorted(rocs, key=lambda x: -x[1]))}
    combined = {s: ey_rank[s] + roc_rank[s] for s in scored if s in ey_rank and s in roc_rank}
    order = sorted(combined, key=lambda s: combined[s])
    return {s: i + 1 for i, s in enumerate(order)}


def _two_stage_fcfe(base_fcf: float, g1: float, fade: int, gterm: float, r: float) -> float:
    """Python port of the client-side two-stage FCFE DCF (app.js _dcfEquityValue):
    growth fades linearly g1→gterm over `fade` years, then a Gordon terminal value.
    Returns total equity value in the same currency as base_fcf."""
    fcf, pv = base_fcf, 0.0
    for t in range(1, fade + 1):
        g = g1 + (gterm - g1) * (t - 1) / (fade - 1) if fade > 1 else g1
        fcf = fcf * (1 + g / 100)
        pv += fcf / (1 + r / 100) ** t
    if r > gterm:
        tv = fcf * (1 + gterm / 100) / ((r - gterm) / 100)
        pv += tv / (1 + r / 100) ** fade
    return pv


def _score_us(tickers: list[str]) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf
    from . import screener
    from .screener import _annual_eps_series, _row_at, _cagr

    by_sym = {r["symbol"]: r for r in _US["rows"]}

    def one(sym: str):
        try:
            row = by_sym.get(sym, {})
            mc, px = row.get("market_cap"), row.get("price")
            sec, ind = (row.get("sector") or "").lower(), (row.get("industry") or "").lower()
            is_fin = "financ" in sec or any(x in ind for x in ("bank", "insurance", "capital markets"))
            t = yf.Ticker(sym)
            fin, bs, cf = t.income_stmt, t.balance_sheet, t.cashflow
            td = _row_at(bs, 0, "Total Debt")
            cash = _row_at(bs, 0, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
            ev = (mc + (td or 0) - (cash or 0)) if mc else None
            eps = _row_at(fin, 0, "Diluted EPS", "Basic EPS")
            fs = screener._fundamental_scores({"trailingEps": eps}, fin, bs, cf, mc, px, ev, is_financial=is_fin)
            vals = [e["eps"] for e in _annual_eps_series(t)]
            g3 = _cagr(vals[-4], vals[-1], 3) if len(vals) >= 4 else (_cagr(vals[0], vals[-1], len(vals)-1) if len(vals) >= 2 else None)
            out = {"real_growth_3y": g3}
            if fs:
                out["f_score"] = fs["piotroski"]["score"] if fs.get("piotroski") else None
                out["earnings_yield"] = fs["value"]["earnings_yield"] if fs.get("value") else None
                out["roc"] = fs["value"]["roc"] if fs.get("value") else None
                out["altman_z"] = fs["altman"].get("z") if (fs.get("altman") and fs["altman"].get("applicable") is not False) else None
                out["graham_mos"] = fs["graham"].get("margin_of_safety_pct") if fs.get("graham") else None
            # ── quality lens: ROIC, gross-profitability, accruals, gross margin ──
            gp = _row_at(fin, 0, "Gross Profit"); rev = _row_at(fin, 0, "Total Revenue")
            ta = _row_at(bs, 0, "Total Assets")
            ni = _row_at(fin, 0, "Net Income", "Net Income From Continuing Operation Net Minority Interest")
            ocf = _row_at(cf, 0, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
            equity = _row_at(bs, 0, "Stockholders Equity", "Common Stock Equity")
            ebit = _row_at(fin, 0, "EBIT", "Operating Income")
            tprov, pretax = _row_at(fin, 0, "Tax Provision"), _row_at(fin, 0, "Pretax Income")
            tax = min(max(tprov / pretax, 0.0), 0.35) if (tprov and pretax and pretax > 0) else 0.21
            ic = (equity or 0) + (td or 0) - (cash or 0)
            out["roic"]         = round(ebit * (1 - tax) / ic * 100, 1) if (ebit is not None and ic > 0) else None
            out["gp_assets"]    = round(gp / ta * 100, 1) if (gp is not None and ta) else None
            out["gross_margin"] = round(gp / rev * 100, 1) if (gp is not None and rev) else None
            out["accruals"]     = round((ni - ocf) / ta * 100, 1) if (ni is not None and ocf is not None and ta) else None
            # ── batch two-stage DCF → intrinsic value + margin of safety (US) ──
            try:
                info = t.info or {}
                dcf = screener._safe_dcf(info, fin, bs, cf, mc, px, out.get("roic"), is_financial=is_fin)
                if dcf and dcf.get("applicable") and dcf.get("shares") and px:
                    eq = _two_stage_fcfe(dcf["base_fcf"], dcf["default_stage1_growth"],
                                         dcf["default_fade_years"], dcf["default_terminal"],
                                         dcf["cost_of_equity"])
                    iv = eq / dcf["shares"]
                    # sanity guard: an intrinsic value wildly off the price (iv/px
                    # outside ~[0.1, 8]) almost always signals a currency mismatch on
                    # foreign ADRs (FCF in local currency vs USD price/shares, e.g. PBR
                    # in BRL) or broken FCF — not a real 90%+ margin of safety. Suppress
                    # it rather than pollute the MoS filter (mirrors the earnings-yield guard).
                    if iv > 0 and 0.1 <= iv / px <= 8:
                        out["dcf_iv"] = round(iv, 2)
                        out["dcf_mos"] = round((iv - px) / iv * 100, 1)
            except Exception:
                pass
            # ── earnings beat rate + next report date (yfinance earnings_dates) ──
            try:
                ed = t.earnings_dates
                if ed is not None and not ed.empty:
                    rep = ed[ed["Reported EPS"].notna()].head(8)   # ≤8 most-recent reported qtrs
                    beats = tot = 0
                    for _, r in rep.iterrows():
                        est, act = r.get("EPS Estimate"), r.get("Reported EPS")
                        if est is not None and act is not None and est == est and act == act:
                            tot += 1
                            if float(act) > float(est):
                                beats += 1
                    if tot:
                        out["beat_rate"] = round(beats / tot * 100)
                        out["beat_count"] = beats
                        out["beat_n"] = tot
                    fut = ed[ed["Reported EPS"].isna()]             # upcoming (not yet reported)
                    if not fut.empty:
                        out["next_earnings"] = fut.index[-1].strftime("%b %d, %Y")
            except Exception:
                pass
            # ── Tier-A: analyst estimate momentum + forward growth (eps_trend, 1 call) ──
            try:
                et = t.eps_trend
                if et is not None and not et.empty:
                    def _cell(row, col):
                        try:
                            v = et.loc[row, col]
                            return float(v) if v == v else None   # NaN-safe
                        except Exception:
                            return None
                    cy, cy90 = _cell("0y", "current"), _cell("0y", "90daysAgo")   # current fiscal yr
                    if cy is not None and cy90 is not None and abs(cy90) > 0.5:
                        out["est_rev"] = round((cy - cy90) / abs(cy90) * 100, 1)   # +ve = consensus rising
                    ny = _cell("+1y", "current")                                   # next fiscal yr
                    if cy is not None and ny is not None and abs(cy) > 0.5:
                        out["fwd_growth_a"] = round((ny - cy) / abs(cy) * 100, 1)  # analyst next-yr EPS growth
            except Exception:
                pass
            # ── Tier-A: analyst target upside + ownership/short (from info, already cached) ──
            try:
                inf = t.info or {}
                tgt, px2 = inf.get("targetMeanPrice"), (inf.get("currentPrice") or px)
                if tgt and px2:
                    out["target_upside"] = round((tgt / px2 - 1) * 100, 1)
                out["n_analysts"] = inf.get("numberOfAnalystOpinions")
                out["rec_key"] = inf.get("recommendationKey")
                for k_out, k_in in (("short_pct", "shortPercentOfFloat"),
                                    ("insider_pct", "heldPercentInsiders"),
                                    ("inst_pct", "heldPercentInstitutions")):
                    v = inf.get(k_in)
                    out[k_out] = round(v * 100, 1) if v is not None else None
            except Exception:
                pass
            # ── Tier-A: R&D intensity + net buyback (from statements already fetched) ──
            try:
                rd, rev0 = _row_at(fin, 0, "Research And Development"), _row_at(fin, 0, "Total Revenue")
                if rd is not None and rev0:
                    out["rd_intensity"] = round(rd / rev0 * 100, 1)
            except Exception:
                pass
            try:
                s0 = _row_at(bs, 0, "Ordinary Shares Number", "Share Issued")
                s1 = _row_at(bs, 1, "Ordinary Shares Number", "Share Issued")
                if s0 and s1:
                    out["net_buyback"] = round(-(s0 - s1) / s1 * 100, 1)   # +ve = share count shrank (buyback)
            except Exception:
                pass
            # ── SBC intensity (stock comp is a real cost FCF hides; from cashflow already fetched) ──
            try:
                sbc = _row_at(cf, 0, "Stock Based Compensation")
                rev1 = _row_at(fin, 0, "Total Revenue")
                if sbc is not None and rev1:
                    out["sbc_rev"] = round(sbc / rev1 * 100, 1)
                    fcf1 = _row_at(cf, 0, "Free Cash Flow")
                    if fcf1 and fcf1 > 0:
                        out["sbc_fcf"] = round(sbc / fcf1 * 100)   # % of FCF that is SBC add-back
            except Exception:
                pass
            # ── insider net buying, last 6 months (% of insider-held shares) ──
            try:
                ip = t.insider_purchases
                if ip is not None and not ip.empty:
                    col0 = ip.columns[0]
                    row = ip[ip[col0].astype(str).str.startswith("% Net Shares Purchased")]
                    if not row.empty:
                        v = row.iloc[0]["Shares"]
                        if v == v and v is not None:               # NaN-safe
                            out["insider_net"] = round(float(v) * 100, 1)
            except Exception:
                pass
            return sym, out
        except Exception:
            return sym, {}

    scored: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, d in ex.map(one, tickers):
            scored[sym] = d
    for s, r in _magic_rank(scored).items():
        scored[s]["magic_rank"] = r
    return {"status": "ready", "scores": scored}


def _score_cn(tickers: list[str]) -> dict:
    from datetime import date, timedelta
    from . import cn_data
    from .ashare_screener import _fundamental_scores_ashare
    ensure_cn_snapshot()
    by_sym = {r["symbol"]: r for r in _CN["rows"]}
    start = (date.today() - timedelta(days=365 * 6)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    scored: dict[str, dict] = {}
    for ts in tickers:
        try:
            row = by_sym.get(ts, {})
            mc, px = row.get("market_cap"), row.get("price")
            is_fin = any(k in (row.get("industry") or "") for k in ("银行", "保险", "证券"))
            bs = cn_data.call("balancesheet", {"ts_code": ts, "start_date": start, "end_date": end},
                              "ts_code,end_date,total_assets,total_liab,total_cur_assets,total_cur_liab,"
                              "st_borr,lt_borr,bond_payable,non_cur_liab_due_1y,lease_liab,money_cap,"
                              "undistr_porfit,accounts_receiv,fix_assets")
            inc = cn_data.call("income", {"ts_code": ts, "start_date": start, "end_date": end},
                               "ts_code,end_date,total_revenue,oper_cost,operate_profit,n_income,sell_exp,admin_exp")
            cf = cn_data.call("cashflow", {"ts_code": ts, "start_date": start, "end_date": end},
                              "ts_code,end_date,n_cashflow_act,free_cashflow,depr_fa_coga_dpba")
            fi = cn_data.call("fina_indicator", {"ts_code": ts, "start_date": start, "end_date": end},
                              "ts_code,end_date,eps,bps,current_ratio,grossprofit_margin,roic")
            fs = _fundamental_scores_ashare(bs, inc, cf, fi, mc, px, is_fin)
            out = {}
            # ── quality lens (Tushare pre-computes roic & gross margin) ──────
            def _latest(rows):
                a = [r for r in rows if (r.get("end_date") or "").endswith("1231")]
                a.sort(key=lambda r: r["end_date"], reverse=True)
                return a[0] if a else {}
            def _nn(d, k):
                v = d.get(k)
                try:
                    return float(v) if v not in (None, "") else None
                except (TypeError, ValueError):
                    return None
            fl, il, bl, cl = _latest(fi), _latest(inc), _latest(bs), _latest(cf)
            out["roic"]         = round(_nn(fl, "roic"), 1) if _nn(fl, "roic") is not None else None
            out["gross_margin"] = round(_nn(fl, "grossprofit_margin"), 1) if _nn(fl, "grossprofit_margin") is not None else None
            rev, oc, ta = _nn(il, "total_revenue"), _nn(il, "oper_cost"), _nn(bl, "total_assets")
            gp = (rev - oc) if (rev is not None and oc is not None) else None
            out["gp_assets"] = round(gp / ta * 100, 1) if (gp is not None and ta) else None
            ni2, ocf2 = _nn(il, "n_income"), _nn(cl, "n_cashflow_act")
            out["accruals"] = round((ni2 - ocf2) / ta * 100, 1) if (ni2 is not None and ocf2 is not None and ta) else None
            if fs:
                out["f_score"] = fs["piotroski"]["score"] if fs.get("piotroski") else None
                out["earnings_yield"] = fs["value"]["earnings_yield"] if fs.get("value") else None
                out["roc"] = fs["value"]["roc"] if fs.get("value") else None
                out["altman_z"] = fs["altman"].get("z") if (fs.get("altman") and fs["altman"].get("applicable") is not False) else None
                out["graham_mos"] = fs["graham"].get("margin_of_safety_pct") if fs.get("graham") else None
                if fi:
                    ann = sorted([r for r in fi if (r.get("end_date") or "").endswith("1231")], key=lambda r: r["end_date"])
                    vals = []
                    for r in ann:
                        try:
                            v = r.get("eps")
                            if v not in (None, ""):
                                vals.append(float(v))
                        except (TypeError, ValueError):
                            pass
                    if len(vals) >= 4:
                        a, b = vals[-4], vals[-1]
                        out["real_growth_3y"] = round(((b/a)**(1/3)-1)*100, 1) if (a > 0 and b > 0) else None
            scored[ts] = out
        except Exception:
            scored[ts] = {}
    for s, r in _magic_rank(scored).items():
        scored[s]["magic_rank"] = r
    return {"status": "ready", "scores": scored}


def score_shortlist(market: str, tickers: list[str]) -> dict:
    """Stage-2 fundamental scoring for a shortlist: F-Score, Earnings Yield, ROC,
    Magic-Formula rank, real 3Y growth. US up to 50, A-share up to 25 (Tushare)."""
    tickers = [t.strip() for t in tickers if t.strip()]
    if not tickers:
        return {"status": "ready", "scores": {}}
    return _score_cn(tickers[:25]) if market == "cn" else _score_us(tickers[:50])


# ══════════════════════════════════════════════════════════════════════════════
# Tier 3 — cross-sectional composite z-score ranking (Value + Momentum + Size)
# ══════════════════════════════════════════════════════════════════════════════

import math
import statistics


def _zmap(pairs: list[tuple]) -> dict:
    """(symbol, value) → rank-normalized score in ~[−1.7, +1.7]. Rank-based
    standardization is immune to fat tails (a stock up 900% or a P/B of 0.04
    just takes the top rank; it can't dominate the way a raw z-score would)."""
    n = len(pairs)
    if n < 5:
        return {}
    order = sorted(pairs, key=lambda kv: kv[1])
    return {sym: ((i + 0.5) / n - 0.5) * 3.4641 for i, (sym, _) in enumerate(order)}


def compute_composite(market: str, weights: dict | None = None, limit: int = 100) -> dict:
    """Rank the whole universe by a Value + Momentum + Size composite z-score.
    Value = avg z of earnings-yield (1/PE), book-to-price (1/PB), sales-to-price (1/PS);
    Momentum = z of 12-mo return; Size = z of −log(market cap) (small-cap tilt)."""
    if market == "cn":
        ensure_cn_snapshot()
        rows = _CN["rows"]
        cur = "¥"
    else:
        ensure_us_snapshot()
        if not _US["ready"]:
            return {"status": "building", **us_status()}
        rows = _US["rows"]
        cur = "$"
    if not rows:
        return {"status": "error", "error": "Universe snapshot unavailable."}

    # drop micro/nano-caps whose ratios & momentum are noisy/speculative
    floor = 3e9 if market == "cn" else 3e8
    rows = [r for r in rows if (r.get("market_cap") or 0) >= floor]

    w = {"value": 0.5, "growth": 0.0, "momentum": 0.3, "size": 0.2}   # value-tilted default
    if weights:
        for k in w:
            if weights.get(k) is not None:
                w[k] = float(weights[k])

    ep, bp, sp, mom, size, grow = [], [], [], [], [], []
    for r in rows:
        s = r["symbol"]
        pe, pb, ps = r.get("pe"), r.get("pb"), r.get("ps")
        mc, mm, ig = r.get("market_cap"), r.get("mom_52w"), r.get("impl_growth")
        if pe and pe > 0: ep.append((s, 1.0 / pe))
        if pb and pb > 0: bp.append((s, 1.0 / pb))
        if ps and ps > 0: sp.append((s, 1.0 / ps))
        if mm is not None: mom.append((s, mm))
        if mc and mc > 0:  size.append((s, -math.log(mc)))
        # Growth factor = implied fwd EPS growth (US only; A-share has no fwd data)
        if ig is not None and -50 < ig < 200: grow.append((s, ig))

    z_ep, z_bp, z_sp = _zmap(ep), _zmap(bp), _zmap(sp)
    z_mom, z_size, z_grow = _zmap(mom), _zmap(size), _zmap(grow)

    out = []
    for r in rows:
        s = r["symbol"]
        vzs = [z for z in (z_ep.get(s), z_bp.get(s), z_sp.get(s)) if z is not None]
        value = sum(vzs) / len(vzs) if vzs else None
        if value is None:                   # require a value score to rank
            continue
        momz, sizez, growz = z_mom.get(s), z_size.get(s), z_grow.get(s)
        # A factor with positive weight but missing data contributes 0 (neutral)
        # while still counting in the denominator — so e.g. a stock with no
        # forward-growth estimate can't top a GARP screen on value+size alone.
        parts, wsum = 0.0, 0.0
        for fz, wt in ((value, w["value"]), (growz, w["growth"]),
                       (momz, w["momentum"]), (sizez, w["size"])):
            if wt > 0:
                parts += wt * (fz if fz is not None else 0.0)
                wsum += wt
        if wsum <= 0:
            continue
        out.append({
            "symbol": s, "name": r.get("name"), "sector": r.get("sector"),
            "industry": r.get("industry"), "market_cap": r.get("market_cap"),
            "price": r.get("price"), "pe": r.get("pe"), "pb": r.get("pb"),
            "mom_52w": r.get("mom_52w"), "div_yield": r.get("div_yield"),
            "impl_growth": r.get("impl_growth"),
            "f_value": round(value, 2),
            "f_growth": round(growz, 2) if growz is not None else None,
            "f_momentum": round(momz, 2) if momz is not None else None,
            "f_size": round(sizez, 2) if sizez is not None else None,
            "composite": round(parts / wsum, 3),
        })
    out.sort(key=lambda x: -x["composite"])
    n = len(out)
    for i, r in enumerate(out):
        r["rank"] = i + 1
        r["percentile"] = round((1 - i / n) * 100, 1) if n else 0

    try:
        lim = max(10, min(300, int(limit)))
    except (TypeError, ValueError):
        lim = 100
    return {"status": "ready", "count": n, "universe": len(rows),
            "results": out[:lim], "weights": w, "currency": cur}


# ══════════════════════════════════════════════════════════════════════════════
# v1 basket backtest — equal-weight buy-and-hold from a start date vs. benchmark
# ══════════════════════════════════════════════════════════════════════════════

def _perf_stats(dates: list[str], curve: list[float]) -> dict:
    """CAGR, annualised vol, max drawdown, Sharpe from a normalised equity curve."""
    if len(curve) < 2:
        return {}
    total_ret = curve[-1] / curve[0] - 1
    years = max((len(curve)) / 252.0, 1e-6)
    cagr = (curve[-1] / curve[0]) ** (1 / years) - 1
    rets = [curve[i] / curve[i-1] - 1 for i in range(1, len(curve)) if curve[i-1]]
    vol = (statistics.pstdev(rets) * (252 ** 0.5)) if len(rets) > 1 else 0.0
    mean_daily = statistics.fmean(rets) if rets else 0.0
    sharpe = (mean_daily * 252) / vol if vol else 0.0
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"total_return": round(total_ret * 100, 1), "cagr": round(cagr * 100, 1),
            "vol": round(vol * 100, 1), "max_drawdown": round(mdd * 100, 1),
            "sharpe": round(sharpe, 2)}


def _backtest_us(tickers: list[str], start: str, benchmark: str) -> dict:
    import yfinance as yf
    import pandas as pd
    syms = tickers + [benchmark]
    data = yf.download(syms, start=start, auto_adjust=True, progress=False)
    close = data["Close"] if "Close" in data else data
    close = close.dropna(how="all")
    bench = close[benchmark].dropna()
    port_syms = [s for s in tickers if s in close.columns and close[s].notna().sum() > 20]
    if not port_syms or bench.empty:
        return {"status": "error", "error": "Not enough price history for these tickers."}
    # equal-weight buy-and-hold: normalise each to 1 at first valid point, average
    norm = close[port_syms].apply(lambda c: c / c.dropna().iloc[0])
    port = norm.mean(axis=1).dropna()
    bench_norm = (bench / bench.dropna().iloc[0]).reindex(port.index).ffill()
    dates = [d.strftime("%Y-%m-%d") for d in port.index]
    pcurve = [round(float(v), 4) for v in port.values]
    bcurve = [round(float(v), 4) for v in bench_norm.values]
    # downsample for transport (~250 pts)
    step = max(1, len(dates) // 250)
    return {
        "status": "ready",
        "dates": dates[::step], "portfolio": pcurve[::step], "benchmark": bcurve[::step],
        "portfolio_stats": _perf_stats(dates, pcurve),
        "benchmark_stats": _perf_stats(dates, bcurve),
        "n_holdings": len(port_syms), "benchmark_name": benchmark,
        "start": dates[0], "end": dates[-1],
    }


def _backtest_cn(tickers: list[str], start: str, benchmark: str) -> dict:
    from . import cn_data
    start_c = start.replace("-", "")
    def hfq_series(ts_code):
        rows = cn_data.call("daily", {"ts_code": ts_code, "start_date": start_c},
                            "ts_code,trade_date,close")
        adj = cn_data.call("adj_factor", {"ts_code": ts_code, "start_date": start_c},
                           "ts_code,trade_date,adj_factor")
        af = {a["trade_date"]: _to_float(a.get("adj_factor")) for a in adj}
        ser = {}
        for r in rows:
            d, c = r.get("trade_date"), _to_float(r.get("close"))
            f = af.get(d)
            if d and c and f:
                ser[d] = c * f            # back-adjusted
        return ser
    # benchmark: CSI300 index
    bidx = cn_data.call("index_daily", {"ts_code": benchmark, "start_date": start_c},
                        "ts_code,trade_date,close")
    bench = {r["trade_date"]: _to_float(r.get("close")) for r in bidx if _to_float(r.get("close"))}
    series = {t: hfq_series(t) for t in tickers}
    series = {t: s for t, s in series.items() if len(s) > 20}
    if not series or not bench:
        return {"status": "error", "error": "Not enough A-share price history for these tickers."}
    all_dates = sorted(set().union(*[set(s) for s in series.values()]) & set(bench))
    if len(all_dates) < 20:
        return {"status": "error", "error": "Insufficient overlapping trading days."}
    # normalise each holding to its first available price on/after start
    base = {t: s[next(d for d in all_dates if d in s)] for t, s in series.items()}
    pcurve, bcurve, last = [], [], {}
    bbase = bench[all_dates[0]]
    for d in all_dates:
        vals = []
        for t, s in series.items():
            if d in s: last[t] = s[d] / base[t]
            if t in last: vals.append(last[t])
        if vals:
            pcurve.append(round(sum(vals) / len(vals), 4))
            bcurve.append(round(bench.get(d, bbase) / bbase, 4))
    dates = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in all_dates[:len(pcurve)]]
    step = max(1, len(dates) // 250)
    return {
        "status": "ready",
        "dates": dates[::step], "portfolio": pcurve[::step], "benchmark": bcurve[::step],
        "portfolio_stats": _perf_stats(dates, pcurve),
        "benchmark_stats": _perf_stats(dates, bcurve),
        "n_holdings": len(series), "benchmark_name": "CSI 300",
        "start": dates[0], "end": dates[-1],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Competitive position — rank/share among LISTED industry peers (moat proxy)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_rev_us(syms: list[str]) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf
    from .screener import _row_series

    def one(sym: str):
        try:
            fin = yf.Ticker(sym).income_stmt
            rev = {r["year"]: r["val"] for r in _row_series(fin, "Total Revenue")}
            gp = {r["year"]: r["val"] for r in _row_series(fin, "Gross Profit")}
            return sym, (rev, gp)
        except Exception:
            return sym, ({}, {})

    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for sym, d in ex.map(one, syms):
            out[sym] = d
    return out


def _fetch_rev_cn(ts_codes: list[str]) -> dict:
    from datetime import date, timedelta
    from . import cn_data
    start = (date.today() - timedelta(days=365 * 6)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    out = {}
    for ts in ts_codes:
        try:
            inc = cn_data.call("income", {"ts_code": ts, "start_date": start, "end_date": end},
                               "ts_code,end_date,total_revenue,revenue,oper_cost")
            rev, gp = {}, {}
            for r in inc:
                ed = r.get("end_date") or ""
                if not ed.endswith("1231"):
                    continue
                yr = int(ed[:4])
                tr = _to_float(r.get("total_revenue"))
                if tr and yr not in rev:
                    rev[yr] = tr
                rv, oc = _to_float(r.get("revenue")), _to_float(r.get("oper_cost"))
                if rv and oc and yr not in gp:
                    gp[yr] = rv - oc
            out[ts] = (rev, gp)
        except Exception:
            out[ts] = ({}, {})
    return out


def competitive_position(market: str, ticker: str) -> dict:
    """Rank & revenue/market-cap share among the stock's LISTED industry peers,
    the 5-yr share trend, and gross margin vs. peer median. NOT true market share
    (excludes private/foreign/non-universe competitors)."""
    if market == "cn":
        ensure_cn_snapshot()
        rows = _CN["rows"]; cur = "¥"
        tq = ticker.strip().upper()
        tgt = next((r for r in rows if r["symbol"].upper() == tq or r["symbol"].split(".")[0] == tq.split(".")[0]), None)
    else:
        ensure_us_snapshot()
        if not _US["ready"]:
            return {"status": "building", **us_status()}
        rows = _US["rows"]; cur = "$"
        tq = ticker.strip().upper()
        tgt = next((r for r in rows if r["symbol"].upper() == tq), None)
    if not tgt:
        return {"status": "error", "error": f"{ticker} not found in the {'A-share' if market=='cn' else 'US'} universe (needs to be an investable common stock)."}

    industry = tgt.get("industry") or tgt.get("sector") or ""
    peers = [r for r in rows if (r.get("industry") or r.get("sector")) == industry and r.get("market_cap")]
    if len(peers) < 3:
        return {"status": "error", "error": f"Only {len(peers)} listed peer(s) in “{industry}” — too few to gauge competitive position."}

    # ── market-cap share (instant, all peers) ─────────────────────────────────
    peers.sort(key=lambda r: -(r["market_cap"] or 0))
    total_mc = sum(r["market_cap"] for r in peers)
    mc_rank = next((i + 1 for i, r in enumerate(peers) if r["symbol"] == tgt["symbol"]), None)
    mc_share = round(tgt["market_cap"] / total_mc * 100, 1) if (tgt.get("market_cap") and total_mc) else None

    # ── revenue share + trend (top ~20 peers by market cap + the target) ──────
    tgt_sym = tgt["symbol"]
    top = list(dict.fromkeys([r["symbol"] for r in peers[:20]] + [tgt_sym]))
    rev_data = _fetch_rev_cn(top) if market == "cn" else _fetch_rev_us(top)
    tgt_rev, tgt_gp = rev_data.get(tgt_sym, ({}, {}))

    # Revenue-share trend on a CONSISTENT peer panel: only peers reporting in
    # both the first and last year of the window count toward the denominator,
    # so the trend reflects real share shifts — not peers entering/leaving the
    # set. (Absolute levels still mix fiscal year-ends, but the panel is fixed,
    # so year-over-year *changes* are apples-to-apples.)
    years = sorted({y for rev, _ in rev_data.values() for y in rev})
    window = sorted(y for y in tgt_rev if tgt_rev.get(y))[-5:]
    share_series = []
    panel_n = 0
    if len(window) >= 2:
        y0, y1 = window[0], window[-1]
        panel = [s for s, (rev, _) in rev_data.items() if rev.get(y0) and rev.get(y1)]
        panel_n = len(panel)
        for y in window:
            tot = sum(rev.get(y, 0) for s, (rev, _) in rev_data.items() if s in panel)
            tr = tgt_rev.get(y)
            if tot and tr:
                share_series.append({"year": y, "share": round(tr / tot * 100, 1)})
    rev_share = share_series[-1]["share"] if share_series else None
    share_trend = None
    if len(share_series) >= 2:
        share_trend = round(share_series[-1]["share"] - share_series[0]["share"], 1)

    # revenue rank among fetched peers, in the target's most recent fiscal year
    rank_y = max(tgt_rev) if tgt_rev else (years[-1] if years else None)
    rev_rank = rev_peers = None
    if rank_y:
        rev_latest = [(s, rev.get(rank_y)) for s, (rev, _) in rev_data.items() if rev.get(rank_y)]
        rev_latest.sort(key=lambda x: -x[1])
        rev_peers = len(rev_latest)
        rev_rank = next((i + 1 for i, (s, _) in enumerate(rev_latest) if s == tgt_sym), None)

    # ── gross margin vs peer median ───────────────────────────────────────────
    def gm(sym):
        rev, gp = rev_data.get(sym, ({}, {}))
        yy = sorted(set(rev) & set(gp))
        if yy and rev.get(yy[-1]):
            return gp[yy[-1]] / rev[yy[-1]] * 100
        return None
    tgt_gm = gm(tgt_sym)
    peer_gms = sorted(g for s in rev_data if s != tgt_sym and (g := gm(s)) is not None)
    peer_gm_median = peer_gms[len(peer_gms) // 2] if peer_gms else None

    top5 = [{"symbol": r["symbol"], "name": r["name"],
             "mcap": r["market_cap"],
             "mcap_share": round(r["market_cap"] / total_mc * 100, 1) if total_mc else None,
             "is_target": r["symbol"] == tgt_sym} for r in peers[:6]]

    return {
        "status": "ready", "currency": cur, "industry": industry,
        "name": tgt.get("name"), "symbol": tgt_sym,
        "n_peers": len(peers),
        "mcap_rank": mc_rank, "mcap_share": mc_share,
        "rev_rank": rev_rank, "rev_peers": rev_peers, "rev_share": rev_share,
        "share_series": share_series, "share_trend": share_trend, "panel_n": panel_n,
        "gross_margin": round(tgt_gm, 1) if tgt_gm is not None else None,
        "peer_gm_median": round(peer_gm_median, 1) if peer_gm_median is not None else None,
        "top_peers": top5,
    }


def backtest_basket(market: str, tickers: list[str], start: str,
                    benchmark: str | None = None) -> dict:
    """Equal-weight buy-and-hold backtest of a basket vs. a benchmark index."""
    tickers = [t.strip() for t in tickers if t.strip()][:30]
    if not tickers:
        return {"status": "error", "error": "No tickers provided."}
    start = start or "2019-01-01"
    if market == "cn":
        return _backtest_cn(tickers, start, benchmark or "000300.SH")
    return _backtest_us(tickers, start, benchmark or "SPY")

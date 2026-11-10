"""Peer comparison — key financial ratios for a stock and its industry peers.

Feeds the "Peer Comparison" card on every stock page (US and A-share).

THE ONE RULE THAT SHAPES EVERYTHING HERE: every row of a comparison table must
be on the SAME basis, or the table misleads more than it informs.

Two vendor traps made that rule concrete when this was built (2026-09-15):

  1. Yahoo's `info` mixes periods. `operatingMargins` is the latest quarter;
     `grossMargins` and `profitMargins` are trailing twelve months. In a steep
     cycle they diverge — Micron showed an operating margin of 80.4% ABOVE its
     gross margin of 72.6%, which is arithmetically impossible on one period.
     So US margins and ROE are computed here from the statements themselves,
     over one consistent window: the last four reported quarters.

  2. Tushare's `roe` is YEAR-TO-DATE. Moutai's H1 2026 `roe` is 17.95; the
     annualised `roe_yearly` is 35.9. Comparing one company's H1 figure with a
     peer's full-year figure would halve or double it. A-share peers are pinned
     to a common reporting period, and ROE uses the annualised field.

PEER SELECTION IS "SAME INDUSTRY", NOT "COMPETITORS"
----------------------------------------------------
US peers come from Yahoo's industry classification, A-share peers from
Tushare's. Both group by what a company is classified as, not who it actually
competes with — Broadcom's industry includes true rivals (NVDA, AMD, MRVL) and
adjacent analog makers (TXN, ADI). The card says so, and lets the user add and
drop tickers to turn the default list into a real competitor set.

...and sometimes the classification is simply wrong. Yahoo files IREN, MARA and
WULF — bitcoin miners turned AI data-centre operators — under "Capital
Markets", so the card compared IREN with Goldman Sachs and Morgan Stanley.
`peer_fit` tests every US cohort against the company's own business
description before it is trusted, and replaces the ones that fail. Nothing
changes for a cohort that passes, which is the overwhelming majority.

Selection: if the stock is among the largest names in its industry, the peers
are the largest others. If it is small, a table of trillion-dollar giants tells
you little, so the list mixes the largest names with the ones nearest in size.
"""
from __future__ import annotations

import json
import math
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import peer_fit

warnings.filterwarnings("ignore")

_DATA = Path(__file__).resolve().parent.parent / "data" / "peers_cache"
_TTL_METRICS = 12 * 3600      # statements move quarterly; prices are not used here
_TTL_SELECT = 24 * 3600
_N_PEERS = 8
_N_LARGEST_WHEN_SMALL = 4
_SCALE_GAP = 0.10             # no peer this close in size -> the median means little

_MEM: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: float) -> Any:
    hit = _MEM.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    f = _DATA / (key.replace("/", "_").replace("^", "_") + ".json")
    if f.exists() and time.time() - f.stat().st_mtime < ttl:
        try:
            v = json.loads(f.read_text())
            _MEM[key] = (f.stat().st_mtime, v)
            return v
        except Exception:                          # noqa: BLE001
            return None
    return None


def _cache_put(key: str, value: Any) -> None:
    _MEM[key] = (time.time(), value)
    try:
        _DATA.mkdir(parents=True, exist_ok=True)
        f = _DATA / (key.replace("/", "_").replace("^", "_") + ".json")
        tmp = f.with_suffix(".partial")             # never truncate a live cache
        tmp.write_text(json.dumps(value))
        tmp.replace(f)
    except Exception:                              # noqa: BLE001
        pass


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _growth(cur: float | None, prior: float | None) -> tuple[float | None, str | None]:
    """Percentage growth, refusing a negative or zero base.

    Growth off a loss has no meaningful percentage: SK hynix went from -1,324
    to +2,842 KRW EPS, and any number printed for that is noise. Returns a
    reason code instead so the UI can say "n/m" rather than show nothing.
    """
    if cur is None or prior is None:
        return None, None
    if prior <= 0:
        return None, "negative_base"
    if cur < 0:
        return None, "turned_to_loss"             # profit -> loss: no meaningful %
    return round((cur / prior - 1) * 100, 1), None


def _select(subject: str, ranked: list[tuple[str, float]], n: int = _N_PEERS) -> tuple[list[str], str]:
    """ranked = [(symbol, size)] largest first, subject included if known."""
    others = [(s, w) for s, w in ranked if s != subject]
    sizes = dict(ranked)
    if subject not in sizes:
        return [s for s, _ in others[:n]], "largest in industry"
    pos = [s for s, _ in ranked].index(subject)
    if pos < n:
        return [s for s, _ in others[:n]], "largest in industry"
    big = [s for s, _ in others[:_N_LARGEST_WHEN_SMALL]]
    me = sizes[subject] or 1e-12
    near = sorted((x for x in others if x[0] not in big),
                  key=lambda x: abs(math.log((x[1] or 1e-12) / me)))
    return big + [s for s, _ in near[:n - len(big)]], "largest + nearest in size"


# ════════════════════════════════════════════════════════════════════════════
# US
# ════════════════════════════════════════════════════════════════════════════
def _us_candidates(symbol: str) -> dict[str, Any]:
    key = f"us_select_{symbol}"
    hit = _cache_get(key, _TTL_SELECT)
    if hit:
        return hit
    import yfinance as yf
    info = yf.Ticker(symbol).info or {}
    ikey = info.get("industryKey")
    out: dict[str, Any] = {"industry": info.get("industry"), "industry_key": ikey,
                           "sector": info.get("sector"), "ranked": []}
    if ikey:
        try:
            tc = yf.Industry(ikey).top_companies
            if tc is not None and not tc.empty:
                rows = []
                for sym, r in tc.iterrows():
                    s = str(sym).upper()
                    if "." in s:                     # foreign-exchange listings: skip on the US page
                        continue
                    rows.append((s, _num(r.get("market weight")) or 0.0))
                out["ranked"] = rows
        except Exception as exc:                   # noqa: BLE001
            out["error"] = f"industry lookup: {type(exc).__name__}: {exc}"
    _cache_put(key, out)
    return out


def _quarter_cols_ok(cols: list) -> bool:
    """Four consecutive quarters, no hole. A missing quarter would silently turn
    'TTM' into fifteen months or nine."""
    if len(cols) < 4:
        return False
    for a, b in zip(cols[:3], cols[1:4]):
        gap = (a - b).days
        if not 70 <= gap <= 110:
            return False
    return True


def _row(df: Any, *names: str) -> Any:
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def _us_metrics(symbol: str) -> dict[str, Any]:
    key = f"us_metrics_v4_{symbol}"   # v4: operating and net income as reported
    hit = _cache_get(key, _TTL_METRICS)
    if hit:
        return hit
    import yfinance as yf
    t = yf.Ticker(symbol)
    m: dict[str, Any] = {"symbol": symbol, "state": "ok", "flags": []}
    try:
        info = t.info or {}
    except Exception:                              # noqa: BLE001
        info = {}
    m.update({
        "name": info.get("shortName") or info.get("longName") or symbol,
        "market_cap": _num(info.get("marketCap")),
        "pe_ttm": _num(info.get("trailingPE")),
        "pe_fwd": _num(info.get("forwardPE")),
        "currency": info.get("currency"),
        "fin_currency": info.get("financialCurrency"),
        # Kept so the peer-classification check costs no extra vendor calls for
        # companies already on the card (see peer_fit.repair).
        "summary": info.get("longBusinessSummary") or "",
    })
    try:
        from .screener import _first_trade_date
        ft = _first_trade_date(info)
        m["first_trade"] = ft.isoformat() if ft else None
    except Exception:                              # noqa: BLE001
        m["first_trade"] = None
    if m["currency"] and m["fin_currency"] and m["currency"] != m["fin_currency"]:
        # Ratios are unit-free and safe. A P/E mixes a price in one currency with
        # earnings in another unless the vendor converted — flag, don't hide.
        m["flags"].append("cross_currency")

    try:
        qi, qb, ai = t.quarterly_income_stmt, t.quarterly_balance_sheet, t.income_stmt
    except Exception as exc:                       # noqa: BLE001
        m.update({"state": "unavailable", "error": f"{type(exc).__name__}: {exc}"})
        _cache_put(key, m)
        return m

    rev = _row(qi, "Total Revenue", "Operating Revenue")
    if rev is None or not _quarter_cols_ok(list(qi.columns)):
        m.update({"state": "insufficient_history",
                  "note": "fewer than four consecutive reported quarters"})
        _cache_put(key, m)
        return m

    cols = list(qi.columns)
    m["period_end"] = cols[0].date().isoformat()

    def ttm(series: Any) -> float | None:
        if series is None:
            return None
        vals = [_num(v) for v in list(series.values)[:4]]
        return None if any(v is None for v in vals) else sum(vals)

    rev_t = ttm(rev)
    gp_t = ttm(_row(qi, "Gross Profit"))
    # As reported, matching the Revenue & Profit card (see rev_profit._US_OPERATING):
    # the vendor's normalised operating income leaves out charges the company
    # itself books above that line, and "to common" net income is not the
    # figure a bank announces.
    oi_t = ttm(_row(qi, "Total Operating Income As Reported", "Operating Income"))
    ni_t = ttm(_row(qi, "Net Income", "Net Income Common Stockholders"))
    m["revenue_ttm"] = rev_t

    def pct(a: float | None, b: float | None) -> float | None:
        return None if (a is None or not b) else round(a / b * 100, 1)

    # Listed inside the trailing window: part of that year was a smaller,
    # pre-IPO company, which inflates ROE until new capital is in every quarter.
    if m.get("first_trade"):
        from datetime import date as _d, timedelta as _t
        if _d.fromisoformat(m["first_trade"]) > cols[0].date() - _t(days=365):
            m["flags"].append("pre_ipo")
            m["pre_ipo"] = {"listed": m["first_trade"], "affects": ["roe"]}
    m["gross_margin"] = pct(gp_t, rev_t)            # None for banks/insurers: no COGS line
    m["op_margin"] = pct(oi_t, rev_t)
    m["net_margin"] = pct(ni_t, rev_t)
    if (m["op_margin"] is not None and m["gross_margin"] is not None
            and m["op_margin"] > m["gross_margin"] + 0.5):
        m["flags"].append("op_above_gross")         # impossible on one period: data error
    if (m["net_margin"] is not None and m["op_margin"] is not None
            and m["net_margin"] > m["op_margin"] + 2):
        m["flags"].append("non_operating_gain")     # net > operating: large one-offs / equity income

    # ROE on AVERAGE equity (start and end of the TTM window) where the balance
    # sheet has it; latest equity otherwise.
    eq = _row(qb, "Stockholders Equity", "Common Stock Equity")
    if eq is not None and ni_t is not None:
        ev = [_num(v) for v in eq.values]
        e_now = ev[0] if ev else None
        e_then = ev[4] if len(ev) > 4 else None
        base = (e_now + e_then) / 2 if (e_now is not None and e_then is not None) else e_now
        m["roe_basis"] = "average equity" if (e_now is not None and e_then is not None) else "latest equity"
        if base is None:
            m["roe"] = None
        elif base <= 0 or (e_now is not None and e_now <= 0):
            m["roe"] = None
            m["roe_reason"] = "negative_equity"     # buybacks can push equity below zero
        else:
            m["roe"] = round(ni_t / base * 100, 1)

    # Latest quarter vs the same quarter a year earlier.
    if len(cols) >= 5 and 330 <= (cols[0] - cols[4]).days <= 400:
        rv = [_num(v) for v in rev.values]
        m["rev_growth_q"], _ = _growth(rv[0], rv[4])
        eps = _row(qi, "Diluted EPS", "Basic EPS")
        if eps is not None:
            e = [_num(v) for v in eps.values]
            m["eps_growth_q"], m["eps_growth_q_reason"] = _growth(e[0], e[4])

    # Last fiscal year vs the one before.
    aeps = _row(ai, "Diluted EPS", "Basic EPS")
    if aeps is not None:
        s = aeps.dropna()
        if len(s) >= 2:
            m["eps_growth_fy"], m["eps_growth_fy_reason"] = _growth(_num(s.iloc[0]), _num(s.iloc[1]))
            m["fy_end"] = s.index[0].date().isoformat()

    _cache_put(key, m)
    return m


def _scale_note(subject: str, subject_cap: float, peer_caps: list[float]) -> str | None:
    """Warn when no peer is remotely the subject's size.

    A correct classification can still leave a useless cohort: Yahoo's
    "Consumer Electronics" holds Apple and six microcaps, the largest of which
    (Sonos) is 0.04% of Apple. The medians are then not a benchmark of
    anything, and the green/red colouring would imply that they are.
    """
    caps = [c for c in peer_caps if c and c > 0]
    if subject_cap <= 0 or not caps or max(caps) >= _SCALE_GAP * subject_cap:
        return None
    pct = max(caps) / subject_cap * 100
    size = "under 0.1%" if pct < 0.1 else f"{pct:.1f}%"
    return (f"No company in this peer set is within {int(_SCALE_GAP * 100)}% of "
            f"{subject}'s size — the largest is {size} of it. Treat the peer "
            f"median as context, not a benchmark.")


def _peers_us(ticker: str, add: list[str], drop: list[str]) -> dict[str, Any]:
    sym = ticker.strip().upper()
    cand = _us_candidates(sym)
    picked, rule = _select(sym, cand.get("ranked") or [])

    def fetch(picks: list[str]) -> list[dict[str, Any]]:
        picks = [p for p in picks if p not in drop]
        for a in add:
            if a not in picks and a != sym:
                picks.append(a)
        with ThreadPoolExecutor(8) as ex:
            return list(ex.map(_us_metrics, [sym] + picks))

    rows = fetch(picked)

    # Yahoo files bitcoin miners turned AI data-centre operators (IREN, MARA,
    # WULF) under "Capital Markets", next to Goldman Sachs. Check the cohort
    # against the company's own business description before trusting it. The
    # descriptions of the rows just fetched are handed over, so the check only
    # pays for the co-viewed names it has not seen.
    classification: dict[str, Any] = {"ok": True, "source": "Yahoo industry classification",
                                      "industry": cand.get("industry")}
    known = {r["symbol"]: r.get("summary") or "" for r in rows if r.get("symbol")}
    caps = {r["symbol"]: float(r.get("market_cap") or 0.0) for r in rows if r.get("symbol")}
    try:
        fix = peer_fit.repair(sym, picked, _N_PEERS, known, caps)
    except Exception as exc:                       # noqa: BLE001 — never fail the card
        fix = {"reject": False, "verdict": {"reason": f"{type(exc).__name__}: {exc}"}}
    if fix.get("reject"):
        picked, rule = _select(sym, fix.get("ranked") or [])
        rule = rule.replace("in industry", "among the closest business matches")
        classification = {
            "ok": False,
            "source": "closest business match",
            "industry": cand.get("industry"),
            "note": (f'Yahoo classifies {sym} under "{cand.get("industry")}", but those '
                     f"companies are in a different business. These peers are the ones "
                     f"whose own business descriptions match {sym}'s most closely."),
            "industry_fit": (fix.get("verdict") or {}).get("industry_fit"),
            "match_fit": (fix.get("verdict") or {}).get("related_fit"),
            "pool_size": fix.get("pool_size"),
        }
        rows = fetch(picked)
    else:
        classification["check"] = (fix.get("verdict") or {}).get("reason") or "cohort matches the business"
        classification["industry_fit"] = (fix.get("verdict") or {}).get("industry_fit")

    # A correct classification can still leave a useless cohort. Yahoo's
    # "Consumer Electronics" holds Apple and six microcaps — Sonos is 0.04% of
    # Apple's market cap — so the peer median below is not a benchmark of
    # anything. Say so rather than letting the colours imply otherwise.
    note = _scale_note(sym, float((rows[0] or {}).get("market_cap") or 0.0),
                       [float(r.get("market_cap") or 0.0) for r in rows[1:]])
    if note:
        classification["scale_warning"] = note

    # Copies: the descriptions stay in the metrics cache but never ship to the
    # browser, where nine of them would be most of the payload.
    rows = [{k: v for k, v in (r or {}).items() if k != "summary"} for r in rows]
    subj_end = (rows[0] or {}).get("period_end") if rows else None
    ttm = f"TTM to {date.fromisoformat(subj_end).strftime('%b %Y')}" if subj_end else "TTM"
    return {"industry": cand.get("industry"), "sector": cand.get("sector"),
            "classification": classification,
            "period_labels": {"ttm": ttm, "growth": "latest quarter vs same quarter last year",
                              "fy": "each company's last fiscal year"},
            "selection_rule": rule, "rows": rows, "currency": "$",
            "industry_size": len(cand.get("ranked") or []),
            "basis": {
                "margins": "trailing twelve months — sum of the last four reported quarters",
                "roe": "TTM net income ÷ average shareholders' equity over the same window",
                "growth_q": "latest reported quarter vs the same quarter a year earlier",
                "growth_fy": "last fiscal year vs the prior fiscal year, diluted EPS",
                "pe": "vendor trailing and forward P/E (GAAP trailing EPS; consensus forward)",
                "classification": (classification.get("source") or "Yahoo industry classification"),
            },
            "columns": ["market_cap", "rev_growth_q", "eps_growth_q", "eps_growth_fy",
                        "gross_margin", "op_margin", "net_margin", "roe", "pe_ttm", "pe_fwd"]}


# ════════════════════════════════════════════════════════════════════════════
# A-shares
# ════════════════════════════════════════════════════════════════════════════
def _cn_latest_basic() -> tuple[str | None, dict[str, dict]]:
    hit = _cache_get("cn_daily_basic", 6 * 3600)
    if hit:
        return hit["date"], hit["rows"]
    from . import cn_data
    d = date.today()
    for _ in range(12):
        ds = d.strftime("%Y%m%d")
        try:
            rows = cn_data.call("daily_basic", {"trade_date": ds}, "ts_code,total_mv,pe_ttm")
        except Exception:                          # noqa: BLE001
            rows = []
        if rows:
            m = {r["ts_code"]: r for r in rows}
            _cache_put("cn_daily_basic", {"date": ds, "rows": m})
            return ds, m
        d -= timedelta(days=1)
    return None, {}


_CN_FIELDS = ("ts_code,end_date,ann_date,roe_yearly,roe_waa,grossprofit_margin,netprofit_margin,"
              "op_of_gr,dt_eps,eps,dt_eps_yoy,basic_eps_yoy,or_yoy")


_CN_YEAR_FRACTION = {"0331": 4.0, "0630": 2.0, "0930": 4.0 / 3.0, "1231": 1.0}


def _cn_roe(row: dict | None, annualise: bool) -> float | None:
    """加权平均 ROE — the figure in the company's report and on every data site.

    Tushare's `roe` and `roe_yearly` divide by PERIOD-END equity. That is a
    different number: Moutai's H1 2026 is 17.95 on that basis and 16.75 as
    reported; China Merchants Bank's FY2025 is 12.02 against a reported 13.44
    (a bank reports on ordinary shareholders' equity). Checked against
    Eastmoney on 2026-10-12, `roe_waa` matches to the second decimal. The
    period-end figure is only a fallback for filers where roe_waa is empty.
    """
    if not row:
        return None
    waa = _num(row.get("roe_waa"))
    if waa is None:
        return _num(row.get("roe_yearly"))
    k = _CN_YEAR_FRACTION.get((row.get("end_date") or "")[4:], 1.0) if annualise else 1.0
    return round(waa * k, 2)


def _cn_fina(ts_code: str) -> list[dict]:
    key = f"cn_fina_v5_{ts_code}"   # v5: weighted-average ROE; same-filing duplicates merged
    hit = _cache_get(key, _TTL_METRICS)
    if hit is not None:
        return hit
    from . import cn_data
    start = (date.today() - timedelta(days=800)).strftime("%Y%m%d")
    try:
        rows = cn_data.call("fina_indicator", {"ts_code": ts_code, "start_date": start,
                                               "end_date": date.today().strftime("%Y%m%d")},
                            _CN_FIELDS) or []
    except Exception:                              # noqa: BLE001
        rows = []
    # Restatements repeat an end_date — keep the most recently announced version,
    # but RECORD that it happened: Wuliangye's H1 2025 EPS is 5.02 as filed and
    # 1.19 as restated, and a growth rate against either base means something
    # different. The newest vintage's announcement date is the restatement event.
    by_end: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("end_date"):
            by_end.setdefault(r["end_date"], []).append(r)
    best: dict[str, dict] = {}
    for e, vs in by_end.items():
        vs = sorted(vs, key=lambda x: x.get("ann_date") or "")
        best[e] = cn_data.newest_filing(vs)
        eps_vals = {round(v, 3) for v in (_num(x.get("dt_eps") if x.get("dt_eps") is not None else x.get("eps")) for x in vs) if v is not None}
        if len(eps_vals) > 1 and max(abs(v) for v in eps_vals) > 0:
            spread = (max(eps_vals) - min(eps_vals)) / max(abs(v) for v in eps_vals)
            if spread > 0.05:
                best[e]["_restated_on"] = vs[-1].get("ann_date")
    out = sorted(best.values(), key=lambda r: r["end_date"], reverse=True)
    _cache_put(key, out)
    return out


def _peers_cn(ticker: str, add: list[str], drop: list[str]) -> dict[str, Any]:
    from . import cn_data
    basic = cn_data._stock_basic_list()
    by_ts = {r["ts_code"]: r for r in basic}
    tq = ticker.strip().upper()
    subj = by_ts.get(tq) or next((r for r in basic if r["symbol"] == tq.split(".")[0]), None)
    if not subj:
        return {"status": "error", "error": f"{ticker} not found in the A-share list"}
    industry = subj.get("industry")
    trade_date, mv = _cn_latest_basic()
    same = [r["ts_code"] for r in basic if r.get("industry") == industry]
    ranked = sorted(((ts, _num((mv.get(ts) or {}).get("total_mv")) or 0.0) for ts in same),
                    key=lambda x: -x[1])
    picked, rule = _select(subj["ts_code"], ranked)
    picked = [p for p in picked if p not in drop]
    for a in add:
        a = a.upper()
        full = a if a in by_ts else next((r["ts_code"] for r in basic if r["symbol"] == a.split(".")[0]), None)
        if full and full not in picked and full != subj["ts_code"]:
            picked.append(full)
    codes = [subj["ts_code"]] + picked

    # Tushare is rate-limited: keep concurrency low.
    with ThreadPoolExecutor(3) as ex:
        fina = dict(zip(codes, ex.map(_cn_fina, codes)))

    # Pin everyone to the most common latest reporting period. Tushare ratios are
    # year-to-date, so an H1 row next to a Q1 row is not a comparison.
    latest = [f[0]["end_date"] for f in fina.values() if f]
    common = max(set(latest), key=latest.count) if latest else None
    fys = [next((x["end_date"] for x in f if x["end_date"].endswith("1231")), None) for f in fina.values() if f]
    fys = [x for x in fys if x]
    common_fy = max(set(fys), key=fys.count) if fys else None

    rows = []
    for ts in codes:
        b = by_ts.get(ts, {})
        r = {"symbol": ts, "name": b.get("name") or ts, "state": "ok", "flags": [],
             "market_cap": (_num((mv.get(ts) or {}).get("total_mv")) or 0) * 1e4 or None,  # 万元 -> ¥
             "pe_ttm": _num((mv.get(ts) or {}).get("pe_ttm"))}
        f = fina.get(ts) or []
        if not f:
            r.update({"state": "unavailable", "note": "no fina_indicator rows"})
            rows.append(r)
            continue
        row = next((x for x in f if x["end_date"] == common), None)
        if row is None:
            row = f[0]
            r["flags"].append("period_mismatch")
        r["period_end"] = f"{row['end_date'][:4]}-{row['end_date'][4:6]}-{row['end_date'][6:]}"
        r["gross_margin"] = _num(row.get("grossprofit_margin"))   # banks: none
        r["op_margin"] = _num(row.get("op_of_gr"))
        r["net_margin"] = _num(row.get("netprofit_margin"))
        r["roe"] = _cn_roe(row, annualise=True)
        # Last full fiscal year, the same figure Gate 1 shows.
        fy_row = next((x for x in f if x["end_date"] == common_fy), None) if common_fy else None
        r["roe_fy"] = _cn_roe(fy_row, annualise=False) if fy_row else None
        lst = b.get("list_date")
        if lst and len(lst) == 8:
            affects = [k for k, end in (("roe", row["end_date"]), ("roe_fy", common_fy)) if end and lst > end]
            if affects:
                r["flags"].append("pre_ipo")
                r["pre_ipo"] = {"listed": f"{lst[:4]}-{lst[4:6]}-{lst[6:]}", "affects": affects}
        r["rev_growth_q"] = _num(row.get("or_yoy"))
        # EPS growth computed from the raw diluted EPS of the same period a year
        # earlier, through the same `_growth` rule the US side uses. Tushare's
        # precomputed dt_eps_yoy happily prints a percentage off a loss, which
        # would make an A-share turnaround look like "+300%" while the identical
        # US case reads "n/m".
        ly = f"{int(row['end_date'][:4]) - 1}{row['end_date'][4:]}"
        prior = next((x for x in f if x["end_date"] == ly), None)
        cur_eps = _num(row.get("dt_eps")) if row.get("dt_eps") is not None else _num(row.get("eps"))
        prv_eps = None
        if prior is not None:
            prv_eps = _num(prior.get("dt_eps")) if prior.get("dt_eps") is not None else _num(prior.get("eps"))
        # Flag a comparison that straddles a restatement: the year-ago period was
        # restated, or a restatement was filed between the two announcements.
        events = [x["_restated_on"] for x in f if x.get("_restated_on")]
        if prior is not None and (prior.get("_restated_on") or any(
                (prior.get("ann_date") or "") < e <= (row.get("ann_date") or "") for e in events)):
            r["flags"].append("restated")
        if cur_eps is not None and prior is not None:
            r["eps_growth_q"], r["eps_growth_q_reason"] = _growth(cur_eps, prv_eps)
        else:
            eg = _num(row.get("dt_eps_yoy"))              # fallback when the prior row is absent
            r["eps_growth_q"] = eg if eg is not None else _num(row.get("basic_eps_yoy"))
        if "restated" in r["flags"]:
            # Withhold growth across a restatement. fina_indicator stamps the
            # original and restated rows with the SAME ann_date, so which base
            # gets picked is arbitrary row order — Wuliangye flipped between
            # +89.3% and -55.1% on identical data. Margins and ROE use only the
            # current period and stay.
            for k in ("rev_growth_q", "eps_growth_q"):
                r[k] = None
                r[k + "_reason"] = "restated"
        for k in ("gross_margin", "op_margin", "net_margin", "roe", "roe_fy", "rev_growth_q", "eps_growth_q"):
            if r.get(k) is not None:
                r[k] = round(r[k], 1)
        rows.append(r)

    def _plabel(e: str | None) -> str | None:
        if not e or len(e) != 8:
            return None
        return {"0331": f"Q1 {e[:4]}", "0630": f"H1 {e[:4]}", "0930": f"9M {e[:4]}",
                "1231": f"FY{e[:4]}"}.get(e[4:])

    return {"industry": industry, "selection_rule": rule, "rows": rows, "currency": "¥",
            "period_labels": {"latest": _plabel(common), "fy": _plabel(common_fy),
                              "growth": f"{_plabel(common)} vs same period last year" if common else None},
            "common_fy": common_fy,
            "industry_size": len(same), "common_period": common, "trade_date": trade_date,
            "basis": {
                "margins": "year-to-date for the common reporting period (Tushare fina_indicator)",
                "roe": "weighted-average ROE (加权) for the latest period, year-to-date, annualised — naive: an H1 figure is simply doubled, so seasonal or fast-ramping businesses are distorted",
                "roe_fy": "weighted-average ROE (加权) for the last full fiscal year, as the company reports it — the same figure Gate 1 shows; audited but older",
                "growth_q": "year-to-date vs the same period last year (or_yoy, dt_eps_yoy)",
                "pe": "P/E TTM from daily_basic",
                "classification": "Tushare industry classification",
            },
            "columns": ["market_cap", "rev_growth_q", "eps_growth_q",
                        "gross_margin", "op_margin", "net_margin", "roe", "roe_fy", "pe_ttm"]}


# ════════════════════════════════════════════════════════════════════════════
def _median(vals: list[float]) -> float | None:
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    k = len(v) // 2
    return round(v[k] if len(v) % 2 else (v[k - 1] + v[k]) / 2, 1)


def peer_comparison(market: str, ticker: str, add: str = "", drop: str = "") -> dict[str, Any]:
    adds = [x.strip().upper() for x in add.split(",") if x.strip()][:10]
    drops = [x.strip().upper() for x in drop.split(",") if x.strip()]
    if not ticker.strip():
        return {"status": "error", "error": "no ticker"}
    try:
        out = _peers_cn(ticker, adds, drops) if market == "cn" else _peers_us(ticker, adds, drops)
    except Exception as exc:                       # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if out.get("status") == "error":
        return out
    peers = [r for r in out["rows"][1:] if r.get("state") == "ok"]
    out["median"] = {c: _median([r.get(c) for r in peers])
                     for c in out["columns"] if c != "market_cap"}
    out["status"] = "ready"
    out["market"] = market
    out["subject"] = out["rows"][0]["symbol"] if out["rows"] else ticker
    out["built_at"] = time.time()
    return out

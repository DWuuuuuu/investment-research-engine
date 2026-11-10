"""A-Share (mainland China) screener — mirrors screen_ticker()'s output shape
so the existing Stock Screen frontend (renderScreenResult) can render it
without modification. Data comes from Tushare Pro instead of yfinance.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import cn_data
from .screener import _check, _cagr, _gate_growth_check, _price_on_or_before

CNY = "¥"

# Long-run "typical" trailing P/E anchors for A-share industries (Chinese
# keyword → benchmark). A-share multiples run structurally different from the
# US — banks/coal trade single-digit, liquor & chips command large premiums.
_CN_INDUSTRY_PE: dict[str, float] = {
    "银行":   6.5,
    "保险":   11.0,
    "证券":   18.0,
    "白酒":   28.0,
    "食品":   25.0,
    "饮料":   26.0,
    "医药":   28.0,
    "生物":   32.0,
    "软件":   45.0,
    "互联网": 35.0,
    "半导体": 48.0,
    "电子":   35.0,
    "计算机": 42.0,
    "房地产": 8.0,
    "地产":   8.0,
    "建筑":   9.0,
    "钢铁":   9.0,
    "煤炭":   8.0,
    "有色":   15.0,
    "化工":   16.0,
    "电力":   15.0,
    "公用":   16.0,
    "汽车":   18.0,
    "家电":   15.0,
    "机械":   22.0,
    "军工":   40.0,
    "国防":   40.0,
    "农业":   20.0,
    "传媒":   28.0,
    "通信":   25.0,
}


def _cn_typical_pe(industry: str | None) -> tuple[float | None, str]:
    ind = industry or ""
    for key, val in _CN_INDUSTRY_PE.items():
        if key in ind:
            return val, industry or key
    return None, industry or "—"


# Whole-market daily_basic snapshot cache (keyed by trade_date) — one Tushare
# call returns every stock's pe_ttm, shared across all A-share screens that day.
_CN_MARKET_PE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CN_MARKET_PE_TTL = 3600   # 1 hour (EOD data doesn't change intraday)


def _cn_market_pe_rows(trade_date: str) -> list[dict]:
    import time as _t
    hit = _CN_MARKET_PE_CACHE.get(trade_date)
    if hit and _t.time() - hit[0] < _CN_MARKET_PE_TTL:
        return hit[1]
    try:
        rows = cn_data.call("daily_basic", {"trade_date": trade_date}, "ts_code,pe_ttm")
    except Exception:
        return []
    _CN_MARKET_PE_CACHE[trade_date] = (_t.time(), rows)
    return rows


def _cn_peer_median_pe(industry: str | None, trade_date: str | None,
                       self_ts_code: str) -> dict | None:
    """TRUE full-industry median P/E: every listed A-share in the same Tushare
    industry on `trade_date`. Returns None if unavailable."""
    import statistics
    if not industry or not trade_date:
        return None
    rows = _cn_market_pe_rows(trade_date)
    if not rows:
        return None
    ind_map = {r["ts_code"]: r.get("industry") for r in cn_data._stock_basic_list()}
    pes: list[float] = []
    for r in rows:
        ts = r.get("ts_code")
        if ts == self_ts_code or ind_map.get(ts) != industry:
            continue
        pe = r.get("pe_ttm")
        try:
            pv = float(pe)
        except (TypeError, ValueError):
            continue
        if 0 < pv < 200:
            pes.append(pv)
    if len(pes) < 3:
        return None
    return {
        "median": round(statistics.median(pes), 1),
        "n":      len(pes),
        "basis":  industry,
        "peers":  None,   # industry can hold dozens — surface count, not a list
    }


def _debt_structure_ashare(bs_rows: list[dict], industry: str | None,
                           latest_fina: dict | None = None) -> dict | None:
    """Debt structure from Tushare balancesheet. Non-bank A-shares expose
    st_borr/lt_borr/bond_payable; banks mostly report only bond_payable +
    total_liab (deposits sit outside 'debt'), so the view degrades gracefully."""
    # one row per fiscal year-end (drop restatement duplicates, keep first seen)
    by_end: dict[str, dict] = {}
    for r in bs_rows:
        ed = r.get("end_date") or ""
        if ed.endswith("1231") and ed not in by_end:
            by_end[ed] = r
    annual = [by_end[k] for k in sorted(by_end)]
    if not annual:
        return None

    def n(r: dict, k: str) -> float | None:
        v = r.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    latest = annual[-1]
    st_borr = n(latest, "st_borr")
    lt_borr = n(latest, "lt_borr")
    bond    = n(latest, "bond_payable")
    due1y   = n(latest, "non_cur_liab_due_1y")
    lease   = n(latest, "lease_liab")
    total_assets = n(latest, "total_assets")
    total_liab   = n(latest, "total_liab")
    equity  = n(latest, "total_hldr_eqy_exc_min_int")
    cash    = n(latest, "money_cap")

    def _sum(vals):
        present = [x for x in vals if x is not None]
        return sum(present) if present else None

    interest_debt = _sum([st_borr, lt_borr, bond, due1y, lease])
    st = _sum([st_borr, due1y])
    lt = _sum([lt_borr, bond, lease])

    if interest_debt is None and total_liab is None:
        return None

    total_debt = interest_debt
    st_pct = lt_pct = None
    if total_debt and total_debt > 0:
        if st is not None: st_pct = round(st / total_debt * 100, 1)
        if lt is not None: lt_pct = round(lt / total_debt * 100, 1)
        if st_pct is None and lt_pct is not None: st_pct = round(max(0.0, 100 - lt_pct), 1)
        if lt_pct is None and st_pct is not None: lt_pct = round(max(0.0, 100 - st_pct), 1)

    net_debt = (total_debt - cash) if (total_debt is not None and cash is not None) else None
    d2e  = round(total_debt / equity, 2) if (total_debt and equity) else None
    d2a  = round(total_debt / total_assets * 100, 1) if (total_debt and total_assets) else None
    liab2a = round(total_liab / total_assets * 100, 1) if (total_liab and total_assets) else None

    series = []
    for r in annual:
        s = _sum([n(r, k) for k in ("st_borr", "lt_borr", "bond_payable",
                                    "non_cur_liab_due_1y", "lease_liab")])
        if s is not None:
            series.append({"year": int(r["end_date"][:4]), "debt": round(s)})

    is_fin = any(k in (industry or "") for k in ("银行", "保险", "证券"))

    def _fin(k):
        if not latest_fina:
            return None
        v = latest_fina.get(k)
        try:
            return round(float(v), 2) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    rnd = lambda v: round(v) if v is not None else None
    return {
        "total_debt": rnd(total_debt), "net_debt": rnd(net_debt), "cash": rnd(cash),
        "current_debt": rnd(st), "long_term_debt": rnd(lt), "capital_leases": rnd(lease),
        "st_pct": st_pct, "lt_pct": lt_pct,
        "debt_to_equity": d2e, "debt_to_assets": d2a, "liab_to_assets": liab2a,
        "debt_ebitda": None, "interest_coverage": None, "cost_of_debt": None,
        "current_ratio": _fin("current_ratio"), "quick_ratio": _fin("quick_ratio"),
        "debt_series": series, "is_financial": is_fin,
    }


def _hidden_assets_ashare(bs_rows: list[dict], market_cap: float | None,
                          pb: float | None) -> dict | None:
    """Hidden-asset signals from Tushare balancesheet. Tushare exposes an
    explicit `invest_real_estate` (投资性房地产) line — a direct real-estate read."""
    annual = {}
    for r in bs_rows:
        ed = r.get("end_date") or ""
        if ed.endswith("1231") and ed not in annual:
            annual[ed] = r
    if not annual:
        return None
    latest = annual[max(annual)]

    def n(k: str) -> float | None:
        v = latest.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    fix_assets = n("fix_assets")
    cip        = n("cip")
    inv_re     = n("invest_real_estate")
    lt_inv     = n("lt_eqt_invest")
    intang     = n("intan_assets")
    goodwill   = n("goodwill")
    cash       = n("money_cap")
    st_inv     = n("trad_asset") or n("fa_avail_for_sale")
    equity     = n("total_hldr_eqy_exc_min_int")
    total_debt = _sum_borrowings(latest)

    if fix_assets is None and inv_re is None and cash is None:
        return None

    cash_st = (cash or 0) + (st_inv or 0) if (cash is not None or st_inv is not None) else None
    net_cash = (cash_st - (total_debt or 0)) if cash_st is not None else None
    net_cash_pct = round(net_cash / market_cap * 100, 1) if (net_cash is not None and market_cap) else None
    p_tang = None
    tang_book = None
    if equity is not None:
        tang_book = equity - (intang or 0) - (goodwill or 0)
        if market_cap and tang_book > 0:
            p_tang = round(market_cap / tang_book, 2)
    goodwill_intang = None
    if goodwill is not None or intang is not None:
        goodwill_intang = (goodwill or 0) + (intang or 0)
    re_total = None
    if inv_re is not None or fix_assets is not None:
        re_total = (inv_re or 0) + (fix_assets or 0)
    re_pct = round(re_total / market_cap * 100, 1) if (re_total and market_cap) else None

    signals: list[dict] = []
    if net_cash_pct is not None and net_cash_pct >= 25:
        signals.append({"kind": "cash",
                        "text": f"Net cash equals {net_cash_pct}% of market cap — a Lynch-style cash cushion the market may under-credit."})
    if inv_re and market_cap and inv_re / market_cap * 100 >= 5:
        signals.append({"kind": "re",
                        "text": f"Investment property (投资性房地产) of {_money_cny(inv_re)} on the books at cost — {round(inv_re/market_cap*100,1)}% of market cap, likely below appraised value."})
    if pb is not None and 0 < pb < 1:
        signals.append({"kind": "book",
                        "text": f"Trades below book value (P/B {round(pb,2)}×) — market prices equity under its accounting net worth."})
    if re_pct is not None and re_pct >= 30:
        signals.append({"kind": "dep",
                        "text": f"Property, plant & equipment plus investment real estate equal {re_pct}% of market cap — an asset-heavy balance sheet."})

    rnd = lambda v: round(v) if v is not None else None
    return {
        "land": None, "buildings": None, "machinery": None,
        "investment_real_estate": rnd(inv_re) if inv_re else None,
        "cip": rnd(cip) if cip else None,
        "net_ppe": rnd(fix_assets), "gross_ppe": None, "accumulated_dep": None, "dep_pct": None,
        "lt_investments": rnd(lt_inv),
        "cash_st_inv": rnd(cash_st), "total_debt": rnd(total_debt),
        "net_cash": rnd(net_cash), "net_cash_pct_mcap": net_cash_pct,
        "book_value": rnd(equity), "tangible_book": rnd(tang_book), "market_cap": rnd(market_cap),
        "pb": round(pb, 2) if pb is not None else None, "p_tangible_book": p_tang,
        "goodwill_intangibles": rnd(goodwill_intang),
        "land_buildings_pct_mcap": None,
        "signals": signals,
    }


def _cashflow_margins_ashare(income_rows: list[dict], cf_rows: list[dict]) -> dict | None:
    """A-share cash flow + margins with trends, from Tushare income + cashflow.
    Pre-tax margin = total_profit / total_revenue (利润总额 = pre-tax profit)."""
    def annualize(rows):
        by_end = {}
        for r in rows:
            ed = r.get("end_date") or ""
            if ed.endswith("1231") and ed not in by_end:
                by_end[ed] = r
        return [by_end[k] for k in sorted(by_end)]

    def num(r, k):
        v = r.get(k)
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    inc = annualize(income_rows)
    cf  = annualize(cf_rows)
    if not inc:
        return None

    rev = {int(r["end_date"][:4]): num(r, "total_revenue") for r in inc if num(r, "total_revenue")}
    if not rev:
        return None

    def margin_from(numer_key, cost_mode=False):
        series = []
        for r in inc:
            yr = int(r["end_date"][:4])
            rv = rev.get(yr)
            if not rv:
                continue
            if cost_mode:
                cost = num(r, "oper_cost"); base = num(r, "revenue") or rv
                if cost is None or not base:
                    continue
                val = (base - cost) / base * 100
            else:
                p = num(r, numer_key)
                if p is None:
                    continue
                val = p / rv * 100
            series.append({"year": yr, "margin": round(val, 1)})
        if not series:
            return {"latest": None, "delta_yoy": None, "delta_period": None, "series": []}
        latest = series[-1]["margin"]
        return {
            "latest": latest,
            "delta_yoy": round(latest - series[-2]["margin"], 1) if len(series) >= 2 else None,
            "delta_period": round(latest - series[0]["margin"], 1) if len(series) >= 2 else None,
            "series": series,
        }

    gross     = margin_from(None, cost_mode=True)
    operating = margin_from("operate_profit")
    pretax    = margin_from("total_profit")
    net       = margin_from("n_income")

    ocf_map   = {int(r["end_date"][:4]): num(r, "n_cashflow_act") for r in cf if num(r, "n_cashflow_act") is not None}
    fcf_map   = {int(r["end_date"][:4]): num(r, "free_cashflow") for r in cf if num(r, "free_cashflow") is not None}
    capex_map = {int(r["end_date"][:4]): abs(num(r, "c_pay_acq_const_fiolta")) for r in cf if num(r, "c_pay_acq_const_fiolta") is not None}

    ocf_series = [{"year": y, "val": round(ocf_map[y])} for y in sorted(ocf_map)]
    fcf_series = [{"year": y, "val": round(fcf_map[y])} for y in sorted(fcf_map)]
    capex_series = [{"year": y, "val": round(capex_map[y])} for y in sorted(capex_map)]

    fcf_margin_series = [{"year": y, "margin": round(fcf_map[y] / rev[y] * 100, 1)}
                         for y in sorted(fcf_map) if y in rev and rev[y]]
    fcf_margin = {
        "latest": fcf_margin_series[-1]["margin"] if fcf_margin_series else None,
        "delta_yoy": (round(fcf_margin_series[-1]["margin"] - fcf_margin_series[-2]["margin"], 1)
                      if len(fcf_margin_series) >= 2 else None),
        "delta_period": (round(fcf_margin_series[-1]["margin"] - fcf_margin_series[0]["margin"], 1)
                         if len(fcf_margin_series) >= 2 else None),
        "series": fcf_margin_series,
    }

    def growth(series):
        if len(series) >= 2 and series[0]["val"] > 0 and series[-1]["val"] > 0:
            n = series[-1]["year"] - series[0]["year"]
            if n > 0:
                return round(((series[-1]["val"] / series[0]["val"]) ** (1 / n) - 1) * 100, 1)
        return None

    if all(m["latest"] is None for m in (gross, operating, pretax, net)) and not ocf_series:
        return None

    return {
        "margins": {"gross": gross, "operating": operating, "pretax": pretax, "net": net, "fcf": fcf_margin},
        "cashflow": {
            "ocf_latest":   ocf_series[-1]["val"] if ocf_series else None,
            "fcf_latest":   fcf_series[-1]["val"] if fcf_series else None,
            "capex_latest": capex_series[-1]["val"] if capex_series else None,
            "ocf_cagr":     growth(ocf_series),
            "fcf_cagr":     growth(fcf_series),
            "ocf_series":   ocf_series,
            "fcf_series":   fcf_series,
            "capex_series": capex_series,
        },
    }


def _fundamental_scores_ashare(bs_rows, income_rows, cf_rows, fina_rows,
                               market_cap, price, is_financial):
    """A-share Tier-1 scores from Tushare — same shape as screener._fundamental_scores."""
    def annual(rows):
        by = {}
        for r in rows or []:
            ed = r.get("end_date") or ""
            if ed.endswith("1231") and ed not in by:
                by[ed] = r
        return [by[k] for k in sorted(by)]        # ascending, latest last

    def num(r, k):
        try:
            v = r.get(k)
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    bs, inc, cf, fi = annual(bs_rows), annual(income_rows), annual(cf_rows), annual(fina_rows)
    if not bs or not inc:
        return None

    def at(lst, idx, k):
        return num(lst[-1 - idx], k) if len(lst) > idx else None

    # ── Piotroski F-Score ─────────────────────────────────────────────────────
    piotroski = None
    try:
        ta0, ta1 = at(bs, 0, "total_assets"), at(bs, 1, "total_assets")
        ni0, ni1 = at(inc, 0, "n_income"), at(inc, 1, "n_income")
        ocf0 = at(cf, 0, "n_cashflow_act")
        roa0 = ni0 / ta0 if (ni0 is not None and ta0) else None
        roa1 = ni1 / ta1 if (ni1 is not None and ta1) else None
        rev0, rev1 = at(inc, 0, "total_revenue"), at(inc, 1, "total_revenue")
        tl0, tl1 = at(bs, 0, "total_liab"), at(bs, 1, "total_liab")
        lev0 = tl0 / ta0 if (tl0 is not None and ta0) else None
        lev1 = tl1 / ta1 if (tl1 is not None and ta1) else None
        cr0, cr1 = at(fi, 0, "current_ratio"), at(fi, 1, "current_ratio")
        gm0, gm1 = at(fi, 0, "grossprofit_margin"), at(fi, 1, "grossprofit_margin")
        eps0, eps1 = at(fi, 0, "eps"), at(fi, 1, "eps")
        sh0 = ni0 / eps0 if (ni0 and eps0) else None      # share-count proxy for dilution
        sh1 = ni1 / eps1 if (ni1 and eps1) else None
        at0 = rev0 / ta0 if (rev0 and ta0) else None
        at1 = rev1 / ta1 if (rev1 and ta1) else None
        tests = [
            ("Positive return on assets", roa0 is not None and roa0 > 0,
             "Company is profitable on its asset base (ROA > 0)."),
            ("Positive operating cash flow", ocf0 is not None and ocf0 > 0,
             "Core operations generate real cash."),
            ("Rising ROA", roa0 is not None and roa1 is not None and roa0 > roa1,
             "Profitability improved vs. last year."),
            ("Earnings backed by cash", ocf0 is not None and ni0 is not None and ocf0 > ni0,
             "Operating cash flow exceeds net income — low accruals, high-quality earnings."),
            ("Lower leverage", lev0 is not None and lev1 is not None and lev0 <= lev1,
             "Total-liabilities-to-assets fell or held."),
            ("Higher current ratio", cr0 is not None and cr1 is not None and cr0 > cr1,
             "Short-term liquidity improved."),
            ("No share dilution", sh0 is not None and sh1 is not None and sh0 <= sh1 * 1.01,
             "Share count didn't meaningfully rise — no dilution."),
            ("Higher gross margin", gm0 is not None and gm1 is not None and gm0 > gm1,
             "Pricing power / cost efficiency improved."),
            ("Higher asset turnover", at0 is not None and at1 is not None and at0 > at1,
             "Generating more revenue per dollar of assets."),
        ]
        score = sum(1 for _, p, _ in tests if p)
        piotroski = {"score": score, "max": 9,
                     "verdict": "strong" if score >= 8 else "moderate" if score >= 5 else "weak",
                     "components": [{"name": n, "pass": bool(p), "detail": d} for n, p, d in tests]}
    except Exception:
        piotroski = None

    # ── Altman Z (non-financial) ──────────────────────────────────────────────
    altman = None
    try:
        if is_financial:
            altman = {"applicable": False,
                      "note": "Altman Z is calibrated for non-financial firms; a bank's balance sheet makes it meaningless."}
        else:
            ta = at(bs, 0, "total_assets")
            tca, tcl = at(bs, 0, "total_cur_assets"), at(bs, 0, "total_cur_liab")
            wc = (tca - tcl) if (tca is not None and tcl is not None) else None
            re = at(bs, 0, "undistr_porfit")
            ebit = at(inc, 0, "operate_profit")
            tl = at(bs, 0, "total_liab")
            sales = at(inc, 0, "total_revenue")
            if all(v is not None for v in (ta, wc, re, ebit, tl, sales, market_cap)) and ta and tl:
                x1, x2, x3, x4, x5 = wc/ta, re/ta, ebit/ta, market_cap/tl, sales/ta
                z = 1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5
                altman = {"applicable": True, "z": round(z, 2),
                          "zone": "safe" if z > 2.99 else "grey" if z >= 1.81 else "distress",
                          "components": {"wc_ta": round(x1,3), "re_ta": round(x2,3),
                                         "ebit_ta": round(x3,3), "mve_tl": round(x4,3), "sales_ta": round(x5,3)}}
    except Exception:
        altman = None

    # ── Beneish M-Score ───────────────────────────────────────────────────────
    beneish = None
    try:
        rec0, rec1 = at(bs, 0, "accounts_receiv"), at(bs, 1, "accounts_receiv")
        s0, s1 = at(inc, 0, "total_revenue"), at(inc, 1, "total_revenue")
        cogs0, cogs1 = at(inc, 0, "oper_cost"), at(inc, 1, "oper_cost")
        ca0, ca1 = at(bs, 0, "total_cur_assets"), at(bs, 1, "total_cur_assets")
        ppe0, ppe1 = at(bs, 0, "fix_assets"), at(bs, 1, "fix_assets")
        ta0b, ta1b = at(bs, 0, "total_assets"), at(bs, 1, "total_assets")
        dep0, dep1 = at(cf, 0, "depr_fa_coga_dpba"), at(cf, 1, "depr_fa_coga_dpba")
        sga0 = (at(inc, 0, "sell_exp") or 0) + (at(inc, 0, "admin_exp") or 0)
        sga1 = (at(inc, 1, "sell_exp") or 0) + (at(inc, 1, "admin_exp") or 0)
        cl0, cl1 = at(bs, 0, "total_cur_liab"), at(bs, 1, "total_cur_liab")
        ltd0 = (at(bs, 0, "lt_borr") or 0) + (at(bs, 0, "bond_payable") or 0)
        ltd1 = (at(bs, 1, "lt_borr") or 0) + (at(bs, 1, "bond_payable") or 0)
        ni0 = at(inc, 0, "n_income"); ocf0 = at(cf, 0, "n_cashflow_act")
        req = [rec0, rec1, s0, s1, cogs0, cogs1, ca0, ca1, ppe0, ppe1, ta0b, ta1b,
               dep0, dep1, cl0, cl1, ni0, ocf0]
        if all(v is not None for v in req) and s0 and s1 and ta0b and ta1b and sga0 and sga1:
            dsri = (rec0/s0) / (rec1/s1)
            gm0b, gm1b = (s0-cogs0)/s0, (s1-cogs1)/s1
            gmi = gm1b / gm0b if gm0b else 1.0
            aqi = (1 - (ca0+ppe0)/ta0b) / (1 - (ca1+ppe1)/ta1b) if (1-(ca1+ppe1)/ta1b) != 0 else 1.0
            sgi = s0 / s1
            depi = (dep1/(dep1+ppe1)) / (dep0/(dep0+ppe0)) if (dep0+ppe0) else 1.0
            sgai = (sga0/s0) / (sga1/s1)
            lvgi = ((ltd0+cl0)/ta0b) / ((ltd1+cl1)/ta1b)
            tata = (ni0 - ocf0) / ta0b
            m = (-4.84 + 0.92*dsri + 0.528*gmi + 0.404*aqi + 0.892*sgi
                 + 0.115*depi - 0.172*sgai + 4.679*tata - 0.327*lvgi)
            beneish = {"available": True, "m": round(m, 2), "flag": bool(m > -1.78),
                       "verdict": "flag" if m > -1.78 else "clean"}
        else:
            beneish = {"available": False}
    except Exception:
        beneish = {"available": False}

    # ── Graham Number ─────────────────────────────────────────────────────────
    graham = None
    try:
        eps = at(fi, 0, "eps")
        bvps = at(fi, 0, "bps")
        if eps is not None and eps > 0 and bvps and bvps > 0:
            gnum = (22.5 * eps * bvps) ** 0.5
            mos = ((gnum - price) / gnum * 100) if (price and gnum) else None
            graham = {"number": round(gnum, 2), "price": round(price, 2) if price else None,
                      "eps": round(eps, 2), "bvps": round(bvps, 2),
                      "margin_of_safety_pct": round(mos, 1) if mos is not None else None,
                      "verdict": ("undervalued" if mos and mos >= 20 else
                                  "fair" if mos and mos > -10 else "overvalued") if mos is not None else "unknown"}
        else:
            graham = {"number": None, "note": "Graham Number needs positive EPS and book value."}
    except Exception:
        graham = None

    # ── Magic Formula value metrics ───────────────────────────────────────────
    value = None
    try:
        ebit = at(inc, 0, "operate_profit")
        debt = _sum_borrowings(bs[-1]) if bs else None
        cash = at(bs, 0, "money_cap")
        ev = (market_cap + (debt or 0) - (cash or 0)) if market_cap else None
        fcf = at(cf, 0, "free_cashflow")
        tca, tcl = at(bs, 0, "total_cur_assets"), at(bs, 0, "total_cur_liab")
        nwc = (tca - tcl) if (tca is not None and tcl is not None) else None
        net_fixed = at(bs, 0, "fix_assets")
        roc = (ebit / (nwc + net_fixed) * 100) if (ebit is not None and nwc is not None and net_fixed and (nwc+net_fixed) > 0) else None
        earnings_yield = (ebit / ev * 100) if (ebit is not None and ev) else None
        fcf_yield = (fcf / market_cap * 100) if (fcf is not None and market_cap) else None
        ev_ebit = (ev / ebit) if (ev and ebit and ebit > 0) else None
        value = {
            "earnings_yield": round(earnings_yield, 1) if earnings_yield is not None else None,
            "roc": round(roc, 1) if roc is not None else None,
            "fcf_yield": round(fcf_yield, 1) if fcf_yield is not None else None,
            "ev_ebit": round(ev_ebit, 1) if ev_ebit is not None else None,
        }
    except Exception:
        value = None

    if not any([piotroski, altman, beneish and beneish.get("available"),
                graham and graham.get("number"), value]):
        return None
    return {"piotroski": piotroski, "altman": altman, "beneish": beneish,
            "graham": graham, "value": value, "is_financial": is_financial}


def _sum_borrowings(r: dict) -> float | None:
    total = None
    for k in ("st_borr", "lt_borr", "bond_payable", "non_cur_liab_due_1y", "lease_liab"):
        v = r.get(k)
        try:
            fv = float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            fv = None
        if fv is not None:
            total = (total or 0) + fv
    return total


def _money_cny(v: float) -> str:
    a = abs(v)
    if a >= 1e12: return f"¥{a/1e12:.2f}T"
    if a >= 1e9:  return f"¥{a/1e9:.1f}B"
    if a >= 1e6:  return f"¥{a/1e6:.0f}M"
    return f"¥{a:.0f}"


def _val_growth_ashare(
    annual_rows: list[dict], price_history: list[dict],
    trailing_pe: float | None, *, forward_pe: float | None,
    dv_ttm: float | None, netprofit_yoy: float | None, industry: str | None,
    trade_date: str | None = None, ts_code: str = "",
) -> dict | None:
    """A-share analogue of screener._valuation_growth_analysis, built from
    Tushare fina_indicator EPS + daily_basic pe_ttm/dv_ttm."""
    pctc = lambda v: round(float(v), 1) if v is not None else None
    tpe = float(trailing_pe) if trailing_pe is not None else None

    # dividend yield already a % from daily_basic.dv_ttm
    div_yield_pct = round(float(dv_ttm), 2) if dv_ttm is not None else None
    if div_yield_pct is not None and not (0 <= div_yield_pct <= 30):
        div_yield_pct = None

    # ── 1. Annual EPS series (oldest→newest, one row per year) ────────────────
    # fina_indicator can return restatements for the same period — keep the
    # first-seen (annual_rows arrives newest-first, so first = latest reported).
    eps_by_year: dict[int, dict] = {}
    for r in annual_rows:
        ed, eps = r.get("end_date"), r.get("eps")
        if not ed or eps is None:
            continue
        try:
            yr = int(ed[:4])
            if yr not in eps_by_year:
                eps_by_year[yr] = {"year": yr, "eps": round(float(eps), 2),
                                   "_date": _to_date_str(ed)}
        except (ValueError, TypeError):
            pass
    eps_pts: list[dict] = [eps_by_year[y] for y in sorted(eps_by_year)]

    eps_yoy = pctc(netprofit_yoy)
    eps_cagr_3y = eps_cagr_5y = None
    if len(eps_pts) >= 4:
        eps_cagr_3y = _cagr(eps_pts[-4]["eps"], eps_pts[-1]["eps"], 3)
    if len(eps_pts) >= 5:
        eps_cagr_5y = _cagr(eps_pts[-5]["eps"], eps_pts[-1]["eps"], 4)
    elif len(eps_pts) >= 2 and eps_cagr_3y is None:
        eps_cagr_3y = _cagr(eps_pts[0]["eps"], eps_pts[-1]["eps"], len(eps_pts) - 1)

    growth_for_peg = eps_cagr_3y if eps_cagr_3y is not None else eps_yoy
    growth_basis = "3Y EPS CAGR" if eps_cagr_3y is not None else "TTM net-profit YoY"

    earnings_growth = {
        "eps_yoy": eps_yoy, "eps_cagr_3y": eps_cagr_3y, "eps_cagr_5y": eps_cagr_5y,
        "eps_series": [{"year": e["year"], "eps": e["eps"]} for e in eps_pts],
        "growth_used": growth_for_peg, "growth_basis": growth_basis,
    }

    # ── 2. Historical P/E multiple (year-end price / annual EPS) ───────────────
    pe_series: list[dict] = []
    for e in eps_pts:
        if e["eps"] and e["eps"] > 0 and e["_date"]:
            px = _price_on_or_before(price_history, e["_date"])
            if px:
                pe_series.append({"year": e["year"], "pe": round(px / e["eps"], 1)})
    pe_cagr = pe_avg = pe_rerating = None
    if len(pe_series) >= 2:
        pe_cagr = _cagr(pe_series[0]["pe"], pe_series[-1]["pe"],
                        pe_series[-1]["year"] - pe_series[0]["year"])
        pe_avg = round(sum(p["pe"] for p in pe_series) / len(pe_series), 1)
        if tpe and pe_avg:
            pe_rerating = round((tpe / pe_avg - 1) * 100, 1)
    pe_growth = {
        "pe_series": pe_series, "pe_cagr": pe_cagr, "pe_avg": pe_avg,
        "pe_current": round(tpe, 1) if tpe else None, "pe_rerating": pe_rerating,
    }

    # ── 3. PEG / PEGY ─────────────────────────────────────────────────────────
    def _pv(x):
        if x is None or x < 0: return "unknown"
        if x < 1.0: return "cheap"
        if x < 1.5: return "fair"
        if x < 2.0: return "full"
        return "expensive"

    peg = pegy = None
    if tpe and growth_for_peg and growth_for_peg > 0:
        peg = round(tpe / growth_for_peg, 2)
        denom = growth_for_peg + (div_yield_pct or 0)
        if denom > 0:
            pegy = round(tpe / denom, 2)
    peg_block = {
        "pe": round(tpe, 1) if tpe else None, "growth_pct": growth_for_peg,
        "growth_basis": growth_basis, "div_yield_pct": div_yield_pct,
        "peg": peg, "pegy": pegy, "peg_verdict": _pv(peg), "pegy_verdict": _pv(pegy),
        "lynch_note": (
            "Fairly valued when P/E ≈ growth rate (PEG ≈ 1). "
            "Lynch's dividend-adjusted version (PEGY) rewards yield: "
            "(growth + yield) / P/E ≥ 1 is attractive."
        ),
    }

    # ── 4. Industry P/E: static anchor + LIVE full-industry median ────────────
    def _prem_verdict(pct: float) -> str:
        if   pct <= -20: return "discount"
        elif pct <   10: return "inline"
        elif pct <   30: return "premium"
        return "rich"

    typ_pe, typ_label = _cn_typical_pe(industry)
    premium_pct = None
    ind_verdict = "unknown"
    if typ_pe and tpe:
        premium_pct = round((tpe / typ_pe - 1) * 100, 1)
        ind_verdict = _prem_verdict(premium_pct)

    peer = _cn_peer_median_pe(industry, trade_date, ts_code)
    peer_premium_pct = None
    peer_verdict = "unknown"
    if peer and tpe and peer.get("median"):
        peer_premium_pct = round((tpe / peer["median"] - 1) * 100, 1)
        peer_verdict = _prem_verdict(peer_premium_pct)
    headline_verdict = peer_verdict if peer else ind_verdict

    industry_pe = {
        "sector": industry, "industry": industry, "label": typ_label,
        "typical_pe": typ_pe, "stock_pe": round(tpe, 1) if tpe else None,
        "forward_pe": None, "premium_pct": premium_pct, "verdict": ind_verdict,
        "peer_median":      peer["median"] if peer else None,
        "peer_n":           peer["n"]      if peer else None,
        "peer_basis":       peer["basis"]  if peer else None,
        "peers":            None,
        "peer_premium_pct": peer_premium_pct,
        "peer_verdict":     peer_verdict,
        "headline_verdict": headline_verdict,
    }

    if not eps_pts and tpe is None and typ_pe is None:
        return None

    return {
        "earnings_growth": earnings_growth, "pe_growth": pe_growth,
        "peg": peg_block, "industry_pe": industry_pe, "is_etf": False,
    }




_CN_BENCH_CACHE: dict[str, Any] = {"ts": 0.0, "rows": []}
_CN_BENCH_TTL = 6 * 3600
CN_BENCHMARK_NAME = "中证全指 · CSI All-Share"   # human label; the bare
                                       # 000985.SH code is meaningless on screen
CN_BENCHMARK_SYMBOL = "000985.SH"      # 中证全指 all-share — same benchmark the
                                       # Stage Analysis cache uses, so the chart's
                                       # RS line and the stage screen agree.




def _top_down_for(symbol: str):
    """Weinstein top-down context: market stage -> industry-group stage.
    Same memoised maps the screener uses, so the surfaces cannot disagree."""
    try:
        from .stage_analysis import top_down_for
        return top_down_for("cn", symbol)
    except Exception:
        return None


def _stage_analysis_for(ts_code: str):
    """Weinstein Stage Analysis for the detail page — same memoised map the
    screener uses, so the two surfaces can never show different numbers."""
    try:
        from .stage_analysis import stage_for
        return stage_for("cn", ts_code)
    except Exception:
        return None


def _cn_benchmark_history(start: str, end: str) -> list[dict]:
    """Benchmark daily closes for the chart's relative-strength panel.

    A-share stocks are compared against 中证全指 rather than SPY — SPY is
    meaningless for an A-share listing. Cached process-wide (6h): identical
    for every stock.
    """
    import time as _t
    now = _t.time()
    if _CN_BENCH_CACHE["rows"] and now - _CN_BENCH_CACHE["ts"] < _CN_BENCH_TTL:
        return _CN_BENCH_CACHE["rows"]
    try:
        # Tushare's suffix convention for 中证全指 is INCONSISTENT ACROSS
        # ENDPOINTS and both variants fail SILENTLY (zero rows, no error):
        #   index_weekly -> 000985.SH works, 000985.CSI returns nothing
        #   index_daily  -> 000985.CSI works, 000985.SH  returns nothing
        # Verified live 2026-08-20. So try both rather than hardcoding either;
        # a silent empty benchmark would blank the RS panel with no diagnostic.
        rows = []
        for _code in (CN_BENCHMARK_SYMBOL, CN_BENCHMARK_SYMBOL.split(".")[0] + ".CSI",
                      CN_BENCHMARK_SYMBOL.split(".")[0] + ".SH"):
            rows = cn_data.call("index_daily",
                                {"ts_code": _code, "start_date": start, "end_date": end},
                                "ts_code,trade_date,close")
            if rows:
                break
        rows.sort(key=lambda r: r["trade_date"])
        out = [{"d": _to_date_str(r["trade_date"]), "c": round(float(r["close"]), 2)}
               for r in rows if r.get("close") is not None]
        if out:
            _CN_BENCH_CACHE["rows"], _CN_BENCH_CACHE["ts"] = out, now
    except Exception:
        pass
    return _CN_BENCH_CACHE["rows"]


def _cn_px(v, digits: int = 2):  # noqa: ANN001, ANN201
    """OHLCV extractor for the A-share price_history payload — see the US
    twin `_ohlc_val` in screener.py."""
    try:
        f = float(v)
        if f != f:
            return None
        return round(f, digits) if digits else round(f)
    except (TypeError, ValueError):
        return None


def _to_date_str(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return None
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


# ── Moat heuristics (Chinese industry keywords) ───────────────────────────────

def _moat_hints(industry: str) -> list[str]:
    ind = industry or ""
    hints: list[str] = []
    if any(k in ind for k in ["白酒", "啤酒", "饮料", "食品", "化妆品", "中药"]):
        hints.append("intangible")          # brand moat
    if any(k in ind for k in ["银行", "保险", "证券", "电力", "燃气", "供水", "铁路", "机场", "港口"]):
        hints.append("scale")               # regulated / capital-intensive, licence-protected
    if any(k in ind for k in ["软件", "互联网", "平台", "电商", "游戏"]):
        hints.append("network")
        hints.append("switching")
    if any(k in ind for k in ["半导体", "医药", "生物", "芯片", "创新药"]):
        hints.append("intangible")
    if any(k in ind for k in ["煤炭", "钢铁", "有色", "水泥", "化工"]):
        hints.append("cost")                # commodity producers compete on cost
    return hints


# ── Gate 3 valuation thresholds (calibrated for A-share, not US) ─────────────

def _val_verdict(metric: str, value: float | None) -> str:
    if value is None:
        return "unknown"
    if metric in ("pe_ttm", "pb", "peg") and value <= 0:
        return "unknown"                     # losses / negative equity are not "cheap"
    if metric == "pe_ttm":
        if value <= 0: return "unknown"     # negative/no earnings
        if value < 20: return "undervalued"
        if value < 35: return "fair"
        if value < 50: return "watch"
        return "expensive"
    if metric == "pb":
        if value < 2: return "undervalued"
        if value < 4: return "fair"
        if value < 6: return "watch"
        return "expensive"
    if metric == "dv_ttm":
        if value > 3: return "undervalued"
        if value > 1.5: return "fair"
        if value > 0.5: return "watch"
        return "expensive"
    if metric == "peg":
        if value < 1.0: return "undervalued"
        if value < 1.5: return "fair"
        if value < 2.0: return "watch"
        return "expensive"
    return "unknown"


def screen_ashare(query: str) -> dict[str, Any]:
    try:
        row = cn_data.resolve_ticker(query)
    except cn_data.TushareError as exc:
        return {"error": str(exc)}
    if not row:
        return {"error": f"Could not find an A-share ticker matching '{query}'. Try a 6-digit code (e.g. 600519) or company name."}

    ts_code  = row["ts_code"]
    name     = row.get("name") or ts_code
    industry = row.get("industry") or "—"
    area     = row.get("area") or "—"

    try:
        # ── Latest market snapshot ──────────────────────────────────────────
        db = cn_data.latest_row(
            "daily_basic", ts_code,
            "ts_code,trade_date,close,pe_ttm,pb,total_mv,circ_mv,dv_ratio,dv_ttm,turnover_rate",
        )
        if not db:
            return {"error": f"No market data found for '{ts_code}'."}

        price       = db.get("close")
        market_cap  = (db.get("total_mv") or 0) * 10_000   # Tushare reports 万元 (10k CNY)
        trailing_pe = db.get("pe_ttm")
        pb          = db.get("pb")
        # Dividend yield = payments with an ex-date in the last twelve months ÷
        # price, computed here from the payment history. Tushare's two fields
        # were checked against it and against Tencent on 2026-10-12: `dv_ttm`
        # agrees (Wuliangye 7.31% vs 7.31% computed, 7.28% Tencent), while
        # `dv_ratio` — which this used to prefer — counted three of Wuliangye's
        # payments and read 11.80%. So: computed first, dv_ttm if there is no
        # history, dv_ratio never.
        dv_ttm = db.get("dv_ttm")
        try:
            _r, _y = _dividend_ttm(_dividend_events(_dividend_rows(ts_code)), price)
            if _y is not None:
                dv_ttm = _y
        except Exception:                          # noqa: BLE001
            pass

        # ── Company profile ──────────────────────────────────────────────────
        try:
            company_rows = cn_data.call(
                "stock_company", {"ts_code": ts_code},
                "ts_code,employees,main_business,introduction",
            )
            company = company_rows[0] if company_rows else {}
        except Exception:
            company = {}
        employees   = company.get("employees")
        description = (company.get("main_business") or company.get("introduction") or "")[:350]

        # ── Financial indicators (annual, for quality gate) ─────────────────
        start = (date.today() - timedelta(days=365 * 6)).strftime("%Y%m%d")
        end   = date.today().strftime("%Y%m%d")
        try:
            fina_rows = cn_data.call(
                "fina_indicator", {"ts_code": ts_code, "start_date": start, "end_date": end},
                "ts_code,end_date,ann_date,update_flag,eps,bps,roe,roe_waa,grossprofit_margin,debt_to_assets,"
                "ebitda,fcff,or_yoy,q_sales_yoy,q_op_yoy,q_netprofit_yoy,netprofit_yoy,current_ratio,quick_ratio",
            )
        except Exception:
            fina_rows = []
        fina_raw = list(fina_rows)      # every vintage, for the single-quarter growth cross-check
        # One row per period: the newest filing, blanks filled from its own
        # duplicates (see cn_data.newest_filing).
        _by_end: dict[str, list[dict]] = {}
        for _r in fina_rows:
            if _r.get("end_date"):
                _by_end.setdefault(_r["end_date"], []).append(_r)
        fina_rows = [cn_data.newest_filing(v) for v in _by_end.values()]
        fina_rows.sort(key=lambda r: r.get("end_date", ""), reverse=True)
        annual_rows = [r for r in fina_rows if (r.get("end_date") or "").endswith("1231")]
        latest_annual = annual_rows[0] if annual_rows else None
        latest_q      = fina_rows[0] if fina_rows else None

        # 加权平均净资产收益率 — the ROE in the annual report and on every data
        # site. Tushare's plain `roe` divides by YEAR-END equity instead, which
        # reads high for anything that pays out heavily (Moutai FY2025: 34.46
        # against the reported 32.53) and low for a bank, whose reported figure
        # is on ordinary shareholders' equity (CMB: 12.02 against 13.44).
        roe = None
        if latest_annual:
            roe = latest_annual.get("roe_waa") if latest_annual.get("roe_waa") is not None else latest_annual.get("roe")
        gross_margin   = latest_annual.get("grossprofit_margin") if latest_annual else None
        debt_to_assets = latest_annual.get("debt_to_assets") if latest_annual else None
        fcff           = latest_annual.get("fcff") if latest_annual else None
        rev_growth     = latest_q.get("or_yoy") if latest_q else None
        netprofit_yoy  = latest_q.get("netprofit_yoy") if latest_q else None

        # ── Balance sheet (annual, for debt structure) ──────────────────────
        try:
            bs_rows = cn_data.call(
                "balancesheet", {"ts_code": ts_code, "start_date": start, "end_date": end},
                "ts_code,end_date,total_assets,total_liab,total_cur_assets,total_cur_liab,"
                "st_borr,lt_borr,bond_payable,non_cur_liab_due_1y,lease_liab,money_cap,"
                "total_hldr_eqy_exc_min_int,undistr_porfit,accounts_receiv,"
                "fix_assets,cip,invest_real_estate,lt_eqt_invest,intan_assets,goodwill,"
                "trad_asset,fa_avail_for_sale",
            )
        except Exception:
            bs_rows = []

        # ── Income & cashflow (annual, for FCF margin / cash conversion) ────
        income_rows: list = []
        cf_rows: list = []
        try:
            income_rows = cn_data.call(
                "income", {"ts_code": ts_code, "start_date": start, "end_date": end},
                "ts_code,end_date,ann_date,f_ann_date,update_flag,total_revenue,revenue,oper_cost,"
                "operate_profit,total_profit,n_income,n_income_attr_p,sell_exp,admin_exp,basic_eps,diluted_eps",
            )
            # Latest filing vintage first: Tushare returns restated and original
            # versions of the same period side by side.
            income_annual = sorted(
                [r for r in income_rows if (r.get("end_date") or "").endswith("1231")],
                key=lambda r: (r["end_date"], r.get("f_ann_date") or "", r.get("update_flag") or ""),
                reverse=True,
            )
            latest_income = income_annual[0] if income_annual else None
        except Exception:
            latest_income = None

        try:
            cf_rows = cn_data.call(
                "cashflow", {"ts_code": ts_code, "start_date": start, "end_date": end},
                "ts_code,end_date,n_cashflow_act,free_cashflow,c_pay_acq_const_fiolta,depr_fa_coga_dpba",
            )
            cf_annual = sorted(
                [r for r in cf_rows if (r.get("end_date") or "").endswith("1231")],
                key=lambda r: r["end_date"], reverse=True,
            )
            latest_cf = cf_annual[0] if cf_annual else None
        except Exception:
            latest_cf = None

        total_revenue = latest_income.get("total_revenue") if latest_income else None
        net_income    = latest_income.get("n_income") if latest_income else None
        op_cashflow   = latest_cf.get("n_cashflow_act") if latest_cf else None

        fcf_margin = (fcff / total_revenue * 100) if (fcff and total_revenue) else None
        cash_conv  = (op_cashflow / net_income * 100) if (op_cashflow and net_income and net_income > 0) else None

        # ── Period bookkeeping for Gate 1 / Gate 3 labels ───────────────────
        def _fy(r: dict | None) -> str | None:
            e = (r or {}).get("end_date") or ""
            return f"FY{e[:4]}" if e.endswith("1231") else None

        fy_fina, fy_inc, fy_cf = _fy(latest_annual), _fy(latest_income), _fy(latest_cf)
        # Tushare's fina_indicator, income and cashflow tables update on
        # different days; around March-April one can already carry the new year
        # while another still holds the old. A ratio across two different years
        # is withheld rather than printed.
        fcf_mismatch = bool(fcf_margin is not None and fy_fina and fy_inc and fy_fina != fy_inc)
        cash_mismatch = bool(cash_conv is not None and fy_cf and fy_inc and fy_cf != fy_inc)
        if fcf_mismatch:
            fcf_margin = None
        if cash_mismatch:
            cash_conv = None
        try:
            _lst = next((b.get("list_date") for b in cn_data._stock_basic_list()
                         if b.get("ts_code") == ts_code), None)
        except Exception:                          # noqa: BLE001
            _lst = None
        list_date = f"{_lst[:4]}-{_lst[4:6]}-{_lst[6:]}" if _lst and len(_lst) == 8 else None
        lq_end = (latest_q or {}).get("end_date") or ""
        lq_label = ({"0331": f"Q1 {lq_end[:4]}", "0630": f"H1 {lq_end[:4]}",
                     "0930": f"9M {lq_end[:4]}", "1231": f"FY{lq_end[:4]}"}.get(lq_end[4:])
                    if len(lq_end) == 8 else None)

        # ── Same-quarter growth, quarter by quarter ─────────────────────────
        # Replaces `or_yoy`, which is YEAR-TO-DATE despite the old "most recent
        # quarter" label. No fallback to or_yoy: silently switching basis when
        # the single-quarter figure is unavailable would make the check lie.
        # Single-quarter statements (income report_type 2): only used to fill a
        # quarter the year-to-date subtraction must refuse across a restatement.
        try:
            single_rows = cn_data.call(
                "income", {"ts_code": ts_code, "start_date": start, "end_date": end, "report_type": "2"},
                "ts_code,end_date,ann_date,f_ann_date,update_flag,total_revenue,operate_profit,"
                "n_income,n_income_attr_p,basic_eps,diluted_eps")
        except Exception:                          # noqa: BLE001
            single_rows = []
        try:
            from .quarterly_growth import cn_quarterly
            quarterly_growth = cn_quarterly(income_rows, single_rows)
        except Exception:                          # noqa: BLE001
            quarterly_growth = None

        # Revenue and profit LEVELS, year by year and quarter by quarter. Growth
        # is measured against the prior year as restated in each year's own
        # report (income report_type 4), where the company restated it.
        try:
            comp_rows = cn_data.call(
                "income", {"ts_code": ts_code, "start_date": start, "end_date": end, "report_type": "4"},
                "ts_code,end_date,ann_date,f_ann_date,update_flag,total_revenue,operate_profit,"
                "n_income,n_income_attr_p")
        except Exception:                          # noqa: BLE001
            comp_rows = []
        try:
            from .rev_profit import cn_series
            revenue_profit = cn_series(income_rows, comp_rows, single_rows, fina_raw)
        except Exception:                          # noqa: BLE001
            revenue_profit = None

        # What comes next: broker forecasts and the company's own guidance.
        # Brokers forecast profit attributable to the parent (归母净利润), so the
        # year they are compared with has to be that line too — not net profit
        # including minorities, which is what the rest of this page charts.
        try:
            from .cn_forecasts import stock_forecasts
            _fy_end = (latest_income or {}).get("end_date") or ""
            _actual = ({"year": int(_fy_end[:4]),
                        "revenue": (latest_income or {}).get("total_revenue"),
                        "net_parent": (latest_income or {}).get("n_income_attr_p")}
                       if len(_fy_end) == 8 else None)
            _reported = max((r.get("end_date") or "" for r in income_rows), default="") or None
            # What each guided period turned out to be: newest filing of each.
            _attr: dict[str, tuple[str, float]] = {}
            for r in income_rows:
                e, v = r.get("end_date") or "", r.get("n_income_attr_p")
                k = r.get("f_ann_date") or r.get("ann_date") or ""
                if e and v is not None and (e not in _attr or k > _attr[e][0]):
                    _attr[e] = (k, float(v))
            # The last three reported years, newest filing of each, for the chart.
            _hist: dict[str, tuple[str, dict]] = {}
            for r in income_rows:
                e = r.get("end_date") or ""
                k = r.get("f_ann_date") or r.get("ann_date") or ""
                if e.endswith("1231") and (e not in _hist or k > _hist[e][0]):
                    _hist[e] = (k, r)
            _history = [{"year": int(e[:4]), "revenue": _hist[e][1].get("total_revenue"),
                         "net_parent": _hist[e][1].get("n_income_attr_p")}
                        for e in sorted(_hist)[-3:]]
            forecasts = stock_forecasts(ts_code, market_cap=market_cap or None, price=price,
                                        actual=_actual, latest_reported_end=_reported,
                                        actuals={e: v for e, (_, v) in _attr.items()},
                                        history=_history)
        except Exception:                          # noqa: BLE001
            forecasts = None
        qg = quarterly_growth or {}

        # Revenue growth when our own figure had to be withheld for a
        # restatement. The year-ago quarter on the restated basis is often not
        # in Tushare's income table at all (Wuliangye's Q1 2025), so it cannot
        # be recomputed here — but the company's own like-for-like comparison is
        # published, as fina_indicator's single-quarter `q_sales_yoy`. Checked
        # against Eastmoney 2026-10-12: Wuliangye Q2 2026 -13.16% on both. Used
        # for the latest period only, where the row's vintage is unambiguous.
        _rev_g, _rev_why, _rev_restated = qg.get("latest_rev_yoy"), qg.get("latest_rev_yoy_reason"), bool(qg.get("latest_restated"))
        _rev_src = ""
        _lq_lab = ({"0331": "Q1", "0630": "Q2", "0930": "Q3", "1231": "Q4"}.get(lq_end[4:], "") + f" {lq_end[:4]}") if len(lq_end) == 8 else None
        if (_rev_g is None and _rev_why in ("restatement_mix", "superseded_by_restatement")
                and latest_q and latest_q.get("q_sales_yoy") is not None and _lq_lab == qg.get("latest_label")):
            _rev_g, _rev_why, _rev_restated = round(float(latest_q["q_sales_yoy"]), 1), None, False
            _rev_src = (" — against the year-ago quarter as the company restated it "
                        "(Tushare's single-quarter figure; that quarter is not on file to recompute)")

        # ── Gate 1: Quantitative quality ─────────────────────────────────────
        checks = [
            _check("ROE",             roe,          15, "above", "%",
                   "Quality earnings without excessive leverage (annual, most recent fiscal year)"),
            _check("Gross Margin",    gross_margin, 40, "above", "%",
                   "Proxy for pricing power — absolute bar, not industry-relative"),
            _check("FCF Margin",      fcf_margin,   10, "above", "%",
                   "Free cash flow to firm / revenue — cash can't be manipulated like accounting earnings"),
            _check("Cash Conversion", cash_conv,    85, "above", "%",
                   "Operating cash flow / net income. Near 100% = no accounting games"),
            _gate_growth_check("Revenue Growth", _rev_g, _rev_why, _rev_restated,
                   f"Latest single quarter{' (' + qg['latest_label'] + ')' if qg.get('latest_label') else ''} vs the same quarter last year — minimum relevance floor{_rev_src}"),
            _gate_growth_check("EPS Growth", qg.get("latest_eps_yoy"), qg.get("latest_eps_yoy_reason"),
                   bool(qg.get("latest_restated")),
                   f"Diluted EPS, latest single quarter{' (' + qg['latest_label'] + ')' if qg.get('latest_label') else ''} vs the same quarter last year"),
            _check("Debt / Assets",   debt_to_assets, 60, "below", "%",
                   "资产负债率 — survivability under stress; >60% is elevated for non-financials"),
        ]
        fy = fy_fina or "last fiscal year"
        q_lab = qg.get("latest_label")
        q_window = (f"{q_lab} vs {q_lab.split()[0]} {int(q_lab.split()[1]) - 1}"
                    if q_lab and q_lab.startswith("Q") else "latest quarter vs same quarter last year")
        periods = {
            "ROE":             f"{fy} · full year · weighted average (加权), as reported",
            "Gross Margin":    f"{fy} · full year",
            "FCF Margin":      f"{fy_fina or fy} free cash flow to firm ÷ {fy_inc or fy} revenue",
            "Cash Conversion": f"{fy_cf or fy} operating cash flow ÷ {fy_inc or fy} net income",
            "Revenue Growth":  f"{q_window} · single quarter",
            "EPS Growth":      f"{q_window} · diluted EPS",
            "Debt / Assets":   f"{fy} · year-end balance sheet",
        }
        for c in checks:
            c["period"] = periods.get(c["name"])
            if c["name"] == "FCF Margin" and fcf_mismatch:
                c["display"], c["note"] = "n/a", c["note"] + f" — withheld: cash flow is {fy_fina}, revenue {fy_inc}"
            if c["name"] == "Cash Conversion" and cash_mismatch:
                c["display"], c["note"] = "n/a", c["note"] + f" — withheld: cash flow is {fy_cf}, net income {fy_inc}"
            # Balance-sheet ratios measured before the IPO describe a smaller,
            # pre-listing company: equity and cash jump when the new capital lands.
            if (list_date and c["name"] in ("ROE", "Debt / Assets") and fy_fina
                    and list_date > f"{fy_fina[2:]}-12-31"):
                c["caveat"] = (f"Listed {list_date}, after this {fy_fina} balance sheet — the figure uses pre-IPO "
                               f"equity and assets, which exaggerates it; it will move sharply once IPO proceeds are included.")

        passed   = sum(1 for c in checks if c["pass"] is True)
        failed   = sum(1 for c in checks if c["pass"] is False)
        na_count = sum(1 for c in checks if c["pass"] is None)
        scorable = len(checks) - na_count
        if scorable == 0:
            g1_verdict = "unknown"
        elif passed >= 5:
            g1_verdict = "pass"
        elif passed >= 3:
            g1_verdict = "partial"
        else:
            g1_verdict = "fail"

        # ── Gate 3: Valuation ─────────────────────────────────────────────────
        peg = (trailing_pe / netprofit_yoy) if (trailing_pe and netprofit_yoy and netprofit_yoy > 0) else None
        g3_metrics = {
            "pe_ttm": {"value": round(trailing_pe, 1) if trailing_pe is not None else None,
                       "label": "P/E (TTM)", "unit": "×",
                       "thresholds": "<20 cheap · 20–35 fair · 35–50 watch · >50 expensive",
                       "verdict": _val_verdict("pe_ttm", trailing_pe)},
            "pb":     {"value": round(pb, 2) if pb is not None else None,
                       "label": "Price / Book", "unit": "×",
                       "thresholds": "<2 cheap · 2–4 fair · 4–6 watch · >6 expensive",
                       "verdict": _val_verdict("pb", pb)},
            "dv_ttm": {"value": round(dv_ttm, 2) if dv_ttm is not None else None,
                       "label": "Dividend Yield", "unit": "%",
                       "thresholds": ">3% cheap · 1.5–3% fair · 0.5–1.5% watch · <0.5% expensive",
                       "verdict": _val_verdict("dv_ttm", dv_ttm)},
            "peg":    {"value": round(peg, 2) if peg is not None else None,
                       "label": "PEG Ratio", "unit": "×",
                       "thresholds": "<1.0 cheap · 1.0–1.5 fair · >2.0 expensive",
                       "verdict": _val_verdict("peg", peg)},
        }
        _g3p = {
            "pe_ttm": "current price ÷ trailing twelve-month EPS",
            "pb":     "current price ÷ book value per share at the latest filing",
            "dv_ttm": "trailing twelve-month dividends ÷ current price",
            # Two different windows, stated plainly: TTM valuation over a
            # year-to-date growth rate. Crude for seasonal or fast-ramping firms.
            "peg":    f"P/E (TTM) ÷ {lq_label or 'latest'} year-to-date net-profit growth — mixed windows, crude",
        }
        for _k, _m in g3_metrics.items():
            _m["period"] = _g3p.get(_k)
        verdicts = [m["verdict"] for m in g3_metrics.values() if m["verdict"] != "unknown"]
        exp_ct = verdicts.count("expensive")
        und_ct = verdicts.count("undervalued")
        if exp_ct >= 2:
            g3_verdict = "expensive"
        elif und_ct >= 2:
            g3_verdict = "pass"
        elif exp_ct == 1 and und_ct == 0:
            g3_verdict = "watch"
        else:
            g3_verdict = "fair"

        moat_hints = _moat_hints(industry)

        raw_data = {
            "trailing_pe": round(trailing_pe, 1) if trailing_pe is not None else None,
            "price_book":  round(pb, 2) if pb is not None else None,
            "beta":        None,
            "ebitda":      latest_annual.get("ebitda") if latest_annual else None,
            "total_debt":  None,
            # latest_annual is filtered to end_date.endswith("1231") rows only — this
            # is the most recent fiscal YEAR's FCFF, not a rolling TTM sum. Renamed
            # from "fcf_ttm" 2026-08-10 (same mislabeling as the US screener).
            "fcf_annual":  round(fcff) if fcff is not None else None,
            "op_cash":     round(op_cashflow) if op_cashflow is not None else None,
            "market_cap":  market_cap,
            "ev":          None,
            "employees":   employees,
        }

        # ── Price history (5Y daily) ─────────────────────────────────────────
        try:
            ph_start = (date.today() - timedelta(days=365 * 5)).strftime("%Y%m%d")
            ph_rows = cn_data.call(
                "daily", {"ts_code": ts_code, "start_date": ph_start, "end_date": end},
                "ts_code,trade_date,open,high,low,close,vol",
            )
            ph_rows.sort(key=lambda r: r["trade_date"])
            # Same call, more fields — Tushare's `daily` already carries OHLCV,
            # so candlestick data costs no extra request. NOTE: `vol` is in 手
            # (lots of 100 shares), Tushare's native unit — fine for relative
            # bar heights, but do not present it as a raw share count.
            price_history = [
                {"d": _to_date_str(r["trade_date"]), "c": round(float(r["close"]), 2),
                 "o": _cn_px(r.get("open")), "h": _cn_px(r.get("high")),
                 "l": _cn_px(r.get("low")), "v": _cn_px(r.get("vol"), 0)}
                for r in ph_rows if r.get("close") is not None
            ]
        except Exception:
            price_history = []

        # ── Take-Profit Lab (same algorithm as US screener, CNY-denominated) ─
        take_profit = _build_take_profit(price_history)

        # ── Capital Return: dividend + buyback ───────────────────────────────
        capital_return = _capital_return(ts_code, market_cap, dv_ttm, price)

        # ── Flow: top holders + major-shareholder trading (insider proxy) ──
        flow_data = _flow_data(ts_code, price)

        # ── Valuation vs. growth (EPS/PE growth, PEG/PEGY, industry P/E) ──
        val_growth = _val_growth_ashare(
            annual_rows, price_history, trailing_pe, forward_pe=None,
            dv_ttm=dv_ttm, netprofit_yoy=netprofit_yoy, industry=industry,
            trade_date=db.get("trade_date"), ts_code=ts_code,
        )

        return {
            "ticker":      ts_code,
            "name":        name,
            "sector":      industry,
            "industry":    industry,
            "description": description or f"{area} · {industry}",
            "price":       round(price, 2) if price is not None else None,
            "market_cap":  market_cap,
            "currency":    CNY,
            "website":     "",
            "gate1": {
                "verdict": g1_verdict, "passed": passed, "failed": failed,
                "na": na_count, "scorable": scorable, "checks": checks,
            },
            "gate2": {"moat_hints": moat_hints},
            "gate3": {"verdict": g3_verdict, "metrics": g3_metrics},
            "raw":           raw_data,
            "cycle":         None,
            "earnings":      None,
            "quarterly_growth": quarterly_growth,
            "revenue_profit": revenue_profit,
            "forecasts": forecasts,
            "price_history": price_history,
            "benchmark_history": _cn_benchmark_history(ph_start, end),
            "benchmark_symbol": CN_BENCHMARK_SYMBOL,
            "benchmark_name": CN_BENCHMARK_NAME,
            "stage_analysis": _stage_analysis_for(ts_code),
            "top_down": _top_down_for(ts_code),
            "flow_data":     flow_data,
            "take_profit":   take_profit,
            "fear_score":    None,
            "lynch_data":    None,
            "capital_return": capital_return,
            "val_growth":    val_growth,
            "debt_structure": _debt_structure_ashare(bs_rows, industry, latest_annual),
            "hidden_assets": _hidden_assets_ashare(bs_rows, market_cap, pb),
            "cashflow_margins": _cashflow_margins_ashare(income_rows, cf_rows),
            "fundamental_scores": _fundamental_scores_ashare(
                bs_rows, income_rows, cf_rows, fina_rows, market_cap, price,
                any(k in (industry or "") for k in ("银行", "保险", "证券")),
            ),
        }
    except Exception as exc:
        return {"error": f"Error fetching A-share data for '{ts_code}': {exc}"}


# ── Take-Profit Lab (ported from screener.py, CNY-denominated notes) ─────────

def _build_take_profit(price_history: list[dict]) -> dict:
    def _tp_sim(closes: list[float]) -> dict:
        entry = closes[0]
        current = closes[-1]
        N = len(closes)
        years = N / 252

        def _cagr(ret_pct: float) -> float:
            return round(((1 + ret_pct / 100) ** (1 / max(years, 0.1)) - 1) * 100, 1)

        def _max_dd(cs: list[float]) -> float:
            peak = cs[0]; worst = 0.0
            for c in cs:
                peak = max(peak, c)
                worst = max(worst, (peak - c) / peak)
            return round(worst * 100, 1)

        def _trailing_stop(stop_pct: float):
            peak = entry
            for i, c in enumerate(closes):
                peak = max(peak, c)
                if c < peak * (1 - stop_pct):
                    ret = round((c - entry) / entry * 100, 1)
                    return ret, _cagr(ret), True, round(c, 2), i
            ret = round((current - entry) / entry * 100, 1)
            return ret, _cagr(ret), False, None, N - 1

        def _tiered(tiers: list[tuple]):
            remaining = 1.0; blended = 0.0; tier_i = 0
            hits = []
            for c in closes:
                while tier_i < len(tiers) and remaining > 1e-9:
                    thresh, frac = tiers[tier_i]
                    if (c - entry) / entry >= thresh:
                        sell = min(frac, remaining)
                        blended += sell * (c - entry) / entry
                        hits.append(thresh)
                        remaining -= sell; tier_i += 1
                    else:
                        break
            blended += remaining * (current - entry) / entry
            ret = round(blended * 100, 1)
            return ret, _cagr(ret), len(hits)

        def _hard_exit(thresh: float):
            for c in closes:
                if (c - entry) / entry >= thresh:
                    ret = round(thresh * 100, 1)
                    return ret, _cagr(ret), True
            ret = round((current - entry) / entry * 100, 1)
            return ret, _cagr(ret), False

        hold_ret  = round((current - entry) / entry * 100, 1)
        hold_cagr = _cagr(hold_ret)
        hold_dd   = _max_dd(closes)
        peak_price = max(closes)
        peak_gain  = round((peak_price - entry) / entry * 100, 1)
        is_rc = peak_gain > 20 and hold_ret < peak_gain - 15

        ts15_ret, ts15_cagr, ts15_hit, ts15_px, _ = _trailing_stop(0.15)
        ts25_ret, ts25_cagr, ts25_hit, ts25_px, _ = _trailing_stop(0.25)
        tier_ret, tier_cagr, tier_hits           = _tiered([(0.25, 1 / 3), (0.75, 1 / 3)])
        hard50_ret, hard50_cagr, hard50_hit      = _hard_exit(0.50)

        if hold_dd < 18 and not is_rc and hold_ret > 0:
            cls = "hold"
        elif hold_dd > 35 or is_rc:
            cls = "tier"
        elif hold_dd > 25:
            cls = "trailing"
        else:
            cls = "hold"

        action_levels = [
            {"label": "+25% tier 1",    "price": round(current * 1.25, 2)},
            {"label": "+50% milestone", "price": round(current * 1.50, 2)},
            {"label": "+75% tier 2",    "price": round(current * 1.75, 2)},
        ]

        return {
            "entry": round(entry, 2), "current": round(current, 2), "years": round(years, 1),
            "strategies": [
                {"name": "Hold forever",        "ret": hold_ret,   "cagr": hold_cagr,   "max_dd": hold_dd, "status": "holding", "note": ""},
                {"name": "Trailing stop 15%",   "ret": ts15_ret,   "cagr": ts15_cagr,   "max_dd": None,    "status": "exited" if ts15_hit else "holding", "note": f"Exited at {CNY}{ts15_px}" if ts15_hit else "Never triggered"},
                {"name": "Trailing stop 25%",   "ret": ts25_ret,   "cagr": ts25_cagr,   "max_dd": None,    "status": "exited" if ts25_hit else "holding", "note": f"Exited at {CNY}{ts25_px}" if ts25_hit else "Never triggered"},
                {"name": "Tiered ⅓+⅓ at +25%/+75%", "ret": tier_ret, "cagr": tier_cagr, "max_dd": None,  "status": "partial", "note": f"{tier_hits}/2 tiers hit"},
                {"name": "Exit all at +50%",    "ret": hard50_ret, "cagr": hard50_cagr, "max_dd": None,    "status": "exited" if hard50_hit else "holding", "note": "Milestone hit" if hard50_hit else "Not yet reached"},
            ],
            "hold_dd": hold_dd, "peak_gain": peak_gain, "is_rc": is_rc, "cls": cls, "levels": action_levels,
        }

    try:
        take_profit: dict = {}
        for label, days in [("1Y", 365), ("3Y", 1095), ("5Y", 1825)]:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            idx = next((i for i, p in enumerate(price_history) if p["d"] >= cutoff), None)
            if idx is not None and idx < len(price_history) - 30:
                closes_slice = [p["c"] for p in price_history[idx:]]
                take_profit[label] = {"entry_date": price_history[idx]["d"], **_tp_sim(closes_slice)}
        return take_profit
    except Exception:
        return {}


# ── Capital Return: dividend + buyback ────────────────────────────────────────

_DIV_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _dividend_events(rows: list[dict]) -> list[dict]:
    """Cash dividends actually paid — one row each, per TODAY'S share, newest first.

    Checked against Tencent's and Tushare's own yields on 2026-10-12; two traps
    in the raw `dividend` table were inflating or distorting what this card
    showed:

      * DUPLICATES. A dividend announced twice (plan, then implementation
        notice) comes back as two "实施" rows. Moutai's FY2025 final of ¥28.02
        was counted twice, making its dividend ¥80.01 a share instead of
        ¥51.98. CATL's special appears under two different period ends.
        Rows are keyed on ex-date and amount, which a duplicate cannot differ
        on.

      * BONUS SHARES. `cash_div` is per share as it stood on that ex-date.
        Shanghai Yanpu issued 4.9 bonus shares per 10 three years running, so
        its ¥0.388 (2025) and ¥0.28 (2026) are per different shares — on
        today's share count they are ¥0.175 and ¥0.188: a rise, where the raw
        figures read as a cut. Each payment is divided by every bonus issue
        from its own ex-date onward.
    """
    seen: set[tuple] = set()
    ev: list[dict] = []
    for r in rows or []:
        if r.get("div_proc") != "实施" or not r.get("ex_date"):
            continue
        cash = r.get("cash_div_tax") if r.get("cash_div_tax") is not None else r.get("cash_div")
        cash, stk = float(cash or 0.0), float(r.get("stk_div") or 0.0)
        if cash <= 0 and stk <= 0:
            continue
        key = (r["ex_date"], round(cash, 6), round(stk, 6))
        if key in seen:
            continue
        seen.add(key)
        ev.append({"ex_date": r["ex_date"], "end_date": r.get("end_date") or "",
                   "cash": cash, "stk": stk})
    ev.sort(key=lambda e: e["ex_date"], reverse=True)
    factor = 1.0
    for e in ev:                              # newest first: later bonus issues accumulate
        factor *= 1.0 + e["stk"]
        e["adj"] = e["cash"] / factor
    return [e for e in ev if e["cash"] > 0]


def _dividend_rows(ts_code: str) -> list[dict]:
    import time as _time
    hit = _DIV_CACHE.get(ts_code)
    if hit and _time.time() - hit[0] < 3600:
        return hit[1]
    rows = cn_data.call("dividend", {"ts_code": ts_code},
                        "ts_code,end_date,div_proc,stk_div,cash_div,cash_div_tax,ex_date")
    _DIV_CACHE[ts_code] = (_time.time(), rows)
    return rows


def _dividend_ttm(events: list[dict], price: float | None, today: date | None = None) -> tuple[float | None, float | None]:
    """(dividends per share over the last 12 months by ex-date, yield %)."""
    today = today or date.today()
    lo, hi = (today - timedelta(days=365)).strftime("%Y%m%d"), today.strftime("%Y%m%d")
    paid = [e["adj"] for e in events if lo < e["ex_date"] <= hi]
    if not paid:
        return None, None
    total = sum(paid)
    return total, (total / price * 100 if price else None)


def _capital_return(ts_code: str, market_cap: float, dv_ttm: float | None = None,
                    price: float | None = None) -> dict:
    dividend: dict = {"has_dividend": False}
    try:
        events = _dividend_events(_dividend_rows(ts_code))
        ttm_rate, ttm_yield = _dividend_ttm(events, price)
        paid = events
        if paid:
            yearly: dict[int, float] = {}
            for r in paid:
                end_date = r.get("end_date") or ""
                if len(end_date) < 4:
                    continue
                yr = int(end_date[:4])
                yearly[yr] = yearly.get(yr, 0.0) + r["adj"]
            years_sorted = sorted(yearly)
            current_year = date.today().year
            full_years = [y for y in years_sorted if y < current_year]
            history = [{"year": y, "total": round(yearly[y], 3 if yearly[y] < 1 else 2)} for y in years_sorted]
            ytd_total = round(yearly.get(current_year, 0.0), 3)

            streak = 0
            for i in range(len(full_years) - 1, 0, -1):
                if yearly[full_years[i]] > yearly[full_years[i - 1]]:
                    streak += 1
                else:
                    break

            def _cagr_over(n):
                if len(full_years) < n:
                    return None
                y0, y1 = full_years[-n], full_years[-1]
                v0, v1 = yearly[y0], yearly[y1]
                span = y1 - y0
                if v0 <= 0 or span <= 0:
                    return None
                return round(((v1 / v0) ** (1 / span) - 1) * 100, 1)

            if len(full_years) < 2:
                trend = "Recently Initiated"
            else:
                last_v, prev_v = yearly[full_years[-1]], yearly[full_years[-2]]
                if streak >= 3:
                    trend = "Growing"
                elif last_v < prev_v * 0.8:
                    trend = "Cut"
                elif last_v < prev_v:
                    trend = "Declining"
                elif prev_v and abs(last_v - prev_v) / prev_v < 0.01:
                    trend = "Flat"
                else:
                    trend = "Growing"

            latest_paid = max(paid, key=lambda r: r.get("ex_date", ""))
            # Rate and yield are ONE basis — the last twelve months of payments
            # by ex-date — so the two figures on the card divide into each other.
            # (They used to be a fiscal-year total beside a vendor yield.)
            _rate = ttm_rate if ttm_rate is not None else yearly.get(full_years[-1] if full_years else years_sorted[-1], 0)
            _yield = ttm_yield if ttm_yield is not None else dv_ttm
            dividend = {
                "has_dividend": True,
                "rate": round(_rate, 3 if _rate < 1 else 2),
                "yield_pct": round(_yield, 2) if _yield is not None else None,
                "yield_basis": ("payments with an ex-date in the last 12 months, per today's share "
                                "(adjusted for bonus shares issued since)") if ttm_rate is not None
                               else "no payment in the last 12 months — vendor yield shown",
                "payout_ratio_pct": None,
                "last_div_value": round(latest_paid["adj"], 3 if latest_paid["adj"] < 1 else 2),
                "ex_div_date": _to_date_str(latest_paid.get("ex_date")),
                "last_div_date": _to_date_str(latest_paid.get("ex_date")),
                "consecutive_growth_years": streak,
                "cagr_5y_pct": _cagr_over(5),
                "cagr_10y_pct": _cagr_over(10),
                "trend": trend,
                "ytd_total": ytd_total,
                "history": history[-15:],
            }
    except Exception:
        dividend = {"has_dividend": False}

    buyback: dict = {"has_buybacks": False}
    try:
        rows = cn_data.call(
            "repurchase", {"ts_code": ts_code},
            "ts_code,ann_date,end_date,proc,vol,amount",
        )
        done = [r for r in rows if r.get("proc") in ("完成", "实施") and r.get("amount")]
        if done:
            yearly_amt: dict[int, float] = {}
            for r in done:
                end_date = r.get("end_date") or r.get("ann_date") or ""
                if len(end_date) < 4:
                    continue
                yr = int(end_date[:4])
                yearly_amt[yr] = yearly_amt.get(yr, 0.0) + float(r["amount"])
            years_sorted = sorted(yearly_amt)
            annual = [{"period": f"FY{y}", "amount": round(yearly_amt[y])} for y in years_sorted]
            latest_amt = annual[-1]["amount"]
            buyback_yield_pct = round(latest_amt / market_cap * 100, 2) if market_cap else None

            if len(annual) >= 2:
                prev_amt = annual[-2]["amount"]
                if latest_amt < prev_amt * 0.1:
                    trend = "Paused"
                elif latest_amt > prev_amt * 1.15:
                    trend = "Accelerating"
                elif latest_amt < prev_amt * 0.85:
                    trend = "Slowing"
                else:
                    trend = "Steady"
            else:
                trend = "Steady"

            buyback = {
                "has_buybacks": True,
                "annual": annual[-8:],
                "latest_amount": latest_amt,
                "buyback_yield_pct": buyback_yield_pct,
                "shares_now": None,
                "shares_3y_ago": None,
                "reduction_3y_pct": None,
                "trend": trend,
            }
    except Exception:
        buyback = {"has_buybacks": False}

    return {"dividend": dividend, "buyback": buyback}


# ── Flow: top holders + major shareholder trading (insider proxy) ────────────

def _flow_data(ts_code: str, price: float | None = None) -> dict | None:
    try:
        holders = cn_data.call(
            "top10_holders", {"ts_code": ts_code},
            "ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio,hold_change",
        )
        if not holders:
            return None
        holders.sort(key=lambda r: r.get("end_date", ""), reverse=True)
        latest_period = holders[0].get("end_date") if holders else None
        latest = [h for h in holders if h.get("end_date") == latest_period][:10]

        def _holder_pct_change(h: dict) -> float:
            # Tushare's hold_change is an ABSOLUTE share delta, not a percentage —
            # convert to QoQ % change vs the implied prior-period holding.
            amount = float(h.get("hold_amount") or 0)
            change = float(h.get("hold_change") or 0)
            prev = amount - change
            if prev > 0:
                return round(change / prev * 100, 1)
            return 100.0 if change > 0 else 0.0

        top_holders = [{
            "name":       h.get("holder_name") or "—",
            "pct_held":   round(float(h.get("hold_ratio") or 0), 2),
            "shares":     int(h.get("hold_amount") or 0),
            "value":      round(float(h.get("hold_amount") or 0) * price) if price else 0,
            "pct_change": _holder_pct_change(h),
            "date":       _to_date_str(h.get("end_date")) or "",
        } for h in latest]

        try:
            trades = cn_data.call(
                "stk_holdertrade", {"ts_code": ts_code},
                "ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,after_share",
            )
        except Exception:
            trades = []
        trades.sort(key=lambda r: r.get("ann_date", ""), reverse=True)

        insider_txns = []
        buy_shares = sell_shares = 0.0
        buy_count = sell_count = 0
        six_mo_cutoff = (date.today() - timedelta(days=182)).strftime("%Y%m%d")
        for tr in trades[:30]:
            vol = float(tr.get("change_vol") or 0)
            kind = "Buy" if tr.get("in_de") == "IN" else "Sell"
            if (tr.get("ann_date") or "") >= six_mo_cutoff:
                if kind == "Buy":
                    buy_shares += vol; buy_count += 1
                else:
                    sell_shares += vol; sell_count += 1
            insider_txns.append({
                "insider":  tr.get("holder_name") or "—",
                "position": tr.get("holder_type") or "",
                "kind":     kind,
                "text":     f"{'增持' if kind == 'Buy' else '减持'} {abs(vol):,.0f} 股",
                "shares":   int(abs(vol)),
                "value":    None,
                "date":     _to_date_str(tr.get("ann_date")) or "",
            })

        insider_summary = {
            "buy_shares": buy_shares, "buy_count": buy_count,
            "sell_shares": sell_shares, "sell_count": sell_count,
            "net_shares": buy_shares - sell_shares,
            "total_held": None,
            "pct_net": None,
        } if (buy_count or sell_count) else None

        top3pct = sum(h["pct_held"] for h in top_holders[:3])
        return {
            "inst_pct":    round(top3pct, 2),
            "insider_pct": 0,
            "inst_float":  round(top3pct, 2),
            "inst_count":  len(top_holders),
            "top_holders": top_holders,
            "etf_exposure": [],
            "ret_12m":     None,
            "insider_summary": insider_summary,
            "insider_txns":    insider_txns[:12],
        }
    except Exception:
        return None

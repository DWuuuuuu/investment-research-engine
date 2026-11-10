"""Total revenue and profit, period by period, for the stock page.

Every other card on the page shows a RATIO — margins, returns, multiples. A
ratio hides the two things an investor actually wants to see first: is the
business bigger than it was, and does it earn anything. This builds the levels
themselves, so a rising margin on falling revenue cannot pass for progress.

THREE LINES, NOT TWO
--------------------
Revenue, OPERATING profit and NET profit. The third is not redundant: Nebius
reported +$584m of net income in Q2 2025 against an operating loss of $111m,
the difference being a one-off gain on the sale of a shareholding. A card
showing only revenue and net profit would have drawn that as a profitable
quarter. Showing both lines makes the gap between them visible, which is
exactly where one-offs, interest and tax live.

WHAT IS COMPARABLE WITH WHAT
----------------------------
US annual figures come from the income statement as filed, each column a
fiscal year, labelled by the year its fiscal period ENDS — so a retailer whose
year ends in January 2026 appears as FY2026, with the end date spelled out.

A-share filings are year-to-date, so a single quarter only exists by
subtraction, and that subtraction is invalid across a restatement. Both the
de-cumulation and the rules for refusing it live in `quarterly_growth`; this
module calls them rather than repeating them, so a quarter withheld from the
growth table is withheld from this chart too.

Growth off a loss is refused the same way it is everywhere else on the page:
no percentage is printed for a swing out of or into a loss, because the
arithmetic is valid and the meaning is not.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Callable

from .quarterly_growth import cn_discrete, pct_growth

_MAX_ANNUAL = 8
_MAX_QUARTERS = 12
_TTM_QUARTERS = 4

_US_REVENUE = ("Total Revenue", "Operating Revenue")
# AS REPORTED, in both cases — checked against SEC filings on 2026-10-12.
#
# Yahoo's "Operating Income" is its own normalised figure: it leaves out
# whatever the company books as "other operating charges". For most companies
# that is nothing and the two rows are identical (Apple, Microsoft). Where it
# is not, the gap is large: Coca-Cola's 2024 reads $14.0bn against the $9.99bn
# in its 10-K (the fairlife charge), Nvidia's FY2023 $5.58bn against $4.22bn
# (the abandoned Arm deal). The as-reported row is the one a reader can look
# up, so it comes first.
#
# "Net Income Common Stockholders" is net income less preferred dividends. For
# a bank that is a different number from the one it announces — JPMorgan's 2025
# is $55.7bn on that row and $57.0bn in its filing — so the headline row comes
# first here too.
_US_OPERATING = ("Total Operating Income As Reported", "Operating Income", "EBIT")
_US_NET = ("Net Income", "Net Income Common Stockholders",
           "Net Income From Continuing Operation Net Minority Interest")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _margin(profit: float | None, revenue: float | None) -> float | None:
    if profit is None or not revenue or revenue <= 0:
        return None
    return round(profit / revenue * 100, 1)


def cagr(first: float | None, last: float | None, years: float) -> tuple[float | None, str | None]:
    """Compound growth, refusing the cases where the formula returns a number
    that means nothing: a loss at either end has no growth rate."""
    if first is None or last is None or years <= 0:
        return None, None
    if first <= 0:
        return None, "negative_base"
    if last <= 0:
        return None, "turned_to_loss"
    return round(((last / first) ** (1 / years) - 1) * 100, 1), None


_SERIES = (("revenue", "rev_yoy"), ("operating", "op_yoy"), ("net", "net_yoy"))


def _trend(rows: list[dict], key: str, dst: str, periods_per_year: float,
           vetoed: dict[int, str]) -> dict[str, Any]:
    """Level now, change on the period before, and compound growth across the
    whole window — the three numbers a trend line is read for. A vetoed step
    (one that crosses a restatement) blocks both, because the two sides are on
    different accounting bases and the arithmetic would look clean anyway."""
    pts = [(i, r) for i, r in enumerate(rows) if r.get(key) is not None]
    out: dict[str, Any] = {"latest": None, "yoy": None, "yoy_reason": None,
                           "cagr": None, "cagr_reason": None, "span": None}
    if not pts:
        return out
    last_i, last_r = pts[-1]
    out["latest"] = last_r[key]
    if len(pts) < 2:
        return out
    out["yoy"], out["yoy_reason"] = last_r.get(dst), last_r.get(dst + "_reason")
    years = (len(pts) - 1) / periods_per_year
    crossed = next((vetoed[i] for i, _ in pts[1:] if i in vetoed), None)
    if crossed:
        out["cagr_reason"] = crossed
    else:
        out["cagr"], out["cagr_reason"] = cagr(pts[0][1][key], last_r[key], years)
    out["span"] = {"from": pts[0][1]["label"], "to": last_r["label"], "years": round(years, 2)}
    return out


def _year_ago(rows: list[dict], i: int, lag: int) -> int | None:
    """Index of the row one year before row `i`.

    For quarters this is found by DATE, not by counting back four rows: a
    withheld or missing quarter leaves a hole, and four rows back from the far
    side of a hole is a different quarter of the year.
    """
    if lag == 1:
        return i - 1 if i >= 1 else None
    try:
        cur = date.fromisoformat(rows[i].get("end") or "")
    except ValueError:
        return i - lag if i >= lag else None
    for j in range(i - 1, -1, -1):
        try:
            gap = (cur - date.fromisoformat(rows[j].get("end") or "")).days
        except ValueError:
            continue
        if 350 <= gap <= 380:
            return j
        if gap > 380:
            break
    return None


_CONFIRM_PP, _CONFIRM_REL = 0.5, 0.01      # how closely "the same growth figure" has to agree


def _finish(rows: list[dict], periods_per_year: float,
            vetoed: dict[int, str] | None = None,
            bases: dict[int, dict[str, float]] | None = None,
            published: dict[int, dict[str, float]] | None = None) -> dict[str, Any]:
    """Shared tail: per-row margins and same-basis growth, then the summary.

    `vetoed` maps a row index to the reason the step INTO it cannot be measured.
    `bases` maps a row index to the prior-year figures AS THE COMPANY RESTATED
    THEM in that row's own report. Where one exists it is the comparison base —
    it is the company's own like-for-like — and the step is not vetoed.
    `published` maps a row index to the like-for-like growth the data vendor
    publishes for that period. It never supplies a number. It releases a veto:
    a comparison across a restatement is withheld because the two levels MAY be
    on different bases, and when the growth computed from them equals the
    published like-for-like, they are not.
    """
    bases = bases or {}
    published = published or {}
    vetoed = {i: why for i, why in (vetoed or {}).items() if i not in bases}
    lag = max(1, round(periods_per_year))   # 1 year back, in this series' own periods
    for i, r in enumerate(rows):
        r["net_margin"] = _margin(r.get("net"), r.get("revenue"))
        r["op_margin"] = _margin(r.get("operating"), r.get("revenue"))
        if i in vetoed:
            r["basis_break"] = vetoed[i]    # the chart marks the step, not just the table
        # A YEAR back, never the quarter before: Moutai sells a third of its
        # year in Q1, so Q2-on-Q1 is seasonality, not a decline.
        pj = _year_ago(rows, i, lag)
        prior = rows[pj] if pj is not None else None
        crossed = (next((vetoed[j] for j in range(pj + 1, i + 1) if j in vetoed), None)
                   if pj is not None else None)
        base = bases.get(i) if lag == 1 else None
        if base and prior is not None:
            # Flag it when the restated base is a different number from the one
            # charted for that year: the reader sees ¥1,110.6bn in the 2022 row
            # and needs to know 2023's growth was measured against ¥880.4bn.
            moved = {k: v for k, v in base.items()
                     if v is not None and prior.get(k) and abs(v / prior[k] - 1) > 0.01}
            if moved:
                r["base_restated"] = moved
        for key, dst in _SERIES:
            if prior is None:
                r[dst], r[dst + "_reason"] = None, None
            elif base and base.get(key) is not None:
                r[dst], r[dst + "_reason"] = pct_growth(r.get(key), base[key])
            elif crossed:
                own, _ = pct_growth(r.get(key), prior.get(key))
                pub = (published.get(i) or {}).get(key)
                if (own is not None and pub is not None
                        and abs(own - pub) <= max(_CONFIRM_PP, _CONFIRM_REL * abs(pub))):
                    r[dst], r[dst + "_reason"] = own, None
                    r.setdefault("yoy_confirmed", []).append(key)
                else:
                    r[dst], r[dst + "_reason"] = None, crossed
            else:
                r[dst], r[dst + "_reason"] = pct_growth(r.get(key), prior.get(key))
        if "revenue" in r.get("yoy_confirmed", ()):
            r.pop("basis_break", None)      # the bar is on the basis the company compares on
    # Compound growth joins the first charted level to the last, so it cannot
    # run across a year whose predecessor was restated: the two ends are on
    # different bases even though each single step now has a clean comparison.
    span_veto = dict(vetoed)
    span_veto.update({i: "restatement_mix" for i, r in enumerate(rows) if r.get("base_restated")})
    out: dict[str, Any] = {"rows": rows, "warnings": _warnings(rows)}
    for key, dst in _SERIES:
        out[key] = _trend(rows, key, dst, periods_per_year, span_veto)
    return out


_GAP_OF_REVENUE = 0.20        # net minus operating, as a share of revenue


def _warnings(rows: list[dict]) -> list[dict]:
    """Periods where the net line cannot be read as the business earning money.

    Yahoo has SK hynix at ₩93.8tn of net income on ₩79.3tn of revenue for Q2
    2026 — a 118% net margin, which is either an enormous non-operating item or
    a bad figure, and in neither case something to print beside a margin
    without comment. The softer version of the same thing is Nebius: +$584m of
    net income on a $111m operating LOSS, all of it a gain on a shareholding.
    """
    above_rev, above_op = [], []
    for r in rows:
        rev, net, op = r.get("revenue"), r.get("net"), r.get("operating")
        if rev and rev > 0 and net is not None:
            if net > rev:
                r.setdefault("flags", []).append("net_above_revenue")
                above_rev.append(r["label"])
            elif op is not None and (net - op) > _GAP_OF_REVENUE * rev:
                r.setdefault("flags", []).append("net_above_operating")
                above_op.append(r["label"])
    out = []
    if above_rev:
        out.append({"code": "net_above_revenue", "periods": above_rev,
                    "text": ("Net profit is larger than revenue in "
                             + ", ".join(above_rev) +
                             ". That cannot come from trading, so it is either a large "
                             "one-off gain or a vendor error — read the net line and the "
                             "net margin with care.")})
    if above_op:
        out.append({"code": "net_above_operating", "periods": above_op,
                    "text": ("Net profit sits well above operating profit in "
                             + ", ".join(above_op) +
                             ", so most of what reached the bottom line did not come from "
                             "operations. The gap between the two lines on the chart is it.")})
    return out


def _consecutive(rows: list[dict]) -> bool:
    """Four quarters that really do sit next to each other in time.

    Yahoo drops a quarter now and then — Berkshire has no Q3 2025 column — and
    the four rows left either side of the hole span fifteen months. Both feeds
    are checked the same way, on the period end dates themselves.
    """
    ends = [r.get("end") or "" for r in rows]
    if not all(len(e) == 10 for e in ends):
        return False
    for a, b in zip(ends, ends[1:]):
        try:
            gap = (date.fromisoformat(b) - date.fromisoformat(a)).days
        except ValueError:
            return False
        if not 70 <= gap <= 110:
            return False
    return True


def ttm(rows: list[dict]) -> dict[str, Any] | None:
    """The last four quarters added up — the only figure on this card that is
    not something the company filed, so it is built only from four consecutive
    quarters."""
    if len(rows) < _TTM_QUARTERS:
        return None
    window = rows[-_TTM_QUARTERS:]
    if not _consecutive(window):
        return None
    out: dict[str, Any] = {"label": f"TTM to {window[-1]['label']}", "quarters": _TTM_QUARTERS}
    for key in ("revenue", "operating", "net"):
        vals = [r.get(key) for r in window]
        out[key] = round(sum(vals), 2) if all(v is not None for v in vals) else None
    out["net_margin"] = _margin(out.get("net"), out.get("revenue"))
    return out


# ════════════════════════════════════════════════════════════════════════════
# US
# ════════════════════════════════════════════════════════════════════════════
def _row(df: Any, names: tuple[str, ...]) -> Any:
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def _us_frame(df: Any, labeler: Callable[[Any], str], limit: int) -> list[dict]:
    """Oldest→newest rows out of one yfinance statement frame."""
    rev = _row(df, _US_REVENUE)
    if rev is None:
        return []
    op, net = _row(df, _US_OPERATING), _row(df, _US_NET)
    rows = []
    for col in df.columns:
        r = _num(rev.get(col))
        if r is None:
            continue
        rows.append({
            "label": labeler(col),
            "end": col.date().isoformat(),
            "revenue": r,
            "operating": _num(op.get(col)) if op is not None else None,
            "net": _num(net.get(col)) if net is not None else None,
        })
    rows.sort(key=lambda x: x["end"])
    return rows[-limit:]


def us_series(fin: Any, qfin: Any, quarter_label: Callable[[Any], str],
              fy_end_month: int | None = None, is_financial: bool = False) -> dict[str, Any] | None:
    """Annual and quarterly levels from the (already FX-converted) statements."""
    annual = _us_frame(fin, lambda c: f"FY{c.year}", _MAX_ANNUAL)
    # `quarter_label` dates an ANNOUNCEMENT, not a period end. The quarter-by-
    # quarter growth table shifts statement columns forward ~45 days to reach
    # it; this card must shift by the same amount or the two tables would label
    # the same column differently on one page.
    quarterly = _us_frame(
        qfin, lambda c: quarter_label(c.to_pydatetime() + timedelta(days=45)), _MAX_QUARTERS)
    if not annual and not quarterly:
        return None
    out: dict[str, Any] = {
        "annual": _finish(annual, 1.0) if annual else None,
        "quarterly": _finish(quarterly, 4.0) if quarterly else None,
        "ttm": ttm(quarterly) if quarterly else None,
        "basis": {
            "revenue": "total revenue as reported on the income statement",
            "operating": ("operating income as the company reports it — after any restructuring, "
                          "impairment or similar charge it books above that line"),
            "net": "net income as reported, before preferred dividends",
            "periods": ("the company's own fiscal years and quarters, each labelled by the "
                        "year its period ends"),
        },
        "limits": [],
    }
    if is_financial:
        out["limits"].append(
            "For a bank or insurer, revenue here is the data vendor's construction and leaves out "
            "large one-off gains that the company counts in its own total net revenue. JPMorgan's "
            "2024 reads $169.4bn here against $177.6bn reported (the Visa share gain), and its "
            "Q2 2026 $52.9bn against $57.3bn — so revenue growth can differ materially from the "
            "figure in the company's release.")
    if fy_end_month and fy_end_month != 12:
        out["limits"].append(
            f"The fiscal year ends in month {fy_end_month}, so FY labels do not line up "
            f"with calendar years — each row's end date is shown.")
    if annual and len(annual) < 4:
        out["limits"].append(
            f"Only {len(annual)} annual period{'s' if len(annual) != 1 else ''} are available "
            f"from this feed.")
    if quarterly and len(quarterly) < 8:
        out["limits"].append(
            f"This feed carries {len(quarterly)} quarters, so the comparison with the same "
            f"quarter a year earlier exists for the most recent "
            f"{max(0, len(quarterly) - 4)} of them.")
    out["limits"].append("Figures are as currently restated by the vendor; "
                         "this feed exposes no filing vintages, so a restatement "
                         "overwrites history silently.")
    return out


# ════════════════════════════════════════════════════════════════════════════
# A-shares
# ════════════════════════════════════════════════════════════════════════════
def _cn_annual(income_rows: list[dict], limit: int) -> list[dict]:
    """Full years only, newest filing vintage of each."""
    best: dict[str, dict] = {}
    for r in income_rows or []:
        end = r.get("end_date") or ""
        if not end.endswith("1231"):
            continue
        key = (r.get("f_ann_date") or r.get("ann_date") or "", r.get("update_flag") or "")
        prev = best.get(end)
        if prev is None or key > prev["_k"]:
            best[end] = {"_k": key, "r": r}
    rows = []
    for end in sorted(best):
        r = best[end]["r"]
        rev = _num(r.get("total_revenue"))
        if rev is None:
            continue
        rows.append({"label": f"FY{end[:4]}", "end": f"{end[:4]}-12-31", "revenue": rev,
                     "operating": _num(r.get("operate_profit")),
                     "net": _cn_net(r),
                     "filed": r.get("f_ann_date") or r.get("ann_date") or "",
                     "period": end})
    return rows[-limit:]


def _basis_veto(rows: list[dict], restated: set[str], events: list[str]) -> dict[int, str]:
    """Which steps from one period to the next cross a restatement — used only
    where the company's own restated comparative is NOT available.

    The preferred answer to a restatement is `_comparative_bases`: every A-share
    report republishes the prior period on the current basis, and that is the
    like-for-like to measure against. This veto is the fallback for periods
    Tushare has no comparative for, which in practice means quarters: the
    de-cumulation already refuses a quarter built from two filing vintages,
    but the quarters either side of a restatement are each internally fine —
    it is the STEP between them that would compare old basis with new.

    CORRECTION, 2026-10-12. This function was first written on the belief that
    Wuliangye's FY2025 revenue (¥40.5bn against FY2024's ¥89.2bn) was a change
    of basis and "a collapse that never happened", and it withheld that year's
    growth. Checked against the company's own comparative and against
    Eastmoney, that was wrong: the FY2025 report restates FY2024 as the same
    ¥89.2bn, and the reported change is -54.55%. What was restated was the
    2025 INTERIM filings, downward. The annual comparison stands, and is now
    shown.
    """
    veto: dict[int, str] = {}
    for i, r in enumerate(rows):
        if not i:
            continue
        cf, pf = r.get("filed") or "", rows[i - 1].get("filed") or ""
        if r.get("period") in restated or rows[i - 1].get("period") in restated:
            veto[i] = "restatement_mix"
        elif cf and pf and any(pf < e <= cf for e in events):
            veto[i] = "restatement_mix"
    return veto


def _comparative_bases(annual: list[dict], comparatives: list[dict] | None) -> dict[int, dict[str, float]]:
    """For each year, the prior year AS RESTATED IN THAT YEAR'S OWN REPORT.

    Tushare serves these as `income` rows with report_type 4 (调整合并报表): the
    comparative column of the following year's statements. Two cases from the
    2026-10-12 check show why they are the right base:

      * Ping An adopted IFRS 17 in 2023. Its 2022 revenue is ¥1,110.6bn as
        first filed and ¥880.4bn as restated beside 2023's ¥913.8bn. Against
        the original, 2023 reads -17.7%; the company, and every data site,
        reports +3.8%.
      * Wuliangye's FY2025 report restates FY2024 as the unchanged ¥89.2bn —
        which is what establishes that its -54.55% is a real reported change
        and not an artefact (see the correction note on _basis_veto).

    The comparative used is the newest one published no later than the year's
    own filing — the one printed beside it.
    """
    out: dict[int, dict[str, float]] = {}
    by_end: dict[str, list[dict]] = {}
    for r in comparatives or []:
        if (r.get("end_date") or "").endswith("1231"):
            by_end.setdefault(r["end_date"], []).append(r)
    for i, row in enumerate(annual):
        if not i:
            continue
        prior_end, filed = annual[i - 1].get("period"), row.get("filed") or ""
        cands = [c for c in by_end.get(prior_end, [])
                 if (c.get("f_ann_date") or c.get("ann_date") or "") <= filed] if filed else []
        if not cands:
            continue
        c = max(cands, key=lambda x: (x.get("f_ann_date") or x.get("ann_date") or "", x.get("update_flag") or ""))
        base = {"revenue": _num(c.get("total_revenue")), "operating": _num(c.get("operate_profit")),
                "net": _cn_net(c)}
        if base["revenue"] is not None:
            out[i] = base
    return out


def _cn_net(r: dict) -> float | None:
    """归母净利润 — net profit attributable to the listed company's shareholders.

    It is the figure every report, data site, P/E and EPS is built on, and it
    is NOT `n_income`, which still includes the minorities' share. The two
    differ by 3-9% for most large caps and far more where a subsidiary earns
    the money: CXMT's FY2025 is ¥7.14bn including minorities and ¥1.87bn to
    shareholders. Checked against Eastmoney on 2026-10-12.
    """
    v = _num(r.get("n_income_attr_p"))
    return v if v is not None else _num(r.get("n_income"))


_PUBLISHED_FIELDS = {"revenue": "q_sales_yoy", "operating": "q_op_yoy", "net": "q_netprofit_yoy"}


def _published_growth(indicator_rows: list[dict] | None, net_is_attributable: bool) -> dict[str, dict[str, float]]:
    """{period: single-quarter growth as Tushare's fina_indicator publishes it}.

    Of several rows for one period the updated one (update_flag 1) of the
    newest announcement wins: Wuliangye's H1 2025 is there twice, +0.1% on the
    figures first filed and -59.4% on the restated ones. `q_netprofit_yoy` is
    on profit attributable to shareholders, so it only vouches for that line.
    """
    best: dict[str, dict] = {}
    for r in indicator_rows or []:
        p = r.get("end_date") or ""
        k = (r.get("ann_date") or "", r.get("update_flag") or "")
        if p and (p not in best or k > best[p]["_k"]):
            best[p] = {"_k": k, "r": r}
    out: dict[str, dict[str, float]] = {}
    for p, b in best.items():
        got = {key: _num(b["r"].get(f)) for key, f in _PUBLISHED_FIELDS.items()
               if key != "net" or net_is_attributable}
        got = {k: v for k, v in got.items() if v is not None}
        if got:
            out[p] = got
    return out


def cn_series(income_rows: list[dict], comparatives: list[dict] | None = None,
              single_rows: list[dict] | None = None,
              indicator_rows: list[dict] | None = None) -> dict[str, Any] | None:
    annual = _cn_annual(income_rows, _MAX_ANNUAL)
    bases = _comparative_bases(annual, comparatives)
    net_field = ("n_income_attr_p" if any(_num(r.get("n_income_attr_p")) is not None for r in income_rows or [])
                 else "n_income")
    d = cn_discrete(income_rows, ("total_revenue", "operate_profit", net_field), single_rows)
    restated, events = set(d["restated_periods"]), d["events"]
    veto = _basis_veto(annual, restated, events)
    v = d["values"]
    quarterly = []
    for lab in reversed(d["order"]):
        if lab in d["withheld"] or lab not in v["total_revenue"]:
            continue
        quarterly.append({"label": lab, "end": _cn_end(lab), "revenue": v["total_revenue"][lab],
                          "operating": v["operate_profit"].get(lab),
                          "net": v[net_field].get(lab),
                          "filed": d["filed"].get(lab, ""),
                          "period": _cn_period(lab)})
        if lab in d["filled"]:
            quarterly[-1]["restated_level"] = d["filled"][lab]
    quarterly = quarterly[-_MAX_QUARTERS:]
    if not annual and not quarterly:
        return None
    q_veto = _basis_veto(quarterly, restated, events)
    pub = _published_growth(indicator_rows, net_field == "n_income_attr_p")
    q_pub = {i: pub[r["period"]] for i, r in enumerate(quarterly) if r["period"] in pub}
    out: dict[str, Any] = {
        "annual": _finish(annual, 1.0, veto, bases) if annual else None,
        "quarterly": _finish(quarterly, 4.0, q_veto, published=q_pub) if quarterly else None,
        "ttm": ttm(quarterly) if quarterly else None,
        "basis": {
            "revenue": "营业总收入 total revenue",
            "operating": "营业利润 operating profit",
            "net": ("归母净利润 — net profit attributable to shareholders, the figure the company "
                    "headlines and P/E is built on (not net profit including minority interests)"
                    if net_field == "n_income_attr_p" else "净利润 net profit, including minority interests"),
            "periods": "calendar quarters and years (A-share fiscal year = calendar year)",
        },
        "limits": [
            "Filings are year-to-date; single quarters are derived by subtraction (Q2 = H1 − Q1).",
        ],
    }
    if net_field == "n_income_attr_p":
        # The conventional 销售净利率 (and the peer table's net margin) is on net
        # profit INCLUDING minorities. This card's margin is on the profit it
        # actually charts, and has to be named differently or the page shows
        # two different "net margins".
        out["net_margin_label"] = "Margin to shareholders"
        out["limits"].append(
            "Margin here is profit attributable to shareholders ÷ revenue. The peer table's net "
            "margin follows the usual 销售净利率 convention — net profit including minority "
            "interests ÷ revenue — so it reads slightly higher.")
    if d["withheld"]:
        out["withheld"] = d["withheld"]
        out["limits"].append(
            f"{len(d['withheld'])} quarter(s) withheld because the year-to-date inputs "
            f"come from different filing vintages or disagree.")
    if d["restated_periods"]:
        out["restated_periods"] = d["restated_periods"]
    crossed = ([annual[i]["label"] for i in sorted(veto) if i not in bases]
               + [r["label"] for r in quarterly if r.get("basis_break")])
    filled = [r["label"] for r in quarterly if r["label"] in d["filled"]]
    if filled:
        out["filled"] = {lab: d["filled"][lab] for lab in filled}
        out["limits"].append(
            ", ".join(filled) + ": taken from the company's restated single-quarter statement, "
            "because the year-to-date filings either side are of different vintages "
            "(restated Q1 = restated half-year − restated Q2).")
    confirmed = [r["label"] for r in quarterly if r.get("yoy_confirmed")]
    if confirmed:
        out["limits"].append(
            "Growth for " + ", ".join(confirmed) + " spans a restatement. It is shown because the "
            "figure computed from the two quarters charted here equals the like-for-like "
            "single-quarter growth Tushare publishes for the company.")
    rebased = [r["label"] for r in annual if r.get("base_restated")]
    if rebased:
        out["limits"].append(
            "Growth for " + ", ".join(rebased) + " is measured against the prior year as the "
            "company restated it in that year's report, not against the figure charted for the "
            "prior year, which is the one originally filed.")
    if crossed:
        out["limits"].append(
            "Change is withheld at " + ", ".join(crossed)
            + ": the company restated its figures between those filings, so the periods "
              "either side are not on the same accounting basis.")
    return out


_CN_QEND = {"1": "-03-31", "2": "-06-30", "3": "-09-30", "4": "-12-31"}


def _cn_period(label: str) -> str:
    """"Q2 2024" -> "20240630", the key `cn_discrete` reports restatements under."""
    return _cn_end(label).replace("-", "")


def _cn_end(label: str) -> str:
    """"Q2 2024" -> "2024-06-30". A withheld quarter leaves a hole in the
    sequence, and the gap between real dates is what `_consecutive` reads."""
    try:
        q, y = label.split(" ")
        return f"{int(y)}{_CN_QEND[q[1]]}"
    except (ValueError, KeyError, IndexError):
        return ""

"""Same-quarter year-over-year growth, quarter by quarter.

Feeds Gate 1 ("EPS Growth", "Revenue Growth") and the quarter-by-quarter table
beneath it, for both markets. One rule everywhere: a quarter is compared with
the SAME fiscal quarter a year earlier, never with the trailing year and never
with a year-to-date figure.

WHY THIS MODULE EXISTS
----------------------
Both Gate 1 revenue-growth labels were wrong before it:
  * US said "TTM YoY". Yahoo's `revenueGrowth` is actually the latest quarter
    vs the same quarter a year earlier (it reproduces exactly: AVGO 29.59bn vs
    15.95bn = +85.5%).
  * A-shares said "Most recent quarter YoY (or_yoy)". Tushare's `or_yoy` is
    YEAR-TO-DATE: an H1 filing compares Jan-Jun with Jan-Jun, not Q2 with Q2.

RESTATEMENTS — the trap this is built around
-------------------------------------------
Wuliangye (000858.SZ) restated 2025 in April 2026. H1 2025 revenue is ¥52.77bn
as first filed and ¥23.51bn as restated; EPS ¥5.02 vs ¥1.19. 2024 was not
restated. Consequences:

  1. A growth rate that straddles the restatement compares two accounting
     bases. H1 2026 EPS is +89% against the restated 2025 base and about -55%
     against the original. Neither is "the" number, so any YoY touching a
     restated period is FLAGGED, never presented as clean.

  2. Tushare's tables disagree on vintage: `fina_indicator` carries the
     restated Q1 2025, `income` only the original. De-cumulating
     H1(restated) - Q1(original) gives NEGATIVE quarterly revenue. A discrete
     quarter is therefore only computed when both cumulative inputs come from
     the same filing, or neither was restated. Otherwise it is withheld.

Yahoo exposes no filing vintages, so US restatements overwrite history
silently and cannot be detected here. That limitation is stated, not hidden.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta
from typing import Any

_RESTATE_TOL = 0.05       # vintages differing by >5% count as a real restatement
_N_ROWS = 8


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def pct_growth(cur: float | None, prior: float | None) -> tuple[float | None, str | None]:
    """Growth in %, refusing a zero or negative base (a loss has no meaningful
    percentage change)."""
    if cur is None or prior is None:
        return None, None
    if prior <= 0:
        return None, "negative_base"
    if cur < 0:
        # Profit to loss. Intel went +0.02 -> -0.10, which prints as "-600%":
        # arithmetically valid, analytically meaningless.
        return None, "turned_to_loss"
    return round((cur / prior - 1) * 100, 1), None


_LABEL = re.compile(r"^Q([1-4]) (\d{4})$")


def label_key(label: str | None) -> tuple[int, int] | None:
    m = _LABEL.match(label or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def yoy_rows(eps: dict[str, float], rev: dict[str, float], order: list[str],
             restated: set[str] | None = None,
             withheld: dict[str, str] | None = None,
             filed: dict[str, str] | None = None,
             events: list[str] | None = None) -> list[dict]:
    """Quarter rows, newest first, each against the same quarter a year earlier.

    `order`     fiscal labels newest first ("Q3 2026").
    `restated`  labels whose figures exist in materially different vintages.
    `withheld`  labels whose discrete value could not be derived, with a reason.
    """
    restated = restated or set()
    withheld = withheld or {}
    rows: list[dict] = []
    for lab in order[:_N_ROWS]:
        k = label_key(lab)
        prior_lab = f"Q{k[0]} {k[1] - 1}" if k else None
        r: dict[str, Any] = {"label": lab, "prior_label": prior_lab,
                             "eps": eps.get(lab), "eps_prior": eps.get(prior_lab) if prior_lab else None,
                             "rev": rev.get(lab), "rev_prior": rev.get(prior_lab) if prior_lab else None}
        r["eps_yoy"], r["eps_yoy_reason"] = pct_growth(r["eps"], r["eps_prior"])
        r["rev_yoy"], r["rev_yoy_reason"] = pct_growth(r["rev"], r["rev_prior"])
        for side, key in (("eps", lab), ("eps_prior", prior_lab)):
            if key in withheld and r.get(side) is None:
                why = withheld[key]
                r["eps_yoy_reason"] = r["eps_yoy_reason"] or why
                r["rev_yoy_reason"] = r["rev_yoy_reason"] or why
        # A comparison is compromised if it STRADDLES a restatement: the current
        # figure was filed on the new basis and the year-ago figure on the old.
        # Checking only "was this period filed twice?" misses quarters born on
        # the restated basis (Wuliangye's FY2025 was filed once, restated from
        # birth, and compared against an unrestated 2024).
        straddles = False
        if filed and events and prior_lab:
            cf, pf = filed.get(lab), filed.get(prior_lab)
            straddles = bool(cf and pf and any(pf < e <= cf for e in events))
        for f, cur_k, prv_k in (("eps_yoy", "eps", "eps_prior"), ("rev_yoy", "rev", "rev_prior")):
            if r[f] is None and r[f + "_reason"] is None and r[cur_k] is not None and r[prv_k] is None:
                r[f + "_reason"] = "no_prior_period"
        if lab in restated or (prior_lab and prior_lab in restated) or straddles:
            r["restated"] = True
        rows.append(r)

    # Acceleration: is this quarter's YoY above or below the previous quarter's?
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        for f in ("eps_yoy", "rev_yoy"):
            a, b = r.get(f), (nxt or {}).get(f)
            if a is None or b is None or r.get("restated") or (nxt or {}).get("restated"):
                r[f + "_trend"] = None          # an arrow across two accounting bases means nothing
            else:
                r[f + "_trend"] = "up" if a > b + 0.05 else "down" if a < b - 0.05 else "flat"
    return rows


def summarize(rows: list[dict], basis: dict[str, str], limits: list[str]) -> dict[str, Any]:
    latest = rows[0] if rows else {}
    return {"rows": rows, "basis": basis, "limits": limits,
            "latest_label": latest.get("label"),
            "latest_eps_yoy": latest.get("eps_yoy"),
            "latest_eps_yoy_reason": latest.get("eps_yoy_reason"),
            "latest_rev_yoy": latest.get("rev_yoy"),
            "latest_rev_yoy_reason": latest.get("rev_yoy_reason"),
            "latest_restated": bool(latest.get("restated"))}


# ── US ──────────────────────────────────────────────────────────────────────
def us_quarterly(t: Any, fy_end_month: int | None, labeler: Any) -> dict[str, Any]:
    """EPS: Yahoo earnings dates (adjusted EPS, the figure analysts forecast),
    ~24 quarters deep. Revenue: quarterly income statement, only 5-7 quarters,
    so revenue YoY exists for the latest one to three quarters only."""
    eps: dict[str, float] = {}
    order: list[str] = []
    suspect: set[str] = set()
    try:
        ed = t.get_earnings_dates(limit=28)
        rep = ed[ed["Reported EPS"].notna()] if ed is not None and not ed.empty else None
        if rep is not None:
            for dt, row in rep.iterrows():               # newest first
                lab = labeler(dt, fy_end_month)
                if lab in eps:
                    continue                             # duplicate announcement for one quarter
                v = _num(row.get("Reported EPS"))
                if v is None:
                    continue
                order.append(lab)
                est = _num(row.get("EPS Estimate"))
                if est is not None and abs(est) >= 0.05 and v / est > 0 and not 0.25 <= v / est <= 4.0:
                    suspect.add(lab)                     # unit mismatch, not a surprise
                    continue
                eps[lab] = v
    except Exception:                                    # noqa: BLE001
        pass

    rev: dict[str, float] = {}
    try:
        qi = t.quarterly_income_stmt
        if qi is not None and not qi.empty:
            row = next((qi.loc[n] for n in ("Total Revenue", "Operating Revenue") if n in qi.index), None)
            if row is not None:
                for col, v in row.items():
                    fv = _num(v)
                    if fv is None:
                        continue
                    # A period END is not an announcement; shift ~45 days so the
                    # announcement-based fiscal labeler lands on the right quarter.
                    rev[labeler(col.to_pydatetime() + timedelta(days=45), fy_end_month)] = fv
    except Exception:                                    # noqa: BLE001
        pass

    withheld = {lab: "units_suspect" for lab in suspect}
    rows = yoy_rows(eps, rev, order, withheld=withheld)
    return summarize(rows, {
        "eps": "adjusted (non-GAAP) EPS as reported against consensus, each quarter vs the same fiscal quarter a year earlier",
        "revenue": "total revenue from the quarterly income statement, same-quarter comparison",
        "quarters": "the company's own fiscal quarters",
    }, [
        "Revenue history on this feed is only 5–7 quarters, so revenue YoY is available for the latest one to three quarters.",
        "Yahoo does not expose filing vintages: a restatement silently overwrites history and cannot be flagged.",
    ])


# ── A-shares ────────────────────────────────────────────────────────────────
def _cn_label(period: str) -> str:
    return f"Q{ {'0331': 1, '0630': 2, '0930': 3, '1231': 4}[period[4:]] } {period[:4]}".replace("{ ", "").replace(" }", "")


def _cn_field(r: dict, name: str) -> float | None:
    """`eps` means diluted, falling back to basic — Tushare leaves one or the
    other empty depending on the filer."""
    if name == "eps":
        v = _num(r.get("diluted_eps"))
        return v if v is not None else _num(r.get("basic_eps"))
    return _num(r.get(name))


def cn_discrete(income_rows: list[dict], fields: tuple[str, ...] = ("total_revenue", "eps"),
                single_rows: list[dict] | None = None) -> dict[str, Any]:
    """Single quarters out of Tushare's year-to-date `income` filings.

    Every A-share caller needs this and all of them need the same safeguards,
    so the de-cumulation lives here once: pick one filing vintage per period,
    subtract the previous cumulative figure, and withhold the quarter outright
    when the two sides cannot be compared. Revenue is always computed, because
    a negative discrete revenue is the signal that the inputs disagree — and
    when it fires, no field of that quarter is trusted.

    `single_rows` are the company's SINGLE-QUARTER statements (Tushare `income`
    report_type 2). They are used for one thing only: a quarter the subtraction
    has to refuse because of a restatement. Wuliangye restated its 2025 interim
    filings in April 2026, and Tushare holds the restated half-year (¥23.5bn)
    but only the superseded first quarter (¥36.9bn), so H1 − Q1 is meaningless
    and both quarters used to be withheld. The restated single-quarter
    statement gives Q2 2025 directly (¥6.4bn), and restated H1 − restated Q2
    gives Q1 (¥17.1bn) — both checked 2026-10-12 against the single-quarter
    growth the company's later reports imply (+33.7% for Q1 2026, -13.2% for
    Q2 2026). A statement is only used when it is of the same filing date as
    the cumulative figure it sits beside.

    Returns {values: {field: {label: value}}, withheld, filed, filled, order,
    restated_periods, restated_labels, events}.
    """
    by_period: dict[str, list[dict]] = {}
    for r in income_rows or []:
        p = r.get("end_date") or ""
        if len(p) == 8 and p[4:] in ("0331", "0630", "0930", "1231"):
            by_period.setdefault(p, []).append(r)

    chosen: dict[str, dict] = {}
    restated_periods: set[str] = set()
    for p, vs in by_period.items():
        # Latest filing wins; among same-day duplicates prefer update_flag '1'.
        vs = sorted(vs, key=lambda x: (x.get("f_ann_date") or x.get("ann_date") or "", x.get("update_flag") or ""))
        chosen[p] = vs[-1]
        revs = {round(_num(x.get("total_revenue")) or 0, -3) for x in vs if _num(x.get("total_revenue"))}
        if len(revs) > 1:
            lo, hi = min(revs), max(revs)
            if hi and (hi - lo) / hi > _RESTATE_TOL:
                restated_periods.add(p)

    # Restatement EVENTS: the filing dates of the newer vintage in each restated
    # period. Any comparison whose two sides were filed either side of one of
    # these dates mixes accounting bases.
    events: list[str] = []
    restated_event: dict[str, str] = {}
    for p in restated_periods:
        vs = sorted(by_period[p], key=lambda x: x.get("f_ann_date") or x.get("ann_date") or "")
        e = vs[-1].get("f_ann_date") or vs[-1].get("ann_date")
        if e:
            events.append(e)
            restated_event[p] = e
    events = sorted(set(events))

    def filed_of(r: dict) -> str:
        return r.get("f_ann_date") or r.get("ann_date") or ""

    wanted = tuple(dict.fromkeys(("total_revenue",) + tuple(fields)))
    prev_period = {"0630": "0331", "0930": "0630", "1231": "0930"}
    values: dict[str, dict[str, float]] = {f: {} for f in wanted}
    withheld: dict[str, str] = {}
    filed: dict[str, str] = {}
    filled: dict[str, str] = {}

    singles: dict[str, dict] = {}
    for r in single_rows or []:
        p = r.get("end_date") or ""
        if len(p) == 8 and p[4:] in ("0331", "0630", "0930", "1231"):
            prev = singles.get(p)
            if prev is None or (filed_of(r), r.get("update_flag") or "") > (filed_of(prev), prev.get("update_flag") or ""):
                singles[p] = r

    def single_of(p: str, cum: dict) -> dict[str, float | None] | None:
        """Quarter `p` from its own statement — only the one filed on the same
        day as the cumulative figure `cum`, so the two are one vintage."""
        s = singles.get(p)
        if s is None or not filed_of(cum) or filed_of(s) != filed_of(cum):
            return None
        out: dict[str, float | None] = {}
        for f in wanted:
            v = _cn_field(s, f)
            if v is None and f == "eps":
                # Single-quarter statements carry no EPS. The cumulative filing
                # of the same vintage fixes the share count (profit ÷ EPS).
                np_s, np_c, eps_c = _num(s.get("n_income_attr_p")), _num(cum.get("n_income_attr_p")), _cn_field(cum, "eps")
                if np_s is not None and np_c and eps_c and eps_c / np_c > 0:
                    v = np_s * eps_c / np_c
            out[f] = v
        return out if out.get("total_revenue") is not None else None
    for p in sorted(chosen):
        cur = chosen[p]
        lab = _cn_label(p)
        if p[4:] == "0331":
            # A first quarter is filed once and rarely refiled. When the company
            # later restates the half-year or nine-month figures of the SAME
            # year, the Q1 number on file is the superseded one — Wuliangye's
            # Q1 2025 is ¥36.9bn as filed and ¥17.1bn on the restated basis its
            # later 2025 periods use, and only the first is in this table.
            # Shown beside restated quarters it would be the odd one out.
            later = [e for rp_, e in restated_event.items() if rp_[:4] == p[:4] and rp_ > p]
            if later and filed_of(cur) < max(later):
                # Restated Q1 = restated half-year − restated Q2, when the
                # half-year on file is itself the restated one.
                h1 = chosen.get(p[:4] + "0630")
                q2 = single_of(p[:4] + "0630", h1) if h1 is not None and filed_of(h1) >= max(later) else None
                discrete = {}
                if q2 is not None:
                    for f in wanted:
                        c = _cn_field(h1, f)
                        discrete[f] = (c - q2[f]) if (c is not None and q2.get(f) is not None) else None
                if not discrete or discrete["total_revenue"] is None or discrete["total_revenue"] < 0:
                    withheld[lab] = "superseded_by_restatement"
                    continue
                filed[lab] = filed_of(h1)
                filled[lab] = "restated_half_year_less_q2"
            else:
                discrete = {f: _cn_field(cur, f) for f in wanted}
                filed[lab] = filed_of(cur)
        else:
            pp = p[:4] + prev_period[p[4:]]
            prv = chosen.get(pp)
            if prv is None:
                withheld[lab] = "missing_prior_filing"
                continue
            same_vintage = (cur.get("f_ann_date") or "") == (prv.get("f_ann_date") or "")
            if (p in restated_periods or pp in restated_periods) and not same_vintage:
                # H1(restated) - Q1(original) is meaningless; the quarter's own
                # restated statement is not.
                discrete = single_of(p, cur)
                if discrete is None:
                    withheld[lab] = "restatement_mix"
                    continue
                filed[lab] = filed_of(cur)
                filled[lab] = "single_quarter_statement"
            else:
                discrete = {}
                for f in wanted:
                    c, b = _cn_field(cur, f), _cn_field(prv, f)
                    discrete[f] = (c - b) if (c is not None and b is not None) else None
                if discrete["total_revenue"] is not None and discrete["total_revenue"] < 0:
                    withheld[lab] = "negative_discrete_revenue"  # impossible; inputs inconsistent
                    continue
                filed[lab] = max(filed_of(cur), filed_of(prv))
        for f in wanted:
            if discrete.get(f) is not None:
                values[f][lab] = discrete[f]

    return {"values": values, "withheld": withheld, "filed": filed, "filled": filled,
            "order": [_cn_label(p) for p in sorted(chosen, reverse=True)],
            "restated_periods": sorted(restated_periods),
            "restated_labels": {_cn_label(p) for p in restated_periods},
            "events": events}


def cn_quarterly(income_rows: list[dict], single_rows: list[dict] | None = None) -> dict[str, Any]:
    """From Tushare `income` rows (cumulative YTD, possibly several vintages per
    period): discrete quarters, restatement detection, same-quarter YoY.

    Needs fields: end_date, f_ann_date, update_flag, total_revenue, diluted_eps, basic_eps.
    """
    d = cn_discrete(income_rows, ("total_revenue", "eps"), single_rows)
    rev = d["values"]["total_revenue"]
    eps = {k: round(v, 4) for k, v in d["values"]["eps"].items()}
    rows = yoy_rows(eps, rev, d["order"], d["restated_labels"], d["withheld"],
                    d["filed"], d["events"])
    out = summarize(rows, {
        "eps": "diluted EPS, de-cumulated from year-to-date filings into single quarters, vs the same quarter a year earlier",
        "revenue": "total revenue, de-cumulated the same way, same-quarter comparison",
        "quarters": "calendar quarters (A-share fiscal year = calendar year)",
    }, [
        "A-share filings are year-to-date; single quarters are derived by subtraction (Q2 = H1 − Q1).",
        "A quarter is withheld when its year-to-date inputs come from different filing vintages across a restatement.",
    ])
    restated_periods = set(d["restated_periods"])
    events = d["events"]
    out["restated_periods"] = sorted(restated_periods)
    out["restatement_events"] = events
    if d["filled"]:
        out["filled"] = d["filled"]
        out["limits"].append(
            ", ".join(sorted(d["filled"], key=label_key)) + ": taken from the company's restated "
            "single-quarter statement, because the year-to-date filings either side are of "
            "different vintages (restated Q1 = restated half-year − restated Q2).")
    return out

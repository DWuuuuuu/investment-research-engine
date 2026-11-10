"""Forecasts for one A-share: what the brokers expect, and what the company
has said itself. Feeds the "Forecasts" card on every A-share stock page.

TWO SOURCES THAT MUST NOT BE BLURRED
------------------------------------
  * BROKER FORECASTS come from Tushare's `report_rc` — one row per broker per
    forecast year, lifted from the forecast table of a research report. An
    outside opinion.
  * COMPANY GUIDANCE comes from `forecast` (业绩预告) — the issuer's own
    pre-announcement of a period that has ended but not yet been reported.
    Not an opinion: a number the company will be held to.
They are drawn as two separate blocks and never averaged together.

WHY THIS READS THE SHARED CACHE INSTEAD OF CALLING THE VENDOR
-------------------------------------------------------------
`report_rc` is held to ten requests an hour on this account, and a refused
request still counts. A call per stock page would spend that allowance in ten
clicks and starve the market-wide backfill the research portal depends on. So
broker forecasts are read out of the cache `cn_reports` already maintains,
which costs nothing. The price is that the answer is only as deep as that
cache: until its 180 days are loaded, a stock can look uncovered when its
reports simply have not been fetched yet — so the card always says how far
back it could see.

WHAT IS AND IS NOT TRUSTED IN A ROW
-----------------------------------
  * `np`, `op_rt`, `eps` — used. `np` and `op_rt` are in 万元.
  * `pe` — NOT used. It is each broker's multiple at its own price on its own
    day and has been seen flatly wrong (Nomura's CXMT rows show 1.5-4.2x
    against its own EPS). Multiples here are recomputed from consensus net
    profit and today's market cap.
  * `rating` — each broker's own scale, bucketed by cn_reports.
  * `max_price`/`min_price` — used where present, which is rarely: most
    reports carry no target at all, and the card says how many did.

CONSENSUS MEANS AT LEAST TWO BROKERS. One analyst's number is that analyst's
number. And a broker counts once per year, by its most recent report — three
updates from the same desk are one view, not three.

COVERAGE IS DOMESTIC. The feed is almost entirely mainland brokers. Goldman,
Morgan Stanley, UBS and the like are largely absent, so this is the onshore
consensus, not the whole street.
"""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

from .quarterly_growth import pct_growth

_WAN = 10_000.0                    # report_rc and forecast both quote 万元
_FY = re.compile(r"^(\d{4})Q4$")   # "2027Q4" = full-year 2027; other quarters are interim and skipped
_UNIT_BAND = 10.0                  # a figure this many times off the median is a unit error, not a view
_REVISION_TOL = 0.01               # a change under 1% is a re-typed number, not a revision
_MAX_YEARS = 3
_GUIDANCE_TTL = 6 * 3600

_GUIDANCE_TYPE = {
    "预增": "sharp increase", "略增": "modest increase", "续盈": "still profitable",
    "扭亏": "turning profitable", "预减": "sharp decrease", "略减": "modest decrease",
    "首亏": "first loss", "续亏": "continued loss", "减亏": "smaller loss",
    "增亏": "wider loss", "不确定": "uncertain",
}
_PERIOD = {"0331": "Q1", "0630": "H1", "0930": "9M", "1231": "FY"}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _median(xs: list[float]) -> float | None:
    s = sorted(xs)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _consensus(values: dict[str, float]) -> dict[str, Any]:
    """Median across brokers, with figures that can only be unit errors set
    aside. With two brokers a 10x disagreement cannot be adjudicated — neither
    is published rather than averaging a typo into the answer."""
    out: dict[str, Any] = {"n": len(values), "median": None, "low": None, "high": None,
                           "excluded": [], "state": "none"}
    if not values:
        return out
    if len(values) == 1:
        (v,) = values.values()
        out.update({"median": v, "low": v, "high": v, "state": "single"})
        return out
    med = _median(list(values.values()))
    keep = {}
    for org, v in values.items():
        ratio = (abs(v) / abs(med)) if med else 1.0
        if med and v * med > 0 and not (1 / _UNIT_BAND <= ratio <= _UNIT_BAND):
            out["excluded"].append(org)
        else:
            keep[org] = v
    if len(keep) < 2:
        out["state"] = "inconsistent"
        return out
    vals = list(keep.values())
    out.update({"n": len(keep), "median": _median(vals), "low": min(vals), "high": max(vals),
                "state": "consensus"})
    return out


def broker_forecasts(ts_code: str, rows: list[dict], *, market_cap: float | None,
                     price: float | None, actual: dict[str, Any] | None,
                     coverage: dict[str, Any] | None = None,
                     bucket: Any = None) -> dict[str, Any]:
    """Consensus by fiscal year from the cached report_rc rows of one stock.

    `actual` is the last reported full year — {"year", "revenue", "net_parent"}
    in yuan — the base the first forecast year is compared with.
    """
    cov = coverage or {}
    out: dict[str, Any] = {
        "status": "empty", "years": [], "brokers": [], "ratings": None, "targets": None,
        "revisions": None, "n_brokers": 0, "n_reports": 0,
        "coverage": {"covered_from": cov.get("covered_from"), "complete": bool(cov.get("complete")),
                     "covered_days": cov.get("covered_days"), "horizon_days": cov.get("horizon_days"),
                     "cooldown_remaining_min": cov.get("cooldown_remaining_min")},
    }
    mine = [r for r in rows or [] if r.get("ts_code") == ts_code
            and r.get("report_type") != "非个股" and r.get("org_name")]
    if not mine:
        return out

    last_actual_year = (actual or {}).get("year")
    floor = (last_actual_year + 1) if last_actual_year else date.today().year

    # One view per broker per year: its most recent report.
    latest: dict[tuple[str, int], dict] = {}        # (org, year) -> row
    history: dict[tuple[str, int], dict[str, float]] = {}   # (org, year) -> {report_date: np}
    newest_report: dict[str, dict] = {}             # org -> newest row of any year
    for r in mine:
        org, day = r["org_name"], r.get("report_date") or ""
        if org not in newest_report or day > (newest_report[org].get("report_date") or ""):
            newest_report[org] = r
        m = _FY.match(r.get("quarter") or "")
        if not m:
            continue
        year = int(m.group(1))
        if year < floor:
            continue                                 # a closed year is an actual, not a forecast
        key = (org, year)
        if key not in latest or day > (latest[key].get("report_date") or ""):
            latest[key] = r
        np_ = _num(r.get("np"))
        if np_ is not None:
            history.setdefault(key, {})[day] = np_ * _WAN

    years = sorted({y for _, y in latest})[:_MAX_YEARS]
    if not years:
        return out

    prev_np = (actual or {}).get("net_parent")
    prev_rev = (actual or {}).get("revenue")
    prev_label = f"FY{last_actual_year} actual" if last_actual_year else None
    for y in years:
        picks = {org: r for (org, yy), r in latest.items() if yy == y}
        rev = _consensus({o: v * _WAN for o, r in picks.items() if (v := _num(r.get("op_rt"))) is not None})
        npc = _consensus({o: v * _WAN for o, r in picks.items() if (v := _num(r.get("np"))) is not None})
        eps = _consensus({o: v for o, r in picks.items() if (v := _num(r.get("eps"))) is not None})
        row: dict[str, Any] = {"year": y, "label": f"FY{y}E", "n_brokers": len(picks),
                               "revenue": rev, "net_profit": npc, "eps": eps,
                               "vs": prev_label}
        usable = npc["state"] in ("consensus", "single")
        row["np_growth"], row["np_growth_reason"] = (
            pct_growth(npc["median"], prev_np) if usable else (None, None))
        row["rev_growth"], row["rev_growth_reason"] = (
            pct_growth(rev["median"], prev_rev) if rev["state"] in ("consensus", "single") else (None, None))
        # Recomputed, never the vendor's `pe`: today's market cap over this year's profit.
        row["pe"] = (round(market_cap / npc["median"], 1)
                     if (usable and market_cap and npc["median"] and npc["median"] > 0) else None)
        row["pe_reason"] = ("loss_forecast" if (usable and npc["median"] is not None and npc["median"] <= 0)
                            else None)
        out["years"].append(row)
        prev_np = npc["median"] if usable else None
        prev_rev = rev["median"] if rev["state"] in ("consensus", "single") else None
        prev_label = f"FY{y}E"

    orgs = sorted({org for org, _ in latest})
    out["n_brokers"] = len(orgs)
    out["n_reports"] = len({(r.get("report_date"), r.get("org_name"), r.get("report_title")) for r in mine})
    days = sorted(r.get("report_date") or "" for r in mine if r.get("report_date"))
    out["from"], out["to"] = (days[0], days[-1]) if days else (None, None)

    # Ratings: each broker's newest report, whatever years it forecast.
    if bucket is not None:
        mix = {"positive": 0, "neutral": 0, "negative": 0, "unrated": 0, "other": 0}
        for org in orgs:
            mix[bucket(newest_report[org].get("rating"))] += 1
        out["ratings"] = mix

    # Targets: rare. Report how many brokers gave one, not just the median.
    targets = []
    for org in orgs:
        r = newest_report[org]
        lo, hi = _num(r.get("min_price")), _num(r.get("max_price"))
        vals = [v for v in (lo, hi) if v and v > 0]
        if vals:
            targets.append({"org": org, "low": min(vals), "high": max(vals),
                            "mid": sum(vals) / len(vals), "date": r.get("report_date")})
    if targets:
        mid = _median([t["mid"] for t in targets])
        out["targets"] = {
            "n": len(targets), "of": len(orgs), "median": round(mid, 2),
            "low": min(t["low"] for t in targets), "high": max(t["high"] for t in targets),
            "upside_pct": round((mid / price - 1) * 100, 1) if price else None,
            "items": sorted(targets, key=lambda t: t["date"] or "", reverse=True),
        }
    else:
        out["targets"] = {"n": 0, "of": len(orgs)}

    # Revisions to the first forecast year: each broker's latest figure against
    # its own previous one. The direction estimates are moving in says more
    # than their level does.
    y1 = years[0]
    up = down = flat = 0
    for org in orgs:
        h = history.get((org, y1)) or {}
        if len(h) < 2:
            continue
        d = sorted(h)
        new, old = h[d[-1]], h[d[-2]]
        if not old:
            continue
        chg = (new - old) / abs(old)
        if chg > _REVISION_TOL:
            up += 1
        elif chg < -_REVISION_TOL:
            down += 1
        else:
            flat += 1
    out["revisions"] = {"year": y1, "up": up, "down": down, "flat": flat, "n": up + down + flat}

    for org in orgs:
        nr = newest_report[org]
        r1 = latest.get((org, y1)) or {}
        lo, hi = _num(nr.get("min_price")), _num(nr.get("max_price"))
        out["brokers"].append({
            "org": org, "date": nr.get("report_date"), "title": nr.get("report_title"),
            "rating": nr.get("rating") or "无",
            "rating_bucket": bucket(nr.get("rating")) if bucket is not None else None,
            "np": (v * _WAN) if (v := _num(r1.get("np"))) is not None else None,
            "eps": _num(r1.get("eps")),
            "target_low": lo if lo and lo > 0 else None, "target_high": hi if hi and hi > 0 else None,
        })
    out["brokers"].sort(key=lambda b: b["date"] or "", reverse=True)
    out["first_year"] = y1
    out["status"] = "ready"
    return out


# ── company guidance (业绩预告) ───────────────────────────────────────────────
_GUIDANCE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_GUIDANCE_FIELDS = ("ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,"
                    "net_profit_max,last_parent_net,first_ann_date,summary,change_reason")


def _guidance_rows(ts_code: str) -> list[dict]:
    hit = _GUIDANCE_CACHE.get(ts_code)
    if hit and time.time() - hit[0] < _GUIDANCE_TTL:
        return hit[1]
    from . import cn_data
    rows = cn_data.call("forecast", {"ts_code": ts_code}, _GUIDANCE_FIELDS)
    _GUIDANCE_CACHE[ts_code] = (time.time(), rows)
    return rows


def company_guidance(rows: list[dict], latest_reported_end: str | None, limit: int = 4,
                     actuals: dict[str, float] | None = None) -> dict[str, Any] | None:
    """The issuer's own pre-announcements, newest period first.

    A period is announced, and sometimes re-announced: the latest filing for a
    period wins and the entry is marked revised. `pending` is the part that
    matters — guidance for a period the company has not yet reported is news;
    guidance for one it has is history.

    Periods are YEAR-TO-DATE, like every A-share filing: "H1" is January to
    June, "9M" January to September. `actuals` maps a period end to the profit
    attributable to the parent the company went on to report for it — also
    year-to-date, so the two are like for like — which turns old guidance into
    a record of how good this company's guidance is.
    """
    by_end: dict[str, list[dict]] = {}
    for r in rows or []:
        end = r.get("end_date") or ""
        if len(end) == 8 and end[4:] in _PERIOD:
            by_end.setdefault(end, []).append(r)
    if not by_end:
        return None
    items = []
    for end in sorted(by_end, reverse=True)[:limit]:
        vs = sorted(by_end[end], key=lambda r: r.get("ann_date") or "")
        r = vs[-1]
        lo, hi = _num(r.get("net_profit_min")), _num(r.get("net_profit_max"))
        prior = _num(r.get("last_parent_net"))
        if prior is None:                       # the amended filing sometimes drops the base; an earlier one has it
            prior = next((p for v in reversed(vs) if (p := _num(v.get("last_parent_net"))) is not None), None)
        lo_y = lo * _WAN if lo is not None else None
        hi_y = hi * _WAN if hi is not None else None
        prior_y = prior * _WAN if prior is not None else None
        # The vendor's own % change is quoted even off a loss — CXMT's H1 2026
        # reads "+2,244%" against a prior-year loss. Recomputed, with the same
        # refusal as everywhere else on the page.
        g_lo, why_lo = pct_growth(lo_y, prior_y)
        g_hi, why_hi = pct_growth(hi_y, prior_y)
        figures = {(_num(v.get("net_profit_min")), _num(v.get("net_profit_max"))) for v in vs}
        items.append({
            "period": f"{_PERIOD[end[4:]]} {end[:4]}", "end_date": end,
            "type": r.get("type"), "type_en": _GUIDANCE_TYPE.get(r.get("type") or "", r.get("type")),
            "np_low": lo_y, "np_high": hi_y, "prior": prior_y,
            "growth_low": g_lo, "growth_high": g_hi, "growth_reason": why_lo or why_hi,
            "announced": r.get("ann_date"), "first_announced": vs[0].get("ann_date"),
            "revised": len(figures) > 1,
            "pending": bool(latest_reported_end) and end > latest_reported_end,
            "reason": (r.get("change_reason") or "").strip() or None,
        })
        act = (actuals or {}).get(end)
        if act is not None and lo_y is not None and hi_y is not None:
            items[-1]["actual"] = act
            items[-1]["outcome"] = ("above" if act > hi_y else "below" if act < lo_y else "within")
    return {"items": items, "has_pending": any(i["pending"] for i in items),
            "latest_reported_end": latest_reported_end}


def stock_forecasts(ts_code: str, *, market_cap: float | None, price: float | None,
                    actual: dict[str, Any] | None, latest_reported_end: str | None,
                    actuals: dict[str, float] | None = None,
                    history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything the card needs. Neither half is allowed to take the other down.

    `history` is the last few reported years — [{year, revenue, net_parent}] —
    which the card draws beside the forecast years, so an expected figure is
    seen against what the company has actually done.
    """
    from . import cn_reports
    out: dict[str, Any] = {"broker": None, "guidance": None, "history": history or []}
    try:
        cn_reports.ensure_reports_cache()
        out["broker"] = broker_forecasts(
            ts_code, cn_reports._load_raw(), market_cap=market_cap, price=price, actual=actual,
            coverage=cn_reports.reports_status(), bucket=cn_reports._rating_bucket)
    except Exception as exc:                       # noqa: BLE001
        out["broker_error"] = f"{type(exc).__name__}: {exc}"
    try:
        out["guidance"] = company_guidance(_guidance_rows(ts_code), latest_reported_end,
                                           actuals=actuals)
    except Exception as exc:                       # noqa: BLE001
        out["guidance_error"] = f"{type(exc).__name__}: {exc}"
    return out

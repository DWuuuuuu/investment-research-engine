"""What analysts expect of a US-listed stock: targets, ratings, estimates.

One source, Yahoo Finance (through yfinance), because it serves the same
fields for every ticker and so can be fetched systematically:

  * a target-price summary (low / mean / median / high) and how many analysts
    it covers,
  * rating counts, this month and the three before,
  * firm-by-firm rating and target changes, dated,
  * EPS and revenue estimates for the current and next quarter and fiscal year,
    with how those estimates moved over the last 90 days,
  * the next earnings date, and how the last four quarters landed.

What that leaves out is stated on the card, because the gaps are the part a
reader is most likely to trip on:

  * AGGREGATORS DISAGREE. Each site polls a different set of analysts on
    different dates. On 2026-10-12 AAOI's average target read $163 on one,
    $139 on another and $173 on a third, with lows of $109 and $54. This card
    is Yahoo's set and nobody else's.
  * A TARGET IS ONLY AS GOOD AS ITS DATE. A consensus is an average of numbers
    set weeks or months apart. Every named change carries its date and what the
    stock has done since, and the summary says how old the newest one is.
  * THE NAMED LIST IS SHORTER THAN THE COUNT. Yahoo publishes a consensus over
    N analysts but names far fewer in its history of changes.
  * EPS IS THE ANALYSTS' BASIS, usually adjusted, not GAAP. It is never mixed
    with the reported EPS elsewhere on the page: the year-ago figure shown
    beside an estimate is the one Yahoo pairs with it.
  * NO CONFLICTS OF INTEREST. Whether a covering firm also underwrites or
    sells the company's shares is not in the data.

Growth follows the house rule (`pct_growth`): no percentage off a loss.
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime
from typing import Any, Callable

from .quarterly_growth import pct_growth

_FIRM_WINDOW_DAYS = 365        # a firm's view older than this is not a current view
_STALE_DAYS = 45               # newest named update older than this -> say so on the tile
_MOVED_PCT = 15.0              # ...or the stock has moved this much since it
_TTL = 30 * 60
_CACHE: dict[str, tuple[float, dict]] = {}

_POSITIVE = {"buy", "strong buy", "outperform", "overweight", "positive", "accumulate", "add",
             "market outperform", "sector outperform", "top pick", "conviction buy", "long-term buy"}
_NEUTRAL = {"hold", "neutral", "equal-weight", "equal weight", "market perform", "sector perform",
            "peer perform", "perform", "in-line", "in line", "sector weight", "mixed", "fair value"}
_NEGATIVE = {"sell", "strong sell", "underperform", "underweight", "reduce", "negative",
             "market underperform", "sector underperform"}
_ACTIONS = {"up": "upgraded", "down": "downgraded", "init": "initiated", "main": "maintained",
            "reit": "reiterated"}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "to_pydatetime"):
        try:
            return v.to_pydatetime().date()
        except (ValueError, TypeError):
            return None
    if isinstance(v, str) and len(v) >= 10:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _pct(a: float | None, b: float | None) -> float | None:
    """a relative to b, in percent. For prices, which are always positive."""
    if a is None or b is None or b <= 0:
        return None
    return round((a / b - 1) * 100, 1)


def rating_bucket(grade: str | None) -> str | None:
    g = (grade or "").strip().lower()
    return ("positive" if g in _POSITIVE else "neutral" if g in _NEUTRAL
            else "negative" if g in _NEGATIVE else None)


# ── targets ──────────────────────────────────────────────────────────────────
def targets(pt: dict | None, n_analysts: Any, price: float | None) -> dict[str, Any] | None:
    """Target-price summary, each level against today's price."""
    pt = pt or {}
    low, high = _num(pt.get("low")), _num(pt.get("high"))
    mean, median = _num(pt.get("mean")), _num(pt.get("median"))
    mid = median if median is not None else mean
    if mid is None or mid <= 0:
        return None
    # A summary whose middle sits outside its own range is not a summary.
    if low is not None and high is not None and not (low <= mid <= high):
        return None
    n = _num(n_analysts)
    return {"median": median, "mean": mean, "low": low, "high": high,
            "n": int(n) if n else None,
            "upside_pct": _pct(mid, price), "upside_low_pct": _pct(low, price),
            "upside_high_pct": _pct(high, price),
            "basis": "median" if median is not None else "mean"}


# ── ratings ──────────────────────────────────────────────────────────────────
def _counts(row: dict) -> dict[str, int] | None:
    vals = {k: _num(row.get(src)) for k, src in (
        ("strong_buy", "strongBuy"), ("buy", "buy"), ("hold", "hold"),
        ("sell", "sell"), ("strong_sell", "strongSell"))}
    if all(v is None for v in vals.values()):
        return None
    c = {k: int(v or 0) for k, v in vals.items()}
    c["positive"], c["neutral"] = c["strong_buy"] + c["buy"], c["hold"]
    c["negative"] = c["sell"] + c["strong_sell"]
    c["n"] = c["positive"] + c["neutral"] + c["negative"]
    return c if c["n"] else None


def ratings(rows: list[dict] | None) -> dict[str, Any] | None:
    """This month's rating counts, and the oldest month on file to set it against."""
    by = {str(r.get("period")): _counts(r) for r in rows or []}
    now = by.get("0m")
    if not now:
        return None
    out: dict[str, Any] = dict(now)
    for back in ("-3m", "-2m", "-1m"):
        if by.get(back):
            out["before"] = dict(by[back], months_ago=int(back[1]))
            break
    return out


# ── firm by firm ─────────────────────────────────────────────────────────────
def firms(rows: list[dict] | None, price: float | None, today: date,
          price_on: Callable[[str], float | None] | None = None) -> dict[str, Any] | None:
    """Each firm's most recent rating/target change in the last year.

    `rows`: {date, firm, to, from, action, target_action, target, prior_target}.
    Yahoo writes a missing target as 0.
    """
    latest: dict[str, dict] = {}
    for r in rows or []:
        d, firm = _day(r.get("date")), (r.get("firm") or "").strip()
        if not d or not firm or d > today or (today - d).days > _FIRM_WINDOW_DAYS:
            continue
        if firm not in latest or d > latest[firm]["_d"]:
            latest[firm] = dict(r, _d=d)
    if not latest:
        return None
    items = []
    for firm, r in sorted(latest.items(), key=lambda kv: kv[1]["_d"], reverse=True):
        d = r["_d"]
        tgt, prior = _num(r.get("target")), _num(r.get("prior_target"))
        tgt, prior = (tgt if tgt else None), (prior if prior else None)
        then = price_on(d.isoformat()) if price_on else None
        act = (r.get("action") or "").strip().lower()
        items.append({
            "firm": firm, "date": d.isoformat(), "age_days": (today - d).days,
            "rating": r.get("to") or None, "from_rating": r.get("from") or None,
            "rating_bucket": rating_bucket(r.get("to")),
            "action": _ACTIONS.get(act, act or None),
            "changed_rating": act in ("up", "down"),
            "target": tgt, "prior_target": prior if prior != tgt else None,
            "target_action": (r.get("target_action") or "").strip().lower() or None,
            "target_vs_price_pct": _pct(tgt, price),
            "price_then": round(then, 2) if then else None,
            "stock_since_pct": _pct(price, then),
        })
    # No median of the named targets is offered: they are a subset of the
    # analysts behind the consensus, of very different ages, and a second
    # "median target" beside Yahoo's would read as a competing answer.
    with_target = [x for x in items if x["target"] is not None]
    newest = items[0]
    out: dict[str, Any] = {
        "items": items, "n": len(items), "n_with_target": len(with_target),
        "window_days": _FIRM_WINDOW_DAYS,
        "newest": newest["date"], "newest_age_days": newest["age_days"],
        "since_newest_pct": newest["stock_since_pct"],
    }
    moved = newest["stock_since_pct"]
    out["stale"] = bool(newest["age_days"] > _STALE_DAYS or (moved is not None and abs(moved) >= _MOVED_PCT))
    return out


# ── estimates ────────────────────────────────────────────────────────────────
def _est(row: dict | None, year_ago_key: str) -> dict[str, Any] | None:
    row = row or {}
    avg = _num(row.get("avg"))
    if avg is None:
        return None
    n = _num(row.get("numberOfAnalysts"))
    ago = _num(row.get(year_ago_key))
    growth, why = pct_growth(avg, ago) if ago is not None else (None, None)
    return {"avg": avg, "low": _num(row.get("low")), "high": _num(row.get("high")),
            "n": int(n) if n else None, "year_ago": ago, "growth": growth, "growth_reason": why}


def _direction(cur: float, then: float | None) -> str | None:
    """Up, down or flat — flat meaning within a quarter of one percent, which
    is one analyst nudging a cent, not a revision trend."""
    if then is None:
        return None
    tol = max(0.005, 0.0025 * abs(then))
    return "up" if cur > then + tol else "down" if cur < then - tol else "flat"


def estimates(periods: list[dict] | None, price: float | None, *,
              quarter_label: Callable[[date], str] | None = None,
              same_currency: bool = True) -> list[dict]:
    """One row per period Yahoo estimates: this quarter, next, this year, next.

    `periods`: {period: "0q"|"+1q"|"0y"|"+1y", end, eps: {...}, revenue: {...},
    trend: {current, 7daysAgo, 30daysAgo, 60daysAgo, 90daysAgo},
    revisions: {upLast7days, upLast30days, downLast30days, downLast7Days}}.
    """
    out = []
    for p in periods or []:
        code = str(p.get("period") or "")
        if code not in ("0q", "+1q", "0y", "+1y"):
            continue
        eps, rev = _est(p.get("eps"), "yearAgoEps"), _est(p.get("revenue"), "yearAgoRevenue")
        if not eps and not rev:
            continue
        end = _day(p.get("end"))
        annual = code.endswith("y")
        if annual:
            label = f"FY{end.year}E" if end else ("This fiscal year" if code == "0y" else "Next fiscal year")
        else:
            label = None
            if end and quarter_label:
                try:
                    label = quarter_label(end)
                except Exception:                  # noqa: BLE001
                    label = None
            label = (label + "E") if label else ("This quarter" if code == "0q" else "Next quarter")
        row: dict[str, Any] = {"period": code, "annual": annual, "label": label,
                               "end": end.isoformat() if end else None, "eps": eps, "revenue": rev}
        if annual and eps and price and same_currency:
            if eps["avg"] > 0:
                row["pe"] = round(price / eps["avg"], 1)
            else:
                row["pe_reason"] = "loss_forecast"
        tr, rv = p.get("trend") or {}, p.get("revisions") or {}
        cur = _num(tr.get("current"))
        if cur is not None:
            ago30, ago90 = _num(tr.get("30daysAgo")), _num(tr.get("90daysAgo"))
            row["eps_trend"] = {
                "current": cur, "d7": _num(tr.get("7daysAgo")), "d30": ago30,
                "d60": _num(tr.get("60daysAgo")), "d90": ago90,
                "direction_30": _direction(cur, ago30), "direction_90": _direction(cur, ago90),
            }
            # A percentage only where the starting estimate was a profit.
            if ago90 is not None and ago90 > 0:
                row["eps_trend"]["change_90_pct"] = round((cur / ago90 - 1) * 100, 1)
        up, down = _num(rv.get("upLast30days")), _num(rv.get("downLast30days"))
        if up is not None or down is not None:
            row["revisions_30"] = {"up": int(up or 0), "down": int(down or 0)}
        out.append(row)
    order = {"0q": 0, "+1q": 1, "0y": 2, "+1y": 3}
    return sorted(out, key=lambda r: order[r["period"]])


# ── next earnings, and how the last ones landed ──────────────────────────────
def next_earnings(cal: dict | None, today: date, is_estimate: Any = None) -> dict[str, Any] | None:
    cal = cal or {}
    raw = cal.get("Earnings Date")
    ds = sorted(d for d in (_day(x) for x in (raw if isinstance(raw, (list, tuple)) else [raw])) if d)
    ds = [d for d in ds if d >= today]
    if not ds:
        return None
    return {"date": ds[0].isoformat(), "date_to": ds[-1].isoformat() if ds[-1] != ds[0] else None,
            "days": (ds[0] - today).days, "estimated": bool(is_estimate) if is_estimate is not None else None,
            "eps_avg": _num(cal.get("Earnings Average")), "eps_low": _num(cal.get("Earnings Low")),
            "eps_high": _num(cal.get("Earnings High")), "revenue_avg": _num(cal.get("Revenue Average"))}


def surprises(rows: list[dict] | None, quarter_label: Callable[[date], str] | None = None,
              limit: int = 4) -> dict[str, Any] | None:
    """Reported EPS against the estimate going in — beat, met or missed.

    A surprise is given as a percentage only against a positive estimate: a
    beat of $0.05 on an estimate of $0.015 is "300%" and means nothing.
    """
    items = []
    for r in rows or []:
        d, act, est = _day(r.get("quarter")), _num(r.get("actual")), _num(r.get("estimate"))
        if d is None or act is None or est is None:
            continue
        label = None
        if quarter_label:
            try:
                label = quarter_label(d)
            except Exception:                      # noqa: BLE001
                label = None
        diff = round(act - est, 4)
        items.append({"end": d.isoformat(), "label": label or d.strftime("%b %Y"),
                      "actual": act, "estimate": est, "diff": diff,
                      "result": "beat" if diff > 0.005 else "miss" if diff < -0.005 else "met",
                      "surprise_pct": round(diff / est * 100, 1) if est >= 0.05 else None})
    if not items:
        return None
    items = sorted(items, key=lambda x: x["end"], reverse=True)[:limit]
    return {"items": items, "beats": sum(1 for x in items if x["result"] == "beat"),
            "misses": sum(1 for x in items if x["result"] == "miss"), "n": len(items)}


# ── charts: reported years, then the forecast ones ───────────────────────────
def revenue_path(history: list[dict] | None, est: list[dict]) -> list[dict]:
    """Reported annual revenue followed by the estimates.

    `history`: [{end: "YYYY-MM-DD", revenue}] oldest first, from the statements.
    When the statements stop a year short of the estimates (the data vendor has
    not loaded the newest annual report yet — Costco, October 2026), the gap is
    filled with the year-ago figure Yahoo pairs with its own estimate.
    """
    years = [r for r in est if r["annual"] and r.get("revenue") and r.get("end")]
    if not years:
        return []
    first_end = date.fromisoformat(years[0]["end"])
    pts = []
    for h in (history or [])[-3:]:
        d = _day(h.get("end"))
        if d and h.get("revenue") is not None and d < first_end:
            pts.append({"label": f"FY{d.year}", "v": h["revenue"], "est": False, "_d": d})
    last = pts[-1]["_d"] if pts else None
    ago = years[0]["revenue"].get("year_ago")
    if ago is not None and (last is None or (first_end - last).days > 400):
        pts.append({"label": f"FY{first_end.year - 1}", "v": ago, "est": False, "from_estimate_table": True})
    for y in years:
        pts.append({"label": y["label"], "v": y["revenue"]["avg"], "lo": y["revenue"]["low"],
                    "hi": y["revenue"]["high"], "est": True})
    return [{k: v for k, v in p.items() if k != "_d"} for p in pts]


def eps_path(est: list[dict]) -> list[dict]:
    """EPS on the analysts' own basis throughout: last year as Yahoo pairs it
    with the estimate, then the estimates. Never the GAAP EPS from the filings."""
    years = [r for r in est if r["annual"] and r.get("eps")]
    if not years:
        return []
    pts = []
    ago = years[0]["eps"].get("year_ago")
    if ago is not None and years[0].get("end"):
        pts.append({"label": f"FY{int(years[0]['end'][:4]) - 1}", "v": ago, "est": False})
    for y in years:
        pts.append({"label": y["label"], "v": y["eps"]["avg"], "lo": y["eps"]["low"],
                    "hi": y["eps"]["high"], "est": True})
    return pts


def build(*, price: float | None, today: date, price_targets: dict | None = None,
          n_target_analysts: Any = None, rating_rows: list[dict] | None = None,
          firm_rows: list[dict] | None = None, periods: list[dict] | None = None,
          calendar: dict | None = None, earnings_date_is_estimate: Any = None,
          surprise_rows: list[dict] | None = None, history: list[dict] | None = None,
          quarter_label: Callable[[date], str] | None = None,
          price_on: Callable[[str], float | None] | None = None,
          currency: str | None = None, estimate_currency: str | None = None,
          revenue_currency: str | None = None) -> dict[str, Any] | None:
    """`currency` is what the share price and the page's statements are in;
    `estimate_currency` what the EPS estimates are in; `revenue_currency` what
    the revenue estimates are in. For an ADR they differ: TSMC's price and EPS
    estimates are US dollars per ADR, its revenue estimates New Taiwan dollars.
    """
    same = not (currency and estimate_currency) or currency == estimate_currency
    rev_same = not (currency and revenue_currency) or currency == revenue_currency
    est = estimates(periods, price, quarter_label=quarter_label, same_currency=same)
    out: dict[str, Any] = {
        "market": "us", "source": "Yahoo Finance", "as_of": today.isoformat(), "price": price,
        "targets": targets(price_targets, n_target_analysts, price),
        "ratings": ratings(rating_rows),
        "firms": firms(firm_rows, price, today, price_on),
        "estimates": est,
        "next_earnings": next_earnings(calendar, today, earnings_date_is_estimate),
        "surprises": surprises(surprise_rows, quarter_label),
        # Statement history is in the page's currency; drawn beside estimates
        # in another one it would be a chart of the exchange rate.
        "revenue_path": revenue_path(history if rev_same else None, est),
        "eps_path": eps_path(est),
        "currency": currency, "estimate_currency": estimate_currency or currency,
        "revenue_currency": revenue_currency or currency,
    }
    if not any(out[k] for k in ("targets", "ratings", "firms", "estimates")):
        return None
    if not same:
        out["currency_note"] = (f"Estimates are in {estimate_currency}; the share price is in {currency}. "
                                "No P/E on forecast earnings is shown, because dividing one by the other "
                                "would mix currencies.")
    return out


# ── yfinance adapter ─────────────────────────────────────────────────────────
def _records(df: Any) -> list[dict]:
    if df is None or getattr(df, "empty", True):
        return []
    return [dict(row, _index=idx) for idx, row in zip(df.index, df.to_dict("records"))]


def _row_of(df: Any, key: str) -> dict | None:
    if df is None or getattr(df, "empty", True) or key not in df.index:
        return None
    return df.loc[key].to_dict()


def fetch(t: Any, info: dict, price: float | None, *, history: list[dict] | None = None,
          quarter_label: Callable[[date], str] | None = None,
          price_on: Callable[[str], float | None] | None = None,
          today: date | None = None) -> dict[str, Any] | None:
    """Pull every analyst table for one ticker and shape it. Each table is
    optional: a thinly covered stock has some and not others."""
    today = today or date.today()
    key = f"{getattr(t, 'ticker', '')}|{today.isoformat()}"
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL and hit[1].get("price") == price:
        return hit[1]

    def grab(name: str) -> Any:
        try:
            return getattr(t, name)
        except Exception:                          # noqa: BLE001
            return None

    ee, re_, tr, rv = (grab(n) for n in ("earnings_estimate", "revenue_estimate", "eps_trend", "eps_revisions"))
    ends: dict[str, Any] = {}
    try:        # period end dates live only in the raw payload behind those tables
        for x in getattr(t._analysis, "_earnings_trend", None) or []:
            ends[str(x.get("period"))] = x.get("endDate")
    except Exception:                              # noqa: BLE001
        ends = {}
    periods = [{"period": code, "end": ends.get(code), "eps": _row_of(ee, code),
                "revenue": _row_of(re_, code), "trend": _row_of(tr, code),
                "revisions": _row_of(rv, code)} for code in ("0q", "+1q", "0y", "+1y")]
    est_ccy = next((str(p["eps"].get("currency")) for p in periods
                    if p["eps"] and isinstance(p["eps"].get("currency"), str)), None)
    rev_ccy = next((str(p["revenue"].get("currency")) for p in periods
                    if p["revenue"] and isinstance(p["revenue"].get("currency"), str)), None)

    firm_rows = [{"date": r["_index"], "firm": r.get("Firm"), "to": r.get("ToGrade"),
                  "from": r.get("FromGrade"), "action": r.get("Action"),
                  "target_action": r.get("priceTargetAction"), "target": r.get("currentPriceTarget"),
                  "prior_target": r.get("priorPriceTarget")} for r in _records(grab("upgrades_downgrades"))]
    surprise_rows = [{"quarter": r["_index"], "actual": r.get("epsActual"), "estimate": r.get("epsEstimate")}
                     for r in _records(grab("earnings_history"))]
    cal = grab("calendar")
    out = build(
        price=price, today=today, price_targets=grab("analyst_price_targets"),
        n_target_analysts=info.get("numberOfAnalystOpinions"),
        rating_rows=_records(grab("recommendations_summary")),
        firm_rows=firm_rows, periods=periods, calendar=cal if isinstance(cal, dict) else None,
        earnings_date_is_estimate=info.get("isEarningsDateEstimate"),
        surprise_rows=surprise_rows, history=history, quarter_label=quarter_label,
        price_on=price_on, currency=info.get("currency"), estimate_currency=est_ccy,
        revenue_currency=rev_ccy)
    if out:
        _CACHE[key] = (time.time(), out)
    return out

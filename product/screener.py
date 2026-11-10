"""
Stock + ETF Screener.

For individual stocks: 3-Gate system (quality, moat, valuation).
For ETFs: 5-Dimension system (cost, size/liquidity, fund quality,
          return quality, structure/strategy).

Uses yfinance (no API key required). Caches nothing — call per request.
"""
from __future__ import annotations

import math
from typing import Any

from .cycle_assess import assess_cycle


# ── Helpers ───────────────────────────────────────────────────────────────────

def _s(val: Any, default=None) -> Any:
    """Return val, or default if None / NaN."""
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return val


def _pct(val: Any) -> float | None:
    """Convert a 0-1 fraction to a % float, or return None."""
    v = _s(val)
    return round(v * 100, 2) if v is not None else None


def _row(df: Any, *names: str) -> float | None:
    """Return first matching row from a DataFrame column[0], or None."""
    if df is None or df.empty:
        return None
    col = df.columns[0]
    for name in names:
        if name in df.index:
            try:
                v = float(df.loc[name, col])
                if not math.isnan(v):
                    return v
            except (TypeError, ValueError):
                pass
    return None


def _check(name: str, value: float | None, threshold: float,
           direction: str, unit: str = "", note: str = "") -> dict:
    """Build a single Gate-1 metric dict. direction: 'above' | 'below'."""
    if value is None:
        return {
            "name": name, "value": None, "threshold": threshold,
            "direction": direction, "unit": unit, "note": note,
            "pass": None, "display": "N/A",
        }
    passed = (value > threshold) if direction == "above" else (value < threshold)
    if abs(value) >= 1000:
        display = f"{value:,.0f}{unit}"
    else:
        display = f"{value:.1f}{unit}"
    return {
        "name": name, "value": round(value, 2), "threshold": threshold,
        "direction": direction, "unit": unit, "note": note,
        "pass": passed, "display": display,
    }


# ── ETF expense ratio thresholds by category ─────────────────────────────────

def _er_thresholds(category: str) -> tuple[float, float]:
    """Return (good_cutoff, flag_cutoff) for a Morningstar category string, as
    DECIMAL FRACTIONS (0.0015 = 0.15%) — must match expense_ratio's own scale
    (a decimal fraction, e.g. 0.002 for a 0.20% fund), not a raw percent number.
    Previously these were the intended percent VALUES (0.15 meaning "0.15%")
    used directly as the decimal-fraction cutoff, making every threshold 100x
    too loose (comparing against 15%/25% instead of 0.15%/0.25%) — confirmed:
    even an expensive 2% (0.02) actively-managed fund read as "excellent" under
    the broad-market 0.15 cutoff, since 0.02 <= 0.15. Fixed 2026-08-10."""
    cat = (category or "").lower()
    if any(x in cat for x in ["technology", "semiconductor", "health", "sector",
                               "energy", "real estate", "financial", "utilities",
                               "consumer", "industrial", "communication"]):
        return 0.0025, 0.0050      # sector ETFs
    if any(x in cat for x in ["emerging", "china", "india", "latin"]):
        return 0.0035, 0.0060      # EM
    if any(x in cat for x in ["foreign", "international", "europe", "pacific",
                               "world", "global", "developed"]):
        return 0.0020, 0.0040      # developed international
    if any(x in cat for x in ["commodity", "gold", "silver", "precious", "oil",
                               "natural resource"]):
        return 0.0040, 0.0075      # commodity
    if any(x in cat for x in ["bond", "fixed income", "treasury", "credit",
                               "inflation", "tips", "high yield", "municipal"]):
        return 0.0015, 0.0035      # bonds
    if any(x in cat for x in ["factor", "smart beta", "value", "momentum",
                               "quality", "minimum volatility", "multi-factor",
                               "dividend", "growth"]):
        return 0.0030, 0.0055      # factor / smart beta
    if any(x in cat for x in ["thematic", "innovation", "disruptive", "theme",
                               "future", "megatrend", "ai", "cyber", "space"]):
        return 0.0055, 0.0085      # thematic
    # default: broad US equity
    return 0.0015, 0.0025


def _etf_check(name: str, value: float | None, threshold: float,
               direction: str, unit: str = "", note: str = "") -> dict:
    """Same shape as _check() for consistent frontend rendering."""
    return _check(name, value, threshold, direction, unit, note)


# ── ETF screener ───────────────────────────────────────────────────────────────

def screen_etf(ticker_str: str, t: Any, info: dict) -> dict:  # noqa: ANN001
    import datetime

    name       = _s(info.get("longName") or info.get("shortName"), ticker_str)
    category   = _s(info.get("category"), "—")
    fund_family = _s(info.get("fundFamily"), "—")
    price      = _s(info.get("navPrice") or info.get("currentPrice") or info.get("previousClose"))
    aum        = _s(info.get("totalAssets"))
    avg_vol    = _s(info.get("averageVolume") or info.get("averageVolume10days"))
    yield_val  = _s(info.get("yield") or info.get("dividendYield"))   # already decimal
    ret_3y     = _s(info.get("threeYearAverageReturn"))
    ret_5y     = _s(info.get("fiveYearAverageReturn"))
    ret_ytd    = _s(info.get("ytdReturn"))
    website    = _s(info.get("website"), "")
    description = ""

    # Fund age
    inception_ts = _s(info.get("fundInceptionDate"))
    fund_age_years: float | None = None
    inception_date_str = "—"
    if inception_ts:
        dt = datetime.datetime.fromtimestamp(inception_ts)
        fund_age_years = round((datetime.datetime.now() - dt).days / 365.25, 1)
        inception_date_str = dt.strftime("%b %Y")

    # Pull fund_operations (expense ratio, turnover, total assets)
    expense_ratio: float | None = None
    turnover_rate: float | None = None
    try:
        fd = t.funds_data
        fo = fd.fund_operations
        if fo is not None and not fo.empty:
            col = fo.columns[0]
            er_key  = next((k for k in fo.index if "expense" in k.lower()), None)
            to_key  = next((k for k in fo.index if "turnover" in k.lower()), None)
            if er_key:
                expense_ratio = float(fo.loc[er_key, col])
                if expense_ratio > 1:          # stored as percentage not decimal
                    expense_ratio = expense_ratio / 100
            if to_key:
                turnover_rate = float(fo.loc[to_key, col])
                if turnover_rate <= 1:         # stored as decimal not percentage
                    turnover_rate = turnover_rate * 100
        description_attr = getattr(fd, "description", "")
        if description_attr:
            description = (description_attr[:350] + "…") if len(description_attr) > 350 else description_attr
    except Exception:
        pass

    # Top holdings
    top_holdings: list[dict] = []
    top10_weight: float | None = None
    try:
        th = t.funds_data.top_holdings
        if th is not None and not th.empty:
            for sym, row in th.iterrows():
                pct = float(row["Holding Percent"]) * 100
                top_holdings.append({"symbol": str(sym), "name": str(row.get("Name", sym)), "pct": round(pct, 2)})
            top10_weight = round(sum(h["pct"] for h in top_holdings), 1)
    except Exception:
        pass

    # Sector weights
    sector_weights: dict = {}
    try:
        sw = t.funds_data.sector_weightings
        if sw:
            sector_weights = {k: round(v * 100, 2) for k, v in sw.items() if v and v > 0}
    except Exception:
        pass

    # Daily dollar volume (proxy for liquidity)
    daily_dollar_vol: float | None = None
    if avg_vol and price:
        daily_dollar_vol = avg_vol * price

    # ── Expense ratio scoring ─────────────────────────────────────────────────
    good_er, flag_er = _er_thresholds(category)
    er_pct = round(expense_ratio * 100, 4) if expense_ratio is not None else None
    if er_pct is None:
        er_verdict = "unknown"
    elif expense_ratio <= good_er:
        er_verdict = "excellent"
    elif expense_ratio <= flag_er:
        er_verdict = "acceptable"
    else:
        er_verdict = "expensive"

    # ── 5 dimension checks ────────────────────────────────────────────────────

    # Dim 1 — Cost (use good_er as threshold)
    dim1 = _etf_check("Expense Ratio", er_pct, round(good_er * 100, 2), "below", "%",
                      f"Category benchmark ≤{round(good_er*100,2)}% · flag >{round(flag_er*100,2)}%")

    # Dim 2a — AUM
    aum_m = round(aum / 1e6) if aum else None
    dim2a = _etf_check("AUM", aum / 1e9 if aum else None, 0.5, "above", "B",
                       "> $500M solid · < $100M closure risk")

    # Dim 2b — Daily dollar volume
    ddv_m = round(daily_dollar_vol / 1e6, 1) if daily_dollar_vol else None
    dim2b = _etf_check("Daily $ Volume", ddv_m, 10, "above", "M",
                       "> $10M/day comfortable · < $1M/day thin liquidity")

    # Dim 3a — Fund age
    dim3a = _etf_check("Fund Age", fund_age_years, 5, "above", " yrs",
                       "> 5 years to see performance across a market cycle")

    # Dim 3b — Turnover (bond ETFs have inherently high turnover as bonds mature — don't penalise)
    _is_bond = any(x in (category or "").lower() for x in
                   ["bond", "fixed income", "treasury", "credit", "inflation",
                    "tips", "high yield", "municipal", "short", "intermediate",
                    "long government", "ultrashort"])
    _to_threshold = 200 if _is_bond else 20
    _to_note = ("Bond ETFs naturally have high turnover as bonds mature — not a tax-efficiency concern"
                if _is_bond else "< 10% excellent · > 50% consider tax-advantaged account only")
    dim3b = _etf_check("Turnover Rate", turnover_rate, _to_threshold, "below", "%", _to_note)

    # Dim 3c — Top-10 concentration
    dim3c = _etf_check("Top-10 Concentration", top10_weight, 55, "below", "%",
                       "< 35% diversified · > 55% concentrated single-name risk")

    all_checks = [dim1, dim2a, dim2b, dim3a, dim3b, dim3c]
    passed   = sum(1 for c in all_checks if c["pass"] is True)
    failed   = sum(1 for c in all_checks if c["pass"] is False)
    na_count = sum(1 for c in all_checks if c["pass"] is None)
    scorable = len(all_checks) - na_count

    if scorable == 0:
        overall_verdict = "unknown"
    elif passed >= 5:
        overall_verdict = "pass"
    elif passed >= 3:
        overall_verdict = "partial"
    else:
        overall_verdict = "fail"

    # ── Returns section ────────────────────────────────────────────────────────
    def _ret_verdict(r: float | None) -> str:
        if r is None: return "unknown"
        if r > 0.15:  return "strong"
        if r > 0.07:  return "good"
        if r > 0.00:  return "weak"
        return "negative"

    # AUM formatted
    if aum:
        if aum >= 1e12: aum_fmt = f"${aum/1e12:.2f}T"
        elif aum >= 1e9: aum_fmt = f"${aum/1e9:.1f}B"
        else: aum_fmt = f"${aum/1e6:.0f}M"
    else:
        aum_fmt = "—"

    # ── Industry cycle assessment ─────────────────────────────────────────────
    try:
        cycle = assess_cycle(ticker_str, t, info)
    except Exception:
        cycle = None

    # ── Price history + beta ───────────────────────────────────────────────────
    try:
        hist = t.history(period="5y")
        price_history = [
            {"d": str(idx.date()), "c": round(float(row["Close"]), 2),
             "o": _ohlc_val(row.get("Open")), "h": _ohlc_val(row.get("High")),
             "l": _ohlc_val(row.get("Low")), "v": _ohlc_val(row.get("Volume"), 0)}
            for idx, row in hist.iterrows()
            if row["Close"] == row["Close"]
        ] if not hist.empty else []
    except Exception:
        price_history = []

    beta_etf = _s(info.get("beta3Year") or info.get("beta"))

    return {
        "ticker":          ticker_str,
        "quote_type":      "ETF",
        "name":            name,
        "category":        category,
        "fund_family":     fund_family,
        "description":     description,
        "price":           price,
        "aum":             aum,
        "aum_fmt":         aum_fmt,
        "website":         website,
        "inception_date":  inception_date_str,
        "fund_age_years":  fund_age_years,
        "expense_ratio":   er_pct,
        "expense_ratio_decimal": expense_ratio,
        "er_verdict":      er_verdict,
        "er_good_threshold": round(good_er * 100, 2),
        "er_flag_threshold": round(flag_er * 100, 2),
        "turnover_rate":   turnover_rate,
        "avg_volume":      avg_vol,
        "daily_dollar_vol_m": ddv_m,
        "yield_pct":       round(yield_val * 100, 2) if yield_val else None,
        "ret_ytd":         round(ret_ytd * 100, 2)   if ret_ytd  else None,
        "ret_3y":          round(ret_3y  * 100, 2)   if ret_3y   else None,
        "ret_5y":          round(ret_5y  * 100, 2)   if ret_5y   else None,
        "ret_3y_verdict":  _ret_verdict(ret_3y),
        "ret_5y_verdict":  _ret_verdict(ret_5y),
        "top_holdings":    top_holdings,
        "top10_weight":    top10_weight,
        "sector_weights":  sector_weights,
        "checks":          all_checks,
        "verdict": {
            "overall":  overall_verdict,
            "passed":   passed,
            "failed":   failed,
            "na":       na_count,
            "scorable": scorable,
        },
        "cycle":         cycle,
        "val_growth":    _safe_val_growth(t, info, price_history, is_etf=True),
        "price_history": price_history,
        "benchmark_history": _benchmark_history(),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "benchmark_name": BENCHMARK_NAME,
        "beta":          round(beta_etf, 2) if beta_etf is not None else None,
    }


# ── Earnings & valuation history ─────────────────────────────────────────────

def _share_change_3y(series: Any, splits: Any) -> tuple[float | None, str | None]:
    """Percent reduction in share count over the series, split-adjusted.

    Yahoo's share-count history is NOT split-adjusted: Broadcom's runs 412.7m ->
    4,773.6m across its 2024 10-for-1 split and showed "-1,056% reduction"; Sony
    "-369%" across its 5-for-1. Each point is scaled by every split AFTER its
    date, and each end is the median of five points, because this series also
    carries isolated outliers.

    Both ends come from the same series. The vendor's current share count is in
    trading units while this history counts ordinary shares, so mixing the two
    showed ADRs "reducing" shares by ~80%.
    """
    import pandas as pd
    if series is None or len(series) < 2:
        return None, None
    ser = series.dropna().astype(float)
    ser = ser[ser > 0]
    if len(ser) < 2:
        return None, None
    if getattr(ser.index, "tz", None) is not None:
        ser.index = ser.index.tz_localize(None)
    sp = splits if splits is not None else pd.Series(dtype=float)
    if len(sp) and getattr(sp.index, "tz", None) is not None:
        sp = sp.copy()
        sp.index = sp.index.tz_localize(None)
    factors = []
    for d in ser.index:
        f = 1.0
        for sd, r in sp.items():
            if sd > d and r and r > 0:
                f *= float(r)
        factors.append(f)
    adj = ser * pd.Series(factors, index=ser.index)
    k = min(5, max(1, len(adj) // 4))
    start, end = float(adj.iloc[:k].median()), float(adj.iloc[-k:].median())
    change = (1 - end / start) * 100
    if abs(change) > 60:
        return None, (f"share history looks inconsistent ({start:,.0f} -> {end:,.0f} after split adjustment); "
                      f"not reported")
    return round(change, 2), None


def _fy_ended_label(df: Any) -> str | None:
    """'FY ended Oct 2025' from an annual statement's newest column.

    Deliberately not 'FY2025': companies whose year ends in Jan/Feb disagree on
    whether that is FY2025 or FY2026 (Nvidia names it by the end year, many
    retailers by the start year). The end month is unambiguous.
    """
    try:
        if df is None or getattr(df, "empty", True):
            return None
        return "FY ended " + df.columns[0].strftime("%b %Y")
    except (AttributeError, IndexError, ValueError):
        return None


def _first_trade_date(info: dict) -> Any:
    from datetime import datetime as _dt, timezone as _tz
    ms = info.get("firstTradeDateMilliseconds")
    if not ms and info.get("firstTradeDateEpochUtc"):
        ms = int(info["firstTradeDateEpochUtc"]) * 1000
    try:
        return _dt.fromtimestamp(int(ms) / 1000, _tz.utc).date() if ms else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _fy_end_month(info: dict) -> int | None:
    """Fiscal year-end month from Yahoo's `lastFiscalYearEnd` (epoch seconds).

    Imports locally (this module has no top-level datetime import) and catches
    only data errors. A first version caught `Exception`, which swallowed the
    NameError from a missing import, returned None for every ticker, and
    silently reverted every fiscal-quarter label to "Mon YYYY".
    """
    from datetime import datetime as _dt, timezone as _tz
    v = info.get("lastFiscalYearEnd")
    if not v:
        return None
    try:
        return _dt.fromtimestamp(int(v), _tz.utc).month
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _gate_growth_check(name: str, value: float | None, reason: str | None,
                       restated: bool, note: str) -> dict:
    """A growth check that says WHY it is unscored instead of a bare N/A.

    Growth off a loss is not meaningful, and a comparison across a restatement
    compares two accounting bases — neither should silently pass or fail.
    """
    c = _check(name, value, 5, "above", "%", note)
    # Every reason quarterly_growth can emit maps to an explanation. A bare
    # "N/A" leaves the reader guessing whether data is missing or meaningless.
    why = {
        "negative_base":             ("n/m", "the year-ago quarter was a loss"),
        "turned_to_loss":            ("n/m", "swung from profit to loss"),
        "restatement_mix":           ("n/a", "withheld: the year-ago quarter spans a restatement"),
        "negative_discrete_revenue": ("n/a", "withheld: filings are internally inconsistent"),
        "missing_prior_filing":      ("n/a", "a filing needed to derive the year-ago quarter is missing (common for recent listings)"),
        "superseded_by_restatement": ("n/a", "withheld: the company later restated this year's figures and the quarter on file is the superseded one"),
        "units_suspect":             ("n/a", "reported EPS and consensus look like different units in the vendor's calendar"),
        "no_prior_period":           ("n/a", "no year-ago quarter on file"),
    }
    if value is None and reason in why:
        c["display"], expl = why[reason]
        c["note"] = f"{note} — {c['display']}: {expl}"
    elif value is not None and restated:
        c["pass"] = None                           # shown, not scored
        c["display"] = f"{value:.1f}% ⚠"
        c["note"] = note + " — ⚠ compares across a restatement (two accounting bases); not scored"
    return c


def _eps_units_suspect(actual: float | None, estimate: float | None) -> bool:
    """True when reported EPS and consensus look like different units.

    Only same-sign pairs are judged: a genuine miss into a loss keeps opposite
    signs and is left alone. Estimates below 0.05 are skipped — near zero, real
    surprises of 4x are ordinary.
    """
    if actual is None or estimate is None or abs(estimate) < 0.05:
        return False
    r = actual / estimate
    return r > 0 and not (0.25 <= r <= 4.0)


def _fiscal_quarter_label(d: Any, fy_end_month: int | None) -> str:
    """Fiscal quarter for an earnings announcement, from the company's OWN
    fiscal calendar.

    The quarter a company reports is the most recent fiscal quarter that ENDED
    before the announcement. Deriving it from the announcement month alone
    silently assumes a December year end. Broadcom's FY ends in early November,
    so it announces in Mar/Jun/Sep/Dec — and the old month-bucket mapping sent
    every one of those to the `else` branch, printing four different quarters
    all as "Q3 2026". The same collapse hit every off-calendar filer (AAPL,
    MSFT, NVDA, ORCL, COST...).

    Returns a plain "Mon YYYY" label when the fiscal year end is unknown or the
    result looks implausible — a vague label beats a confidently wrong one.
    """
    import calendar as _cal
    if not fy_end_month:
        return d.strftime("%b %Y")
    # Fiscal quarters end at fy_end_month - 9, -6, -3, 0 (mod 12).
    qe_months = {((fy_end_month - 9 + 3 * i - 1) % 12) + 1 for i in range(4)}
    y, m = d.year, d.month
    for _ in range(6):                       # walk back at most two quarters
        # Anchor on mid-month: fiscal quarters end within a few days of a month
        # boundary, so the 15th separates "already ended" from "not yet" without
        # needing the exact fiscal end date.
        if m in qe_months and (y, m, 15) < (d.year, d.month, d.day):
            from datetime import date as _date
            gap = (d.date() - _date(y, m, 15)).days
            if gap > 120:                    # too stale to be this quarter's report
                return d.strftime("%b %Y")
            q = ((m - fy_end_month + 9) % 12) // 3 + 1
            fy = y if m <= fy_end_month else y + 1
            return f"Q{q} {fy}"
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return d.strftime("%b %Y")


def _fetch_earnings_data(t: Any, info: dict) -> dict | None:
    """Pull quarterly earnings history and key valuation metrics from yfinance."""
    from datetime import datetime, timezone

    trailing_pe  = _s(info.get("trailingPE"))
    forward_pe   = _s(info.get("forwardPE"))
    trailing_eps = _s(info.get("trailingEps"))
    forward_eps  = _s(info.get("forwardEps"))
    peg          = _s(info.get("pegRatio"))
    eg_yoy       = _pct(info.get("earningsGrowth"))
    rev_g_yoy    = _pct(info.get("revenueGrowth"))

    # Next earnings date
    next_date = None
    days_to_next = None
    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            now = datetime.now(timezone.utc)
            future = ed[ed["Reported EPS"].isna()]
            if not future.empty:
                # DataFrame sorted newest-first; last row in future = soonest upcoming
                next_dt = future.index[-1]
                ts = next_dt.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                next_date = ts.strftime("%b %d, %Y")
                days_to_next = max((ts - now).days, 0)
    except Exception:
        pass

    # Quarterly history
    quarters: list[dict] = []
    beat_count = 0
    miss_count = 0

    # Fiscal year end drives every quarter label below. `info` already carries
    # it, so this costs no extra request.
    _fy_end_month_v = _fy_end_month(info)

    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            reported = ed[ed["Reported EPS"].notna()].head(8)

            # Revenue from quarterly financials (columns = period-end dates, newest first)
            rev_vals: list[float | None] = []
            try:
                qf = t.quarterly_financials
                if qf is not None and not qf.empty and "Total Revenue" in qf.index:
                    rev_vals = [
                        float(v) if v == v else None  # NaN check
                        for v in qf.loc["Total Revenue"].values
                    ]
            except Exception:
                pass

            for i, (dt, row) in enumerate(reported.iterrows()):
                eps_est = _s(row.get("EPS Estimate"))
                eps_act = _s(row.get("Reported EPS"))
                surp    = _s(row.get("Surprise(%)"))
                # Cast numpy types to native Python to avoid JSON serialization errors
                if eps_est is not None:
                    eps_est = float(eps_est)
                if eps_act is not None:
                    eps_act = float(eps_act)
                if surp is not None:
                    surp = float(surp)
                # Units check. Yahoo's earnings calendar sometimes puts the reported
                # EPS and the consensus in different units for foreign listings:
                # SK hynix 90.62 vs 4.87, Alibaba ~1/9 of consensus (per ordinary
                # share vs per ADS), HSBC ~1/30. A same-sign result more than 4x or
                # under 1/4 of consensus is far likelier a unit error than a real
                # surprise, so it is shown but excluded from beat/miss and growth.
                units_suspect = _eps_units_suspect(eps_act, eps_est)
                if units_suspect:
                    beat = None
                    surp = None
                # Three states. Strict `>` made an IN-LINE print count as a miss,
                # which both understated the beat rate and coloured the row red.
                elif eps_est is None or eps_act is None:
                    beat = None
                elif eps_act > eps_est:
                    beat = True
                elif eps_act < eps_est:
                    beat = False
                else:
                    beat = None                    # exactly in line

                qlabel = _fiscal_quarter_label(dt, _fy_end_month_v)

                rev = rev_vals[i] if i < len(rev_vals) else None
                if beat is True:
                    beat_count += 1
                elif beat is False:
                    miss_count += 1

                quarters.append({
                    "label":        qlabel,
                    "date":         dt.strftime("%b %d, %Y"),
                    "eps_est":      round(eps_est, 2) if eps_est is not None else None,
                    "eps_act":      round(eps_act, 2) if eps_act is not None else None,
                    "surprise_pct": round(surp, 1)    if surp    is not None else None,
                    "beat":         beat,
                    "units_suspect": units_suspect,
                    "revenue":      round(rev)         if rev     is not None else None,
                })
    except Exception:
        pass

    return {
        "next_earnings_date": next_date,
        "days_to_next":       days_to_next,
        "eps_trailing":  round(trailing_eps, 2) if trailing_eps is not None else None,
        "eps_forward":   round(forward_eps,  2) if forward_eps  is not None else None,
        "trailing_pe":   round(trailing_pe,  1) if trailing_pe  is not None else None,
        "forward_pe":    round(forward_pe,   1) if forward_pe   is not None else None,
        "peg_ratio":     round(peg, 2)          if peg          is not None else None,
        "eg_yoy":        eg_yoy,
        "rev_growth_yoy": rev_g_yoy,
        "beat_count":    beat_count,
        "miss_count":    miss_count,
        "quarters":      quarters,
    }


# ── Valuation vs. growth analysis ────────────────────────────────────────────

# Long-run "typical" trailing P/E benchmarks. Industry substrings are checked
# first (more specific), then the sector fallback. Sourced from multi-decade
# US-market sector medians (Damodaran-style norms), rounded to whole numbers.
_INDUSTRY_PE: dict[str, float] = {
    # ── fine-grained industry overrides ──
    "bank":                 11.0,
    "insurance":            12.0,
    "capital markets":      15.0,
    "asset management":     14.0,
    "credit services":      12.0,
    "software":             34.0,
    "semiconductor":        26.0,
    "internet":             28.0,
    "biotechnology":        22.0,
    "drug manufacturers":   18.0,
    "medical device":       28.0,
    "healthcare plans":     16.0,
    "aerospace":            22.0,
    "airlines":             10.0,
    "oil & gas":            12.0,
    "utilities":            18.0,
    "reit":                 30.0,
    "real estate":          28.0,
    "auto manufacturers":   12.0,
    "retail":               20.0,
    "restaurants":          24.0,
    "beverages":            23.0,
    "tobacco":              12.0,
    "gold":                 16.0,
    "steel":                12.0,
    "chemicals":            17.0,
}
_SECTOR_PE: dict[str, float] = {
    "technology":             30.0,
    "communication services": 21.0,
    "consumer cyclical":      22.0,
    "consumer defensive":     21.0,
    "healthcare":             24.0,
    "financial services":     13.0,
    "financial":              13.0,
    "industrials":            20.0,
    "energy":                 12.0,
    "utilities":              18.0,
    "real estate":            28.0,
    "basic materials":        15.0,
}


# Representative liquid peer baskets, used to compute a *live* median P/E.
# Industry substrings checked first (tighter comparable), then sector.
_INDUSTRY_PEERS: dict[str, list[str]] = {
    "bank":               ["JPM", "BAC", "WFC", "C", "USB", "PNC", "TFC", "MTB"],
    "insurance":          ["PGR", "TRV", "ALL", "AIG", "MET", "PRU", "AFL", "HIG"],
    "capital markets":    ["GS", "MS", "SCHW", "BLK", "BX", "KKR", "TROW"],
    "asset management":   ["BLK", "BX", "KKR", "TROW", "AMP", "BEN", "IVZ"],
    "credit services":    ["V", "MA", "AXP", "COF", "DFS", "SYF", "PYPL"],
    "semiconductor":      ["NVDA", "AMD", "INTC", "TXN", "QCOM", "AVGO", "MU", "AMAT"],
    "software":           ["MSFT", "ORCL", "CRM", "ADBE", "NOW", "INTU", "PANW", "SNPS"],
    "internet":           ["GOOGL", "META", "NFLX", "SPOT", "PINS", "SNAP"],
    "biotechnology":      ["AMGN", "GILD", "VRTX", "REGN", "BIIB", "MRNA"],
    "drug manufacturers": ["LLY", "JNJ", "MRK", "PFE", "ABBV", "BMY", "AMGN", "GILD"],
    "medical device":     ["ABT", "MDT", "SYK", "BSX", "ISRG", "BDX", "EW"],
    "healthcare plans":   ["UNH", "ELV", "CI", "CVS", "HUM", "CNC"],
    "aerospace":          ["BA", "LMT", "RTX", "GD", "NOC", "GE", "HON"],
    "airlines":           ["DAL", "UAL", "AAL", "LUV", "ALK"],
    "oil & gas":          ["XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC"],
    "utilities":          ["NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL"],
    "reit":               ["PLD", "AMT", "EQIX", "SPG", "O", "PSA", "WELL"],
    "real estate":        ["PLD", "AMT", "EQIX", "SPG", "O", "PSA", "WELL"],
    "auto manufacturers": ["TSLA", "GM", "F", "TM", "STLA"],
    "restaurants":        ["MCD", "SBUX", "CMG", "YUM", "DPZ"],
    "beverages":          ["KO", "PEP", "MNST", "KDP", "STZ"],
    "tobacco":            ["PM", "MO", "BTI"],
    "gold":               ["NEM", "GOLD", "AEM", "KGC"],
    "steel":              ["NUE", "STLD", "X", "CLF", "RS"],
    "chemicals":          ["LIN", "SHW", "APD", "ECL", "DD"],
    "retail":             ["WMT", "COST", "TGT", "HD", "LOW", "TJX"],
}
_SECTOR_PEERS: dict[str, list[str]] = {
    "technology":             ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO"],
    "communication services": ["GOOGL", "META", "NFLX", "DIS", "VZ", "TMUS", "CMCSA"],
    "consumer cyclical":      ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX"],
    "consumer defensive":     ["WMT", "PG", "KO", "COST", "PEP", "PM", "MDLZ"],
    "healthcare":             ["LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "TMO", "ABT"],
    "financial services":     ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS"],
    "financial":              ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS"],
    "industrials":            ["GE", "CAT", "RTX", "HON", "UNP", "BA", "LMT"],
    "energy":                 ["XOM", "CVX", "COP", "EOG", "SLB", "PSX"],
    "utilities":              ["NEE", "DUK", "SO", "D", "AEP", "EXC"],
    "real estate":            ["PLD", "AMT", "EQIX", "SPG", "O", "PSA"],
    "basic materials":        ["LIN", "SHW", "FCX", "APD", "NEM", "ECL"],
}

_PEER_PE_CACHE: dict[frozenset, tuple[float, dict]] = {}
_PEER_PE_TTL = 900   # 15 minutes


def _batch_trailing_pe(symbols: list[str]) -> dict[str, float]:
    """One crumb-authenticated Yahoo quote request → {symbol: trailingPE}.
    Cached 15 min per symbol-set. Returns {} on any failure."""
    key = frozenset(symbols)
    hit = _PEER_PE_CACHE.get(key)
    import time as _t
    if hit and _t.time() - hit[0] < _PEER_PE_TTL:
        return hit[1]
    out: dict[str, float] = {}
    try:
        from yfinance.data import YfData
        yd = YfData()
        r = yd.get("https://query2.finance.yahoo.com/v7/finance/quote",
                   params={"symbols": ",".join(symbols)})
        for x in r.json().get("quoteResponse", {}).get("result", []):
            pe = x.get("trailingPE")
            sym = x.get("symbol")
            if sym and pe and 0 < float(pe) < 200:   # drop nonsense multiples
                out[sym] = round(float(pe), 1)
    except Exception:
        return {}
    _PEER_PE_CACHE[key] = (_t.time(), out)
    return out


def _peer_median_pe(sector: str | None, industry: str | None,
                    self_ticker: str) -> dict | None:
    """Live median trailing P/E of a representative peer basket (industry first,
    sector fallback). Excludes the stock itself. Returns None if unavailable."""
    import statistics
    ind = (industry or "").lower()
    basket = None
    basis = None
    for key, syms in _INDUSTRY_PEERS.items():
        if key in ind:
            basket, basis = syms, industry or key.title()
            break
    if basket is None:
        sec = (sector or "").lower()
        for key, syms in _SECTOR_PEERS.items():
            if key in sec:
                basket, basis = syms, sector or key.title()
                break
    if not basket:
        return None

    pes = _batch_trailing_pe(basket)
    # exclude self so the comparison is stock-vs-peers
    peer_pes = {s: v for s, v in pes.items() if s.upper() != self_ticker.upper()}
    if len(peer_pes) < 3:      # too few live quotes → not trustworthy
        return None
    vals = sorted(peer_pes.values())
    return {
        "median":  round(statistics.median(vals), 1),
        "n":       len(vals),
        "basis":   basis,
        "peers":   [{"sym": s, "pe": v} for s, v in
                    sorted(peer_pes.items(), key=lambda kv: kv[1])],
    }


def _typical_pe(sector: str | None, industry: str | None) -> tuple[float | None, str]:
    """Return (typical P/E, label) for the stock's industry, falling back to sector."""
    ind = (industry or "").lower()
    for key, val in _INDUSTRY_PE.items():
        if key in ind:
            return val, industry or key.title()
    sec = (sector or "").lower()
    for key, val in _SECTOR_PE.items():
        if key in sec:
            return val, sector or key.title()
    return None, sector or industry or "—"


def _annual_eps_series(t: Any) -> list[dict]:  # noqa: ANN001
    """Annual diluted EPS from income_stmt, oldest→newest. Falls back to NI/shares."""
    try:
        fin = t.income_stmt
    except Exception:
        return []
    if fin is None or getattr(fin, "empty", True):
        return []

    eps_row = None
    for key in ("Diluted EPS", "Basic EPS"):
        if key in fin.index:
            eps_row = fin.loc[key]
            break

    # Fallback: Net Income / Diluted Average Shares
    ni_row = shr_row = None
    if eps_row is None or eps_row.isna().all():
        for key in ("Net Income Common Stockholders", "Net Income"):
            if key in fin.index:
                ni_row = fin.loc[key]
                break
        if "Diluted Average Shares" in fin.index:
            shr_row = fin.loc["Diluted Average Shares"]

    out: list[dict] = []
    for col in fin.columns:
        eps: float | None = None
        if eps_row is not None:
            v = eps_row.get(col)
            if v is not None and v == v:
                eps = float(v)
        if eps is None and ni_row is not None and shr_row is not None:
            ni, sh = ni_row.get(col), shr_row.get(col)
            if ni is not None and sh not in (None, 0) and ni == ni and sh == sh and sh != 0:
                eps = float(ni) / float(sh)
        if eps is not None:
            out.append({"year": int(col.year), "eps": round(eps, 2), "_date": str(col.date())})
    out.sort(key=lambda x: x["year"])
    return out


def _cagr(first: float, last: float, years: int) -> float | None:
    """CAGR %, or None if signs make it meaningless (e.g. crossed through zero)."""
    if years <= 0 or first is None or last is None:
        return None
    if first <= 0 or last <= 0:
        return None
    return round(((last / first) ** (1.0 / years) - 1.0) * 100, 1)


def _price_on_or_before(price_history: list[dict], date_str: str) -> float | None:
    """Latest close on/before date_str. price_history is ascending [{d,c}, …]."""
    best = None
    for p in price_history:
        if p["d"] <= date_str:
            best = p["c"]
        else:
            break
    return best


def _valuation_growth_analysis(
    t: Any, info: dict, price_history: list[dict], *, is_etf: bool = False,  # noqa: ANN001
) -> dict | None:
    """
    Four analytics the user asked for:
      1. earnings_growth — EPS growth rate (YoY, 3Y & 5Y CAGR) from annual EPS
      2. pe_growth       — historical P/E multiple series + its CAGR (re-rating)
      3. peg / pegy      — P/E vs earnings growth, and vs earnings growth + dividend
      4. industry_pe     — stock P/E vs long-run typical P/E for its industry
    Gracefully degrades for ETFs (no per-share earnings history).
    """
    trailing_pe = _s(info.get("trailingPE"))
    forward_pe  = _s(info.get("forwardPE"))
    trailing_pe = float(trailing_pe) if trailing_pe is not None else None
    forward_pe  = float(forward_pe)  if forward_pe  is not None else None
    sector      = _s(info.get("sector"))
    industry    = _s(info.get("category")) if is_etf else _s(info.get("industry"))

    # dividend yield as % — trailingAnnualDividendYield is a clean decimal
    # fraction; info["dividendYield"] is unreliable (sometimes already a %).
    div_yield_pct: float | None = None
    tady = _s(info.get("trailingAnnualDividendYield"))
    if tady is not None:
        dy = float(tady) * 100
        div_yield_pct = round(dy, 2) if 0 <= dy <= 30 else None
    if div_yield_pct is None:
        dy2 = _s(info.get("dividendYield"))
        if dy2 is not None:
            dy2 = float(dy2)
            dy2 = dy2 if dy2 < 30 else dy2 / 100   # some feeds return basis-pointy values
            div_yield_pct = round(dy2, 2) if 0 <= dy2 <= 30 else None

    # ── 1. Earnings growth ────────────────────────────────────────────────────
    eps_series = [] if is_etf else _annual_eps_series(t)
    eps_yoy = _pct(info.get("earningsGrowth"))
    eps_cagr_3y = eps_cagr_5y = None
    if len(eps_series) >= 2:
        vals = eps_series
        if len(vals) >= 4:
            eps_cagr_3y = _cagr(vals[-4]["eps"], vals[-1]["eps"], 3)
        if len(vals) >= 5:
            eps_cagr_5y = _cagr(vals[-5]["eps"], vals[-1]["eps"], 4)
        elif len(vals) >= 2 and eps_cagr_3y is None:
            eps_cagr_3y = _cagr(vals[0]["eps"], vals[-1]["eps"], len(vals) - 1)

    # Growth figure used for PEG: prefer 3Y CAGR (smoother), fall back to YoY
    growth_for_peg = eps_cagr_3y if eps_cagr_3y is not None else eps_yoy
    growth_basis = "3Y EPS CAGR" if eps_cagr_3y is not None else "TTM YoY"

    earnings_growth = {
        "eps_yoy":     eps_yoy,
        "eps_cagr_3y": eps_cagr_3y,
        "eps_cagr_5y": eps_cagr_5y,
        "eps_series":  [{"year": e["year"], "eps": e["eps"]} for e in eps_series],
        "growth_used": growth_for_peg,
        "growth_basis": growth_basis,
    }

    # ── 2. P/E multiple history & its growth rate ─────────────────────────────
    pe_series: list[dict] = []
    for e in eps_series:
        if e["eps"] and e["eps"] > 0:
            px = _price_on_or_before(price_history, e["_date"])
            if px:
                pe_series.append({"year": e["year"], "pe": round(px / e["eps"], 1)})
    pe_cagr = pe_avg = pe_rerating = None
    if len(pe_series) >= 2:
        pe_cagr = _cagr(pe_series[0]["pe"], pe_series[-1]["pe"], pe_series[-1]["year"] - pe_series[0]["year"])
        pe_avg = round(sum(p["pe"] for p in pe_series) / len(pe_series), 1)
        if trailing_pe and pe_avg:
            pe_rerating = round((trailing_pe / pe_avg - 1) * 100, 1)   # +% = re-rated up
    pe_growth = {
        "pe_series":   pe_series,
        "pe_cagr":     pe_cagr,
        "pe_avg":      pe_avg,
        "pe_current":  round(trailing_pe, 1) if trailing_pe else None,
        "pe_rerating": pe_rerating,
    }

    # ── 3. PEG and PEGY (earnings growth, and growth + dividend) ───────────────
    def _peg_verdict(x: float | None) -> str:
        if x is None:            return "unknown"
        if x < 0:                return "unknown"     # negative growth → PEG meaningless
        if x < 1.0:              return "cheap"
        if x < 1.5:              return "fair"
        if x < 2.0:              return "full"
        return "expensive"

    peg = pegy = None
    if trailing_pe and growth_for_peg and growth_for_peg > 0:
        peg = round(trailing_pe / growth_for_peg, 2)
        denom = growth_for_peg + (div_yield_pct or 0)
        if denom > 0:
            pegy = round(trailing_pe / denom, 2)

    peg_block = {
        "pe":            round(trailing_pe, 1) if trailing_pe else None,
        "growth_pct":    growth_for_peg,
        "growth_basis":  growth_basis,
        "div_yield_pct": div_yield_pct,
        "peg":           peg,
        "pegy":          pegy,
        "peg_verdict":   _peg_verdict(peg),
        "pegy_verdict":  _peg_verdict(pegy),
        # Lynch's rule of thumb, expressed for the UI
        "lynch_note": (
            "Fairly valued when P/E ≈ growth rate (PEG ≈ 1). "
            "Lynch's dividend-adjusted version (PEGY) rewards yield: "
            "(growth + yield) / P/E ≥ 1 is attractive."
        ),
    }

    # ── 4. Industry P/E: static long-run anchor + LIVE peer median ────────────
    def _prem_verdict(pct: float) -> str:
        if   pct <= -20: return "discount"
        elif pct <   10: return "inline"
        elif pct <   30: return "premium"
        return "rich"

    typ_pe, typ_label = _typical_pe(sector, industry)
    premium_pct = None
    ind_verdict = "unknown"
    if typ_pe and trailing_pe:
        premium_pct = round((trailing_pe / typ_pe - 1) * 100, 1)
        ind_verdict = _prem_verdict(premium_pct)

    # Live peer-basket median (ETFs skip — no single-stock peer set)
    peer = None
    if not is_etf:
        self_sym = _s(info.get("symbol")) or ""
        peer = _peer_median_pe(sector, industry, self_sym)
    peer_premium_pct = None
    peer_verdict = "unknown"
    if peer and trailing_pe and peer.get("median"):
        peer_premium_pct = round((trailing_pe / peer["median"] - 1) * 100, 1)
        peer_verdict = _prem_verdict(peer_premium_pct)

    # Headline verdict prefers the live peer median when we have it
    headline_verdict = peer_verdict if peer else ind_verdict

    industry_pe = {
        "sector":       sector,
        "industry":     industry,
        "label":        typ_label,
        "typical_pe":   typ_pe,
        "stock_pe":     round(trailing_pe, 1) if trailing_pe else None,
        "forward_pe":   round(forward_pe, 1)  if forward_pe  else None,
        "premium_pct":  premium_pct,
        "verdict":      ind_verdict,
        # live peer median
        "peer_median":      peer["median"] if peer else None,
        "peer_n":           peer["n"]      if peer else None,
        "peer_basis":       peer["basis"]  if peer else None,
        "peers":            peer["peers"]  if peer else None,
        "peer_premium_pct": peer_premium_pct,
        "peer_verdict":     peer_verdict,
        "headline_verdict": headline_verdict,
    }

    # Nothing meaningful to show?
    if not eps_series and trailing_pe is None and typ_pe is None:
        return None

    return {
        "earnings_growth": earnings_growth,
        "pe_growth":       pe_growth,
        "peg":             peg_block,
        "industry_pe":     industry_pe,
        "is_etf":          is_etf,
    }


def _safe_val_growth(t, info, price_history, *, is_etf=False):  # noqa: ANN001
    """Never let valuation analysis break a screen — degrade to None on error."""
    try:
        return _valuation_growth_analysis(t, info, price_history, is_etf=is_etf)
    except Exception:
        return None


# ── Debt structure ────────────────────────────────────────────────────────────

def _row_series(df: Any, *names: str) -> list[dict]:  # noqa: ANN001
    """[{year, val}] oldest→newest for the first matching row across all columns."""
    if df is None or getattr(df, "empty", True):
        return []
    for name in names:
        if name in df.index:
            out = []
            for col in df.columns:
                try:
                    v = float(df.loc[name, col])
                    if not math.isnan(v):
                        out.append({"year": int(col.year), "val": v})
                except (TypeError, ValueError):
                    pass
            out.sort(key=lambda x: x["year"])
            return out
    return []


def _debt_structure(info: dict, fin: Any, bs: Any, *, is_financial: bool) -> dict | None:  # noqa: ANN001
    """Maturity split, leverage & coverage, and a total-debt trend from the
    balance sheet. Returns None when there's essentially no debt data."""
    total_debt   = _row(bs, "Total Debt") or _s(info.get("totalDebt"))
    cash         = _row(bs, "Cash Cash Equivalents And Short Term Investments",
                        "Cash And Cash Equivalents") or _s(info.get("totalCash"))
    # Net debt is computed from the two figures shown beside it, NOT taken from
    # the vendor's "Net Debt" row. That row nets off cash and equivalents only
    # and ignores short-term investments, so it sat next to a cash figure that
    # includes them: Microsoft showed debt $56.8bn, cash $76.7bn and "net debt"
    # of +$19.4bn — when it has $19.8bn more cash than debt.
    net_debt     = None
    current_debt = _row(bs, "Current Debt And Capital Lease Obligation", "Current Debt")
    lt_debt      = _row(bs, "Long Term Debt And Capital Lease Obligation", "Long Term Debt")
    cap_leases   = _row(bs, "Capital Lease Obligations")
    total_assets = _row(bs, "Total Assets")
    total_liab   = _row(bs, "Total Liabilities Net Minority Interest")

    if total_debt is None and current_debt is None and lt_debt is None:
        return None

    # Fill total from parts (or vice-versa) when one side is missing
    if total_debt is None and (current_debt is not None or lt_debt is not None):
        total_debt = (current_debt or 0) + (lt_debt or 0)
    if net_debt is None and total_debt is not None and cash is not None:
        net_debt = total_debt - cash

    # Maturity split
    st_pct = lt_pct = None
    if total_debt and total_debt > 0:
        if current_debt is not None:
            st_pct = round(current_debt / total_debt * 100, 1)
        if lt_debt is not None:
            lt_pct = round(lt_debt / total_debt * 100, 1)
        # if only one side known, infer the other
        if st_pct is None and lt_pct is not None:
            st_pct = round(max(0.0, 100 - lt_pct), 1)
        if lt_pct is None and st_pct is not None:
            lt_pct = round(max(0.0, 100 - st_pct), 1)

    # Leverage & coverage
    ebitda   = _s(info.get("ebitda"))
    debt_ebitda = (total_debt / ebitda) if (total_debt and ebitda and ebitda > 0) else None

    ebit     = _row(fin, "EBIT", "Operating Income")
    int_exp  = _row(fin, "Interest Expense")
    int_exp  = abs(int_exp) if int_exp is not None else None
    interest_coverage = (ebit / int_exp) if (ebit is not None and int_exp) else None
    cost_of_debt = (int_exp / total_debt * 100) if (int_exp and total_debt) else None

    d2e = _s(info.get("debtToEquity"))            # yfinance reports as a percent
    d2e_ratio = round(d2e / 100, 2) if d2e is not None else None
    debt_to_assets = round(total_debt / total_assets * 100, 1) if (total_debt and total_assets) else None
    liab_to_assets = round(total_liab / total_assets * 100, 1) if (total_liab and total_assets) else None
    current_ratio  = _s(info.get("currentRatio"))
    quick_ratio    = _s(info.get("quickRatio"))

    # Trend (total debt / net debt across reported years)
    debt_series = [{"year": r["year"], "debt": round(r["val"])} for r in _row_series(bs, "Total Debt")]

    def _rnd(v):
        return round(v) if v is not None else None

    return {
        "total_debt":     _rnd(total_debt),
        "net_debt":       _rnd(net_debt),
        "cash":           _rnd(cash),
        "current_debt":   _rnd(current_debt),
        "long_term_debt": _rnd(lt_debt),
        "capital_leases": _rnd(cap_leases),
        "st_pct":         st_pct,
        "lt_pct":         lt_pct,
        "debt_to_equity":    d2e_ratio,
        "debt_to_assets":    debt_to_assets,
        "liab_to_assets":    liab_to_assets,
        "debt_ebitda":       round(debt_ebitda, 1) if debt_ebitda is not None else None,
        "interest_coverage": round(interest_coverage, 1) if interest_coverage is not None else None,
        "cost_of_debt":      round(cost_of_debt, 1) if cost_of_debt is not None else None,
        "current_ratio":     round(current_ratio, 2) if current_ratio is not None else None,
        "quick_ratio":       round(quick_ratio, 2) if quick_ratio is not None else None,
        "debt_series":       debt_series,
        "is_financial":      is_financial,
        "broader_debt":      _broader_debt(info, total_debt),
    }


def _broader_debt(info: dict, total_debt: float | None) -> dict | None:
    """The vendor's other "total debt", when it is a materially bigger number.

    Yahoo publishes two. The balance-sheet row this card is built on counts
    borrowings and operating leases; the summary field Gate 1's Debt / EBITDA
    uses also counts finance-lease liabilities. For most companies they agree.
    For Microsoft, whose data-centre finance leases are enormous, they are
    $56.8bn and $128.8bn for the same date — and a page that shows both under
    the same name owes the reader the reason.
    """
    wide = _s(info.get("totalDebt"))
    if not wide or not total_debt or wide <= total_debt * 1.25:
        return None
    return {"amount": round(wide), "gap": round(wide - total_debt)}


def _safe_debt_structure(info, fin, bs, *, is_financial=False):  # noqa: ANN001
    try:
        return _debt_structure(info, fin, bs, is_financial=is_financial)
    except Exception:
        return None


# ── Hidden assets & asset backing (Lynch "Asset Play" signals) ────────────────

def _hidden_assets(info: dict, bs: Any, market_cap: float | None) -> dict | None:  # noqa: ANN001
    """Systematic *signals* for hidden asset value — land/buildings at historical
    cost, heavily-depreciated PP&E, net cash, and book backing. NOT an appraisal:
    true market value of real estate lives in 10-K footnotes."""
    land      = _row(bs, "Land And Improvements")
    buildings = _row(bs, "Buildings And Improvements")
    machinery = _row(bs, "Machinery Furniture Equipment")
    inv_re    = _row(bs, "Other Properties", "Properties")
    net_ppe   = _row(bs, "Net PPE")
    gross_ppe = _row(bs, "Gross PPE")
    accum_dep = _row(bs, "Accumulated Depreciation")
    accum_dep = abs(accum_dep) if accum_dep is not None else None
    lt_inv    = _row(bs, "Investments And Advances", "Investmentin Financial Assets",
                     "Long Term Equity Investment")
    cash_st   = _row(bs, "Cash Cash Equivalents And Short Term Investments",
                     "Cash And Cash Equivalents") or _s(info.get("totalCash"))
    tang_book = _row(bs, "Tangible Book Value", "Net Tangible Assets")
    equity    = _row(bs, "Stockholders Equity", "Common Stock Equity")
    goodwill  = _row(bs, "Goodwill")
    intang    = _row(bs, "Other Intangible Assets", "Goodwill And Other Intangible Assets")
    total_debt = _s(info.get("totalDebt"))

    # nothing physical/relevant to show
    if net_ppe is None and gross_ppe is None and land is None and cash_st is None:
        return None

    dep_pct = round(accum_dep / gross_ppe * 100, 1) if (accum_dep and gross_ppe and gross_ppe > 0) else None

    net_cash = None
    if cash_st is not None:
        net_cash = cash_st - (total_debt or 0)
    net_cash_pct = round(net_cash / market_cap * 100, 1) if (net_cash is not None and market_cap) else None

    pb = _s(info.get("priceToBook"))
    p_tang = round(market_cap / tang_book, 2) if (market_cap and tang_book and tang_book > 0) else None

    goodwill_intang = None
    if goodwill is not None or intang is not None:
        goodwill_intang = (goodwill or 0) + (intang or 0) if not (goodwill is not None and intang is not None and goodwill == intang) else intang

    land_bld = None
    if land is not None or buildings is not None:
        land_bld = (land or 0) + (buildings or 0)
    land_bld_pct = round(land_bld / market_cap * 100, 1) if (land_bld and market_cap) else None

    # ── Asset-play signals ────────────────────────────────────────────────────
    signals: list[dict] = []
    if net_cash_pct is not None and net_cash_pct >= 25:
        signals.append({"kind": "cash",
                        "text": f"Net cash equals {net_cash_pct}% of market cap — a Lynch-style cash cushion the market may under-credit."})
    if dep_pct is not None and dep_pct >= 55:
        signals.append({"kind": "dep",
                        "text": f"Fixed assets are {dep_pct}% depreciated — book value likely sits well below replacement/market cost."})
    if pb is not None and 0 < pb < 1:
        signals.append({"kind": "book",
                        "text": f"Trades below book value (P/B {round(pb,2)}×) — market prices equity under its accounting net worth."})
    if land_bld_pct is not None and land_bld_pct >= 20:
        signals.append({"kind": "re",
                        "text": f"Land & buildings on the books (historical cost) equal {land_bld_pct}% of market cap — potential understated real estate."})

    def _rnd(v):
        return round(v) if v is not None else None

    return {
        "land":          _rnd(land),
        "buildings":     _rnd(buildings),
        "machinery":     _rnd(machinery),
        "investment_real_estate": _rnd(inv_re) if inv_re else None,
        "cip":           None,
        "net_ppe":       _rnd(net_ppe),
        "gross_ppe":     _rnd(gross_ppe),
        "accumulated_dep": _rnd(accum_dep),
        "dep_pct":       dep_pct,
        "lt_investments": _rnd(lt_inv),
        "cash_st_inv":   _rnd(cash_st),
        "total_debt":    _rnd(total_debt),
        "net_cash":      _rnd(net_cash),
        "net_cash_pct_mcap": net_cash_pct,
        "book_value":    _rnd(equity),
        "tangible_book": _rnd(tang_book),
        "market_cap":    _rnd(market_cap),
        "pb":            round(pb, 2) if pb is not None else None,
        "p_tangible_book": p_tang,
        "goodwill_intangibles": _rnd(goodwill_intang),
        "land_buildings_pct_mcap": land_bld_pct,
        "signals":       signals,
    }


def _safe_hidden_assets(info, bs, market_cap):  # noqa: ANN001
    try:
        return _hidden_assets(info, bs, market_cap)
    except Exception:
        return None


# ── Cash flow & profit margins (with trends) ──────────────────────────────────

def _cashflow_margins(fin: Any, cf: Any) -> dict | None:  # noqa: ANN001
    """Gross/operating/pre-tax/net + FCF margins and OCF/FCF levels, each with a
    multi-year trend. Margins in %, cash-flow levels in reporting currency."""
    rev_series = _row_series(fin, "Total Revenue")
    if not rev_series:
        return None
    rev = {r["year"]: r["val"] for r in rev_series if r["val"]}

    def _margin(*names) -> dict:
        prof = {r["year"]: r["val"] for r in _row_series(fin, *names)}
        series = [{"year": y, "margin": round(prof[y] / rev[y] * 100, 1)}
                  for y in sorted(rev) if y in prof and rev[y]]
        if not series:
            return {"latest": None, "delta_yoy": None, "delta_period": None, "series": []}
        latest = series[-1]["margin"]
        delta_yoy = round(latest - series[-2]["margin"], 1) if len(series) >= 2 else None
        delta_period = round(latest - series[0]["margin"], 1) if len(series) >= 2 else None
        return {"latest": latest, "delta_yoy": delta_yoy, "delta_period": delta_period, "series": series}

    gross    = _margin("Gross Profit")
    operating = _margin("Total Operating Income As Reported", "Operating Income")   # as filed; see rev_profit
    pretax   = _margin("Pretax Income")
    net      = _margin("Net Income", "Net Income From Continuing Operation Net Minority Interest")

    # ── Cash flow levels ──────────────────────────────────────────────────────
    ocf_s = _row_series(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    fcf_s = _row_series(cf, "Free Cash Flow")
    capex_s = _row_series(cf, "Capital Expenditure")
    ocf_map = {r["year"]: r["val"] for r in ocf_s}
    capex_map = {r["year"]: abs(r["val"]) for r in capex_s}

    # Build FCF series (prefer direct; else OCF - capex)
    if fcf_s:
        fcf_map = {r["year"]: r["val"] for r in fcf_s}
    else:
        fcf_map = {y: ocf_map[y] - capex_map.get(y, 0) for y in ocf_map}

    years = sorted(set(ocf_map) | set(fcf_map))
    ocf_series   = [{"year": y, "val": round(ocf_map[y])} for y in years if y in ocf_map]
    fcf_series   = [{"year": y, "val": round(fcf_map[y])} for y in years if y in fcf_map]
    capex_series = [{"year": y, "val": round(capex_map[y])} for y in sorted(capex_map)]

    # FCF margin trend (fcf / revenue)
    fcf_margin_series = [{"year": y, "margin": round(fcf_map[y] / rev[y] * 100, 1)}
                         for y in years if y in fcf_map and y in rev and rev[y]]
    fcf_margin = {
        "latest": fcf_margin_series[-1]["margin"] if fcf_margin_series else None,
        "delta_yoy": (round(fcf_margin_series[-1]["margin"] - fcf_margin_series[-2]["margin"], 1)
                      if len(fcf_margin_series) >= 2 else None),
        "delta_period": (round(fcf_margin_series[-1]["margin"] - fcf_margin_series[0]["margin"], 1)
                         if len(fcf_margin_series) >= 2 else None),
        "series": fcf_margin_series,
    }

    def _growth(series):
        if len(series) >= 2 and series[0]["val"] > 0 and series[-1]["val"] > 0:
            return _cagr(series[0]["val"], series[-1]["val"], series[-1]["year"] - series[0]["year"])
        return None

    if all(m["latest"] is None for m in (gross, operating, pretax, net)) and not ocf_series:
        return None

    return {
        "margins": {
            "gross": gross, "operating": operating, "pretax": pretax,
            "net": net, "fcf": fcf_margin,
        },
        "cashflow": {
            "ocf_latest":   ocf_series[-1]["val"] if ocf_series else None,
            "fcf_latest":   fcf_series[-1]["val"] if fcf_series else None,
            "capex_latest": capex_series[-1]["val"] if capex_series else None,
            "ocf_cagr":     _growth(ocf_series),
            "fcf_cagr":     _growth(fcf_series),
            "ocf_series":   ocf_series,
            "fcf_series":   fcf_series,
            "capex_series": capex_series,
        },
    }


def _safe_cashflow_margins(fin, cf):  # noqa: ANN001
    try:
        return _cashflow_margins(fin, cf)
    except Exception:
        return None


# ── Tier-1 fundamental scores (F-Score, Altman Z, Beneish M, Graham, Magic) ────



_BENCH_CACHE: dict[str, Any] = {"ts": 0.0, "rows": []}
_BENCH_TTL = 6 * 3600
BENCHMARK_SYMBOL = "SPY"
BENCHMARK_NAME = "S&P 500 (SPY)"




def _top_down_for(symbol: str):
    """Weinstein top-down context: market stage -> industry-group stage.
    Same memoised maps the screener uses, so the surfaces cannot disagree."""
    try:
        from .stage_analysis import top_down_for
        return top_down_for("us", symbol)
    except Exception:
        return None


def _stage_analysis_for(symbol: str):
    """Weinstein Stage Analysis for the detail page — see the CN twin."""
    try:
        from .stage_analysis import stage_for
        return stage_for("us", symbol)
    except Exception:
        return None


def _benchmark_history() -> list[dict]:
    """SPY daily closes for the chart's relative-strength panel (price / SPY).

    Cached process-wide with a 6h TTL: the series is IDENTICAL for every stock,
    so re-fetching it on each detail view would be pure waste — and this
    codebase has already been bitten twice by avoidable per-stock yfinance
    call volume tripping Yahoo's rate limiter.
    """
    import time as _t
    now = _t.time()
    if _BENCH_CACHE["rows"] and now - _BENCH_CACHE["ts"] < _BENCH_TTL:
        return _BENCH_CACHE["rows"]
    try:
        import yfinance as yf
        h = yf.Ticker(BENCHMARK_SYMBOL).history(period="5y")
        rows = [{"d": str(i.date()), "c": round(float(r["Close"]), 2)}
                for i, r in h.iterrows() if r["Close"] == r["Close"]]
        if rows:
            _BENCH_CACHE["rows"], _BENCH_CACHE["ts"] = rows, now
    except Exception:
        pass          # stale/empty benchmark just hides the RS panel, never breaks the chart
    return _BENCH_CACHE["rows"]


def _ohlc_val(v: Any, digits: int = 2) -> float | None:  # noqa: ANN401
    """OHLCV field extractor for the per-stock price_history payload.

    yfinance's .history() and Tushare's `daily` BOTH already return full OHLCV
    in the calls this codebase was already making — only Close was being kept.
    Widening the payload therefore costs zero extra API requests. `d` and `c`
    keep their exact prior shape so existing consumers are unaffected.
    """
    try:
        f = float(v)
        if f != f:            # NaN
            return None
        return round(f, digits) if digits else round(f)
    except (TypeError, ValueError):
        return None


def _row_at(df: Any, idx: int, *names: str) -> float | None:  # noqa: ANN001
    """Value of the first matching row at column `idx` (0 = latest year)."""
    if df is None or getattr(df, "empty", True) or idx >= len(df.columns):
        return None
    col = df.columns[idx]
    for name in names:
        if name in df.index:
            try:
                v = float(df.loc[name, col])
                if not math.isnan(v):
                    return v
            except (TypeError, ValueError):
                pass
    return None


def _fundamental_scores(info: dict, fin: Any, bs: Any, cf: Any,  # noqa: ANN001
                        market_cap: float | None, price: float | None,
                        ev: float | None, *, is_financial: bool) -> dict | None:
    """Piotroski F-Score, Altman Z, Beneish M, Graham Number/margin-of-safety,
    Magic-Formula components, FCF yield & EV/EBIT — all from statements already
    fetched. Each degrades to None/unavailable when inputs are missing."""

    def A(idx, *n):  # income statement
        return _row_at(fin, idx, *n)
    def B(idx, *n):  # balance sheet
        return _row_at(bs, idx, *n)
    def C(idx, *n):  # cash flow
        return _row_at(cf, idx, *n)

    # ── Piotroski F-Score (0–9) ──────────────────────────────────────────────
    piotroski = None
    try:
        ni0, ni1 = A(0, "Net Income", "Net Income From Continuing Operation Net Minority Interest"), A(1, "Net Income", "Net Income From Continuing Operation Net Minority Interest")
        ta0, ta1 = B(0, "Total Assets"), B(1, "Total Assets")
        ocf0 = C(0, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
        roa0 = ni0 / ta0 if (ni0 is not None and ta0) else None
        roa1 = ni1 / ta1 if (ni1 is not None and ta1) else None
        ltd0, ltd1 = B(0, "Long Term Debt") or 0, B(1, "Long Term Debt") or 0
        ca0, cl0 = B(0, "Current Assets"), B(0, "Current Liabilities")
        ca1, cl1 = B(1, "Current Assets"), B(1, "Current Liabilities")
        cr0 = ca0 / cl0 if (ca0 and cl0) else None
        cr1 = ca1 / cl1 if (ca1 and cl1) else None
        sh0 = B(0, "Share Issued", "Ordinary Shares Number")
        sh1 = B(1, "Share Issued", "Ordinary Shares Number")
        gp0, gp1 = A(0, "Gross Profit"), A(1, "Gross Profit")
        rev0, rev1 = A(0, "Total Revenue"), A(1, "Total Revenue")
        gm0 = gp0 / rev0 if (gp0 is not None and rev0) else None
        gm1 = gp1 / rev1 if (gp1 is not None and rev1) else None
        at0 = rev0 / ta0 if (rev0 and ta0) else None
        at1 = rev1 / ta1 if (rev1 and ta1) else None
        ltda0 = ltd0 / ta0 if ta0 else None
        ltda1 = ltd1 / ta1 if ta1 else None

        # Each test's inputs are checked for None SEPARATELY from the comparison, so
        # "criterion genuinely failed" and "criterion couldn't be evaluated (missing
        # data)" are distinguishable — not both silently collapsed into "fail" as
        # before (confirmed bug: e.g. a recent-IPO with no prior-year comparison
        # would score 0 on every YoY test, indistinguishable from a company that
        # genuinely failed all of them). Piotroski's own paper defaults unscoreable
        # criteria to 0 (source-faithful convention) — that's preserved in `score`,
        # but `unscoreable` now surfaces how many were actually undetermined rather
        # than hiding it. Fixed 2026-08-10.
        tests = [
            ("Positive return on assets", roa0 is None, roa0 is not None and roa0 > 0,
             "Company is profitable on its asset base (ROA > 0)."),
            ("Positive operating cash flow", ocf0 is None, ocf0 is not None and ocf0 > 0,
             "Core operations generate real cash."),
            ("Rising ROA", roa0 is None or roa1 is None, roa0 is not None and roa1 is not None and roa0 > roa1,
             "Profitability improved vs. last year."),
            ("Earnings backed by cash", ocf0 is None or ni0 is None, ocf0 is not None and ni0 is not None and ocf0 > ni0,
             "Operating cash flow exceeds net income — low accruals, high-quality earnings."),
            ("Lower leverage", ltda0 is None or ltda1 is None, ltda0 is not None and ltda1 is not None and ltda0 <= ltda1,
             "Long-term-debt-to-assets fell (or stayed at zero)."),
            ("Higher current ratio", cr0 is None or cr1 is None, cr0 is not None and cr1 is not None and cr0 > cr1,
             "Short-term liquidity improved."),
            ("No share dilution", sh0 is None or sh1 is None, sh0 is not None and sh1 is not None and sh0 <= sh1,
             "Shares outstanding didn't rise — no dilution of your ownership."),
            ("Higher gross margin", gm0 is None or gm1 is None, gm0 is not None and gm1 is not None and gm0 > gm1,
             "Pricing power / cost efficiency improved."),
            ("Higher asset turnover", at0 is None or at1 is None, at0 is not None and at1 is not None and at0 > at1,
             "Generating more revenue per dollar of assets."),
        ]
        score = sum(1 for _, _, p, _ in tests if p)
        unscoreable = sum(1 for _, missing, _, _ in tests if missing)
        verdict = "strong" if score >= 8 else "moderate" if score >= 5 else "weak"
        piotroski = {
            "score": score, "max": 9, "unscoreable": unscoreable, "verdict": verdict,
            "components": [{"name": n, "pass": bool(p), "unscoreable": missing, "detail": d}
                            for n, missing, p, d in tests],
        }
    except Exception:
        piotroski = None

    # ── Altman Z-Score ───────────────────────────────────────────────────────
    altman = None
    try:
        ta = B(0, "Total Assets")
        ca, cl = B(0, "Current Assets"), B(0, "Current Liabilities")
        wc = (ca - cl) if (ca is not None and cl is not None) else None
        re = B(0, "Retained Earnings")
        ebit = A(0, "EBIT", "Operating Income")
        tl = B(0, "Total Liabilities Net Minority Interest")
        sales = A(0, "Total Revenue")
        mve = market_cap
        if is_financial:
            altman = {"applicable": False,
                      "note": "Altman Z is calibrated for non-financial firms; a bank's balance sheet makes it meaningless."}
        elif all(v is not None for v in (ta, wc, re, ebit, tl, sales, mve)) and ta and tl:
            x1, x2, x3, x4, x5 = wc/ta, re/ta, ebit/ta, mve/tl, sales/ta
            z = 1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5
            zone = "safe" if z > 2.99 else "grey" if z >= 1.81 else "distress"
            altman = {"applicable": True, "z": round(z, 2), "zone": zone,
                      "components": {"wc_ta": round(x1,3), "re_ta": round(x2,3),
                                     "ebit_ta": round(x3,3), "mve_tl": round(x4,3),
                                     "sales_ta": round(x5,3)}}
    except Exception:
        altman = None

    # ── Beneish M-Score (earnings-manipulation flag) ─────────────────────────
    beneish = None
    try:
        rec0, rec1 = B(0, "Receivables", "Accounts Receivable"), B(1, "Receivables", "Accounts Receivable")
        s0, s1 = A(0, "Total Revenue"), A(1, "Total Revenue")
        cogs0, cogs1 = A(0, "Cost Of Revenue", "Reconciled Cost Of Revenue"), A(1, "Cost Of Revenue", "Reconciled Cost Of Revenue")
        ca0b, ca1b = B(0, "Current Assets"), B(1, "Current Assets")
        ppe0, ppe1 = B(0, "Net PPE"), B(1, "Net PPE")
        ta0b, ta1b = B(0, "Total Assets"), B(1, "Total Assets")
        dep0 = C(0, "Depreciation And Amortization") or A(0, "Reconciled Depreciation")
        dep1 = C(1, "Depreciation And Amortization") or A(1, "Reconciled Depreciation")
        sga0, sga1 = A(0, "Selling General And Administration"), A(1, "Selling General And Administration")
        cl0b, cl1b = B(0, "Current Liabilities"), B(1, "Current Liabilities")
        ltd0b, ltd1b = B(0, "Long Term Debt") or 0, B(1, "Long Term Debt") or 0
        ni_cont0 = A(0, "Net Income Continuous Operations", "Net Income")
        ocf0b = C(0, "Operating Cash Flow")

        req = [rec0, rec1, s0, s1, cogs0, cogs1, ca0b, ca1b, ppe0, ppe1, ta0b, ta1b,
               dep0, dep1, sga0, sga1, cl0b, cl1b, ni_cont0, ocf0b]
        if all(v is not None for v in req) and s0 and s1 and ta0b and ta1b:
            dsri = (rec0/s0) / (rec1/s1)
            gm0b, gm1b = (s0-cogs0)/s0, (s1-cogs1)/s1
            gmi = gm1b / gm0b if gm0b else 1.0
            aqi = (1 - (ca0b+ppe0)/ta0b) / (1 - (ca1b+ppe1)/ta1b) if (ta1b and (1-(ca1b+ppe1)/ta1b) != 0) else 1.0
            sgi = s0 / s1
            depi = (dep1/(dep1+ppe1)) / (dep0/(dep0+ppe0)) if (dep0+ppe0) else 1.0
            sgai = (sga0/s0) / (sga1/s1)
            lvgi = ((ltd0b+cl0b)/ta0b) / ((ltd1b+cl1b)/ta1b)
            tata = (ni_cont0 - ocf0b) / ta0b
            m = (-4.84 + 0.92*dsri + 0.528*gmi + 0.404*aqi + 0.892*sgi
                 + 0.115*depi - 0.172*sgai + 4.679*tata - 0.327*lvgi)
            flag = m > -1.78
            beneish = {"available": True, "m": round(m, 2), "flag": bool(flag),
                       "verdict": "flag" if flag else "clean"}
        else:
            beneish = {"available": False}
    except Exception:
        beneish = {"available": False}

    # ── Graham Number & margin of safety ─────────────────────────────────────
    graham = None
    try:
        eps = _s(info.get("trailingEps"))
        equity = B(0, "Stockholders Equity", "Common Stock Equity")
        # Trading units (market cap ÷ price), NOT the balance sheet's ordinary
        # share count: EPS here is per ADR, so book value must be too. Alibaba
        # (1 ADS = 8 shares) would otherwise understate the Graham Number by √8.
        shares = _s(info.get("sharesOutstanding")) or B(0, "Share Issued", "Ordinary Shares Number")
        bvps = (equity / shares) if (equity and shares) else None
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

    # ── Magic Formula components + FCF yield + EV/EBIT ────────────────────────
    value = None
    try:
        ebit = A(0, "EBIT", "Operating Income")
        fcf = C(0, "Free Cash Flow")
        if fcf is None:
            ocf = C(0, "Operating Cash Flow")
            capex = C(0, "Capital Expenditure")
            fcf = (ocf - abs(capex)) if (ocf is not None and capex is not None) else None
        ca, cl = B(0, "Current Assets"), B(0, "Current Liabilities")
        nwc = (ca - cl) if (ca is not None and cl is not None) else None
        net_fixed = B(0, "Net PPE")
        roc = (ebit / (nwc + net_fixed) * 100) if (ebit is not None and nwc is not None and net_fixed and (nwc+net_fixed) > 0) else None
        earnings_yield = (ebit / ev * 100) if (ebit is not None and ev) else None
        # sanity: |EY| > 100% almost always means currency-mismatched statements
        # (e.g. foreign ADRs reporting in local currency vs. USD market cap)
        if earnings_yield is not None and not (-100 < earnings_yield < 100):
            earnings_yield = None
        fcf_yield = (fcf / market_cap * 100) if (fcf is not None and market_cap) else None
        ev_ebit = (ev / ebit) if (ev and ebit and ebit > 0) else None
        if ev_ebit is not None and (ev_ebit < 0 or ev_ebit > 500):
            ev_ebit = None
        value = {
            "earnings_yield": round(earnings_yield, 1) if earnings_yield is not None else None,
            "roc": round(roc, 1) if roc is not None else None,
            "fcf_yield": round(fcf_yield, 1) if fcf_yield is not None else None,
            "ev_ebit": round(ev_ebit, 1) if ev_ebit is not None else None,
        }
    except Exception:
        value = None

    if not any([piotroski, altman, beneish and beneish.get("available"), graham and graham.get("number"), value]):
        return None

    return {
        "piotroski": piotroski,
        "altman": altman,
        "beneish": beneish,
        "graham": graham,
        "value": value,
        "is_financial": is_financial,
    }


def _safe_fundamental_scores(info, fin, bs, cf, market_cap, price, ev, *, is_financial=False):  # noqa: ANN001
    try:
        return _fundamental_scores(info, fin, bs, cf, market_cap, price, ev, is_financial=is_financial)
    except Exception:
        return None


# ── DCF / reverse-DCF inputs (intrinsic value & economic profit) ──────────────
# Risk-free & equity-risk-premium defaults (editable in the UI).
_DCF_RF = 4.3
_DCF_ERP = 5.0


def _dcf_inputs(info: dict, fin: Any, bs: Any, cf: Any,  # noqa: ANN001
                market_cap: float | None, price: float | None,
                roic: float | None, *, is_financial: bool) -> dict | None:
    """Everything the client needs to run a two-stage FCFE DCF + reverse-DCF:
    base free cash flow, CAPM cost of equity, WACC, ROIC−WACC economic profit,
    owner earnings, and sensible default growth stages. The projection itself is
    done client-side so slider changes recompute instantly."""
    if is_financial:
        return {"applicable": False,
                "note": "A DCF on free cash flow isn't meaningful for banks/insurers (cash flows are the business). Use book value & ROE instead."}

    # ── free cash flow (normalized: mean of last 3 reported years) ─────────────
    fcf_series = _row_series(cf, "Free Cash Flow")
    if not fcf_series:
        ocf = {r["year"]: r["val"] for r in _row_series(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")}
        capex = {r["year"]: abs(r["val"]) for r in _row_series(cf, "Capital Expenditure")}
        fcf_series = [{"year": y, "val": ocf[y] - capex.get(y, 0)} for y in sorted(ocf)]
    if not fcf_series:
        return {"applicable": False, "note": "No cash-flow history available."}
    recent = [r["val"] for r in fcf_series[-3:]]
    base_fcf = sum(recent) / len(recent)

    shares = _s(info.get("sharesOutstanding"))

    # ── owner earnings (NI + D&A − capex) ─────────────────────────────────────
    ni = _row(fin, "Net Income", "Net Income From Continuing Operation Net Minority Interest")
    dep = _row(cf, "Depreciation And Amortization") or _row(fin, "Reconciled Depreciation")
    capex_latest = abs(_row(cf, "Capital Expenditure") or 0)
    owner_earnings = (ni + (dep or 0) - capex_latest) if ni is not None else None

    # ── CAPM cost of equity + WACC ────────────────────────────────────────────
    beta = _s(info.get("beta"))
    beta = float(beta) if beta is not None else 1.0
    coe = _DCF_RF + beta * _DCF_ERP
    total_debt = _s(info.get("totalDebt")) or 0
    int_exp = _row(fin, "Interest Expense")
    int_exp = abs(int_exp) if int_exp is not None else None
    tax_prov, pretax = _row(fin, "Tax Provision"), _row(fin, "Pretax Income")
    tax_rate = (tax_prov / pretax) if (tax_prov and pretax and pretax > 0) else 0.21
    tax_rate = min(max(tax_rate, 0.0), 0.35)
    cod = (int_exp / total_debt * 100) if (int_exp and total_debt) else None
    e, d = (market_cap or 0), total_debt
    if e + d > 0 and cod is not None:
        wacc = e / (e + d) * coe + d / (e + d) * cod * (1 - tax_rate)
    else:
        wacc = coe

    # ── default stage-1 growth (from TTM earnings growth, clamped) ─────────────
    g_default = _pct(info.get("earningsGrowth"))
    if g_default is None:
        g_default = _pct(info.get("revenueGrowth"))
    g_default = 8.0 if g_default is None else min(max(g_default, 0.0), 20.0)

    return {
        "applicable": bool(base_fcf > 0 and shares),
        "base_fcf": round(base_fcf),
        "fcf_series": [{"year": r["year"], "fcf": round(r["val"])} for r in fcf_series[-5:]],
        "shares": shares,
        "price": round(price, 2) if price else None,
        "market_cap": market_cap,
        "beta": round(beta, 2),
        "rf": _DCF_RF, "erp": _DCF_ERP,
        "cost_of_equity": round(coe, 1),
        "wacc": round(wacc, 1),
        "roic": round(roic, 1) if roic is not None else None,
        "econ_profit_spread": round(roic - wacc, 1) if roic is not None else None,
        "owner_earnings": round(owner_earnings) if owner_earnings is not None else None,
        "default_stage1_growth": round(g_default, 1),
        "default_fade_years": 10,
        "default_terminal": 2.5,
        "note": None if base_fcf > 0 else "Base free cash flow is negative — a DCF isn't meaningful here.",
    }


def _safe_dcf(info, fin, bs, cf, market_cap, price, roic, *, is_financial=False):  # noqa: ANN001
    try:
        return _dcf_inputs(info, fin, bs, cf, market_cap, price, roic, is_financial=is_financial)
    except Exception:
        return None


# ── Main entry point ──────────────────────────────────────────────────────────

def screen_ticker(ticker_str: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed. Run: pip install yfinance"}

    ticker_str = ticker_str.strip().upper()
    if not ticker_str:
        return {"error": "No ticker provided."}

    try:
        t = yf.Ticker(ticker_str)
        info = t.info or {}
    except Exception as exc:
        return {"error": f"Could not fetch data for '{ticker_str}': {exc}"}

    # Sanity-check: yfinance returns a minimal dict for unknown tickers
    price_check = _s(info.get("currentPrice") or info.get("regularMarketPrice")
                     or info.get("navPrice") or info.get("previousClose"))
    if not price_check:
        return {"error": f"No market data found for '{ticker_str}'. Check the symbol."}

    # Dispatch to ETF screener if applicable
    quote_type = (info.get("quoteType") or "").upper()
    if quote_type in ("ETF", "MUTUALFUND"):
        return screen_etf(ticker_str, t, info)

    # Trades in one currency, reports in another (SKHY: USD / KRW, TSM: USD /
    # TWD). Convert every statement into the trading currency HERE, once, so no
    # downstream card can divide won by dollars. See fx_normalize.py.
    from .fx_normalize import normalize as _fx_normalize
    t, info, listing_basis = _fx_normalize(t, info)
    fx_conversion = (listing_basis or {}).get("fx")

    # ── Basic company info ────────────────────────────────────────────────────
    name        = _s(info.get("longName") or info.get("shortName"), ticker_str)
    sector      = _s(info.get("sector"), "—")
    industry    = _s(info.get("industry"), "—")
    price       = _s(info.get("currentPrice") or info.get("regularMarketPrice"))
    market_cap  = _s(info.get("marketCap"))
    currency    = _s(info.get("currency"), "USD")
    website     = _s(info.get("website"), "")
    employees   = _s(info.get("fullTimeEmployees"))
    summary_raw = _s(info.get("longBusinessSummary"), "")
    description = (summary_raw[:350] + "…") if len(summary_raw) > 350 else summary_raw

    # ── Pull financial statements ─────────────────────────────────────────────
    try:
        fin = t.financials
        bs  = t.balance_sheet
        cf  = t.cashflow
    except Exception:
        fin = bs = cf = None

    # Income statement
    total_revenue  = _row(fin, "Total Revenue")
    gross_profit   = _row(fin, "Gross Profit")
    # As filed, like every other operating figure on the page (see rev_profit).
    operating_inc  = _row(fin, "Total Operating Income As Reported", "Operating Income")
    net_income     = _row(fin, "Net Income", "Net Income From Continuing Operation Net Minority Interest")
    tax_prov       = _row(fin, "Tax Provision")
    pretax_inc     = _row(fin, "Pretax Income")

    # Balance sheet
    invested_cap   = _row(bs, "Invested Capital")
    equity         = _row(bs, "Stockholders Equity", "Common Stock Equity")
    total_debt_bs  = _row(bs, "Total Debt")
    cash_bs        = _row(bs, "Cash Cash Equivalents And Short Term Investments",
                          "Cash And Cash Equivalents")

    # Cash flow
    op_cash        = _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    capex_raw      = _row(cf, "Capital Expenditure")
    capex          = abs(capex_raw) if capex_raw is not None else None
    fcf_direct     = _row(cf, "Free Cash Flow")

    # Derived
    tax_rate = (tax_prov / pretax_inc) if (tax_prov and pretax_inc and pretax_inc != 0) else None

    # ROIC — prefer direct Invested Capital row, else construct it
    roic: float | None = None
    if operating_inc and tax_rate is not None:
        nopat = operating_inc * (1 - tax_rate)
        ic = invested_cap
        if ic is None and equity is not None:
            ic = equity + (total_debt_bs or 0) - (cash_bs or 0)
        if ic and ic > 0:
            roic = nopat / ic * 100

    # ROE from info (more reliable than computed)
    roe = _pct(info.get("returnOnEquity"))

    # FCF — prefer direct row, fallback to op_cash - capex
    fcf: float | None = fcf_direct
    if fcf is None and op_cash is not None and capex is not None:
        fcf = op_cash - capex

    # FCF Margin
    fcf_margin: float | None = None
    if fcf is not None and total_revenue and total_revenue > 0:
        fcf_margin = fcf / total_revenue * 100

    # Cash Conversion (Operating Cash Flow / Net Income)
    cash_conv: float | None = None
    if op_cash is not None and net_income and net_income > 0:
        cash_conv = op_cash / net_income * 100

    # Same-quarter YoY for revenue and EPS, quarter by quarter (quarterly_growth.py).
    # The old label here said "TTM YoY"; Yahoo's revenueGrowth is actually the
    # latest quarter vs the same quarter a year earlier.
    try:
        from .quarterly_growth import us_quarterly
        quarterly_growth = us_quarterly(t, _fy_end_month(info), _fiscal_quarter_label)
    except Exception:                              # noqa: BLE001
        quarterly_growth = None
    qg = quarterly_growth or {}

    # Revenue and profit LEVELS over time (rev_profit.py). The margin cards show
    # ratios; a ratio cannot tell you whether the business got bigger.
    try:
        from .rev_profit import us_series
        _fym = _fy_end_month(info)
        _fin_co = ("financial" in (info.get("sector") or "").lower()
                   or any(x in (info.get("industry") or "").lower()
                          for x in ("bank", "insurance", "capital markets")))
        revenue_profit = us_series(fin, t.quarterly_income_stmt,
                                   lambda d: _fiscal_quarter_label(d, _fym), _fym, _fin_co)
    except Exception:                              # noqa: BLE001
        revenue_profit = None

    rev_growth = qg.get("latest_rev_yoy")
    if rev_growth is None and qg.get("latest_rev_yoy_reason") is None:
        rev_growth = _pct(info.get("revenueGrowth"))   # same basis, verified identical

    # ROE and Gross Margin — computed from the statements over one TTM window,
    # by the SAME function the Peer Comparison card uses, so the two cards on a
    # page can never disagree. Yahoo's `grossMargins` did not reproduce from
    # AVGO's own statements (75.5% vs 68.0-69.5% in every recent quarter), and
    # its period is undisclosed. Vendor fields are the fallback only.
    fy_label = _fy_ended_label(fin)
    ttm_label = "TTM, vendor (period not disclosed)"
    gm_label = "vendor figure (period not disclosed)"
    roe_label = ttm_label
    ttm_start = None
    try:
        from .peers import _us_metrics
        pm = _us_metrics(ticker_str.strip().upper())
    except Exception:                              # noqa: BLE001
        pm = {}
    if pm.get("state") == "ok" and pm.get("period_end"):
        from datetime import date as _date, timedelta as _td
        pe_d = _date.fromisoformat(pm["period_end"])
        ttm_start = pe_d - _td(days=365)
        ttm_label = f"TTM to {pe_d.strftime('%b %Y')}"
        if pm.get("roe") is not None or pm.get("roe_reason"):
            roe = pm.get("roe")
            roe_label = f"{ttm_label} · net income ÷ {pm.get('roe_basis') or 'equity'}"
        if pm.get("gross_margin") is not None:
            gross_margin = pm["gross_margin"]
            gm_label = f"{ttm_label} · from income statement"
        else:
            gross_margin = _pct(info.get("grossMargins"))
    else:
        gross_margin = _pct(info.get("grossMargins"))

    # Debt/EBITDA
    ebitda_info   = _s(info.get("ebitda"))
    total_debt_inf = _s(info.get("totalDebt"))
    debt_ebitda: float | None = None
    if total_debt_inf is not None and ebitda_info and ebitda_info > 0:
        debt_ebitda = total_debt_inf / ebitda_info

    # ── Gate 1 checks ─────────────────────────────────────────────────────────
    checks = [
        _check("ROIC",           roic,         15,  "above", "%",
               "Buffett's #1 metric — sustained >15% = genuine competitive advantage"),
        _check("ROE",            roe,          15,  "above", "%",
               "Quality earnings without excessive leverage (Peter Lynch / GARP)"),
        _check("FCF Margin",     fcf_margin,   10,  "above", "%",
               "Cash flow can't be manipulated — confirms real economic output"),
        _check("Cash Conversion",cash_conv,    85,  "above", "%",
               "Near 100% = no accounting games"),
        _gate_growth_check("Revenue Growth", rev_growth, qg.get("latest_rev_yoy_reason"), False,
               f"Latest quarter{' (' + qg['latest_label'] + ')' if qg.get('latest_label') else ''} vs the same quarter last year — minimum relevance floor"),
        _gate_growth_check("EPS Growth", qg.get("latest_eps_yoy"), qg.get("latest_eps_yoy_reason"), False,
               f"Adjusted EPS, latest quarter{' (' + qg['latest_label'] + ')' if qg.get('latest_label') else ''} vs the same quarter last year — earnings must keep pace"),
        _check("Gross Margin",   gross_margin, 40,  "above", "%",
               "Proxy for pricing power; compare within sector (40% is broad heuristic)"),
        _check("Debt / EBITDA",  debt_ebitda,   3,  "below", "×",
               "Survivability — <3× can weather crises without forced selling"),
    ]

    # Every check states the window it measures. Gate 1 deliberately mixes
    # windows (audited full-year cash metrics, current trailing returns, the
    # latest quarter's growth), so an unlabeled number is ambiguous by design.
    q_lab, q_prior = qg.get("latest_label"), None
    if q_lab and q_lab.startswith("Q") and " " in q_lab:
        q_prior = f"{q_lab.split()[0]} {int(q_lab.split()[1]) - 1}"
    q_window = f"{q_lab} vs {q_prior}" if q_prior else "latest quarter vs same quarter last year"
    fy = fy_label or "last fiscal year"
    periods = {
        "ROIC":            f"{fy} · NOPAT ÷ year-end invested capital",
        "ROE":             roe_label,
        "FCF Margin":      f"{fy} · free cash flow ÷ revenue",
        "Cash Conversion": f"{fy} · operating cash flow ÷ net income",
        "Revenue Growth":  f"{q_window} · single quarter",
        "EPS Growth":      f"{q_window} · adjusted EPS",
        "Gross Margin":    gm_label,
        "Debt / EBITDA":   "total debt at latest quarter ÷ TTM EBITDA (vendor)",
    }
    first_trade = _first_trade_date(info)
    for c in checks:
        c["period"] = periods.get(c["name"])
        if roe is None and c["name"] == "ROE" and pm.get("roe_reason") == "negative_equity":
            c["display"] = "n/m"
            c["note"] += " — n/m: shareholders' equity is negative (typically from buybacks)"
        # A trailing window that reaches back before the listing measures a
        # smaller, pre-IPO company: equity and share count jump at the IPO.
        if first_trade and c["name"] == "ROE" and ttm_start and first_trade > ttm_start:
            c["caveat"] = (f"Listed {first_trade:%d %b %Y}, inside the trailing window — part of the equity base "
                           f"is pre-IPO, which exaggerates ROE in either direction until the new capital is in every quarter.")

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

    # ── Gate 3: Valuation ─────────────────────────────────────────────────────
    peg         = _s(info.get("pegRatio") or info.get("trailingPegRatio"))
    forward_pe  = _s(info.get("forwardPE"))
    trailing_pe = _s(info.get("trailingPE"))
    pb          = _s(info.get("priceToBook"))
    ev          = _s(info.get("enterpriseValue"))

    # FCF Yield = FCF / Market Cap
    fcf_yield: float | None = None
    if fcf is not None and market_cap and market_cap > 0:
        fcf_yield = fcf / market_cap * 100

    # EV/FCF
    ev_fcf: float | None = None
    if ev and ev > 0 and fcf and fcf > 0:
        ev_fcf = ev / fcf

    def _val_verdict(metric: str, value: float | None) -> str:
        if value is None:
            return "unknown"
        # A negative multiple is not "cheap": a negative forward P/E means
        # forecast losses, a negative EV/FCF a negative EV, a negative PEG
        # shrinking earnings. Each once fell through `value < threshold` and
        # scored as undervalued (SKHY's corrupted EV/FCF of -2.6x did exactly that).
        if metric in ("peg", "fwd_pe", "ev_fcf") and value <= 0:
            return "unknown"
        if metric == "fcf_yield":
            if value > 6: return "undervalued"
            if value > 3: return "fair"
            return "expensive"
        if metric == "peg":
            if value < 1.0: return "undervalued"
            if value < 1.5: return "fair"
            if value < 2.0: return "watch"
            return "expensive"
        if metric == "fwd_pe":
            if value < 17: return "undervalued"
            if value < 22: return "fair"
            if value < 27: return "watch"
            return "expensive"
        if metric == "ev_fcf":
            if value < 15: return "undervalued"
            if value < 25: return "fair"
            if value < 35: return "watch"
            return "expensive"
        return "unknown"

    g3_metrics = {
        "fcf_yield":  {"value": round(fcf_yield, 2)  if fcf_yield  is not None else None,
                       "label": "FCF Yield",    "unit": "%",
                       "thresholds": ">6% deep value · 3–6% fair · <3% priced for perfection",
                       "verdict": _val_verdict("fcf_yield", fcf_yield)},
        "peg":        {"value": round(peg, 2)         if peg        is not None else None,
                       "label": "PEG Ratio",    "unit": "×",
                       "thresholds": "<1.0 cheap · 1.0–1.5 fair · >2.0 expensive",
                       "verdict": _val_verdict("peg", peg)},
        "forward_pe": {"value": round(forward_pe, 1)  if forward_pe is not None else None,
                       "label": "Forward P/E",  "unit": "×",
                       "thresholds": "<17 cheap · 17–22 fair · >27 expensive",
                       "verdict": _val_verdict("fwd_pe", forward_pe)},
        "ev_fcf":     {"value": round(ev_fcf, 1)      if ev_fcf     is not None else None,
                       "label": "EV / FCF",     "unit": "×",
                       "thresholds": "<15 cheap · 15–25 fair · >35 expensive",
                       "verdict": _val_verdict("ev_fcf", ev_fcf)},
    }

    _g3_periods = {
        "fcf_yield":  f"{fy_label or 'last fiscal year'} free cash flow ÷ current market cap",
        "peg":        "vendor-supplied — growth window not disclosed; treat with caution",
        "forward_pe": "current price ÷ consensus EPS for the next fiscal year",
        "ev_fcf":     f"current enterprise value ÷ {fy_label or 'last fiscal year'} free cash flow",
    }
    for _k, _m in g3_metrics.items():
        _m["period"] = _g3_periods.get(_k)

    verdicts = [m["verdict"] for m in g3_metrics.values() if m["verdict"] != "unknown"]
    exp_ct   = verdicts.count("expensive")
    und_ct   = verdicts.count("undervalued")
    if exp_ct >= 2:
        g3_verdict = "expensive"
    elif und_ct >= 2:
        g3_verdict = "pass"
    elif exp_ct == 1 and und_ct == 0:
        g3_verdict = "watch"
    else:
        g3_verdict = "fair"

    # ── Gate 2 metadata (qualitative — user assessed in UI) ───────────────────
    moat_hints: list[str] = []
    sec = (sector or "").lower()
    ind = (industry or "").lower()
    if any(x in ind for x in ["software", "internet", "platform", "marketplace"]):
        moat_hints.append("network")
        moat_hints.append("switching")
    if any(x in ind for x in ["pharmaceutical", "biotech", "semiconductor"]):
        moat_hints.append("intangible")
    if any(x in sec for x in ["technology", "communication"]):
        moat_hints.append("switching")

    # ── Supporting ratios (for the "Raw Data" expand section) ─────────────────
    beta = _s(info.get("beta"))
    raw_data = {
        "trailing_pe":  round(trailing_pe, 1) if trailing_pe is not None else None,
        "price_book":   round(pb, 2)           if pb          is not None else None,
        "beta":         round(beta, 2)         if beta        is not None else None,
        "ebitda":       ebitda_info,
        "total_debt":   total_debt_inf,
        # fin/bs/cf here are yfinance's ANNUAL statements (t.financials/.balance_sheet/
        # .cashflow, not the quarterly_* variants) — this is the most recent fiscal
        # YEAR's FCF, not a rolling trailing-twelve-month sum. Was mislabeled "TTM"
        # (confirmed real: for a company mid-fiscal-year, FY and TTM can diverge by
        # up to ~12 months of staleness). Renamed 2026-08-10.
        "fcf_annual":   round(fcf)             if fcf         is not None else None,
        "op_cash":      round(op_cash)         if op_cash     is not None else None,
        "market_cap":   market_cap,
        "ev":           ev,
        "employees":    employees,
    }

    # ── Industry cycle assessment ─────────────────────────────────────────────
    try:
        cycle = assess_cycle(ticker_str, t, info)
    except Exception:
        cycle = None

    # ── Earnings & valuation history ──────────────────────────────────────────
    try:
        earnings = _fetch_earnings_data(t, info)
    except Exception:
        earnings = None

    # ── Price history (5Y daily) ───────────────────────────────────────────────
    try:
        hist = t.history(period="5y")
        price_history = [
            {"d": str(idx.date()), "c": round(float(row["Close"]), 2),
             "o": _ohlc_val(row.get("Open")), "h": _ohlc_val(row.get("High")),
             "l": _ohlc_val(row.get("Low")), "v": _ohlc_val(row.get("Volume"), 0)}
            for idx, row in hist.iterrows()
            if row["Close"] == row["Close"]
        ] if not hist.empty else []
    except Exception:
        price_history = []

    # ── What analysts expect (us_forecasts.py) ────────────────────────────────
    # Targets, ratings, estimates and the dated firm-by-firm changes behind
    # them, all from the one source that serves them for every ticker.
    try:
        from datetime import datetime as _fc_dt, timedelta as _fc_td
        from . import us_forecasts
        _fc_fym = _fy_end_month(info)
        _fc_hist = [{"end": r["end"], "revenue": r["revenue"]}
                    for r in ((revenue_profit or {}).get("annual") or {}).get("rows", [])]
        forecasts = us_forecasts.fetch(
            t, info, price, history=_fc_hist,
            quarter_label=lambda d: _fiscal_quarter_label(
                _fc_dt(d.year, d.month, d.day) + _fc_td(days=45), _fc_fym),
            price_on=lambda d: _price_on_or_before(price_history, d))
    except Exception:                              # noqa: BLE001
        forecasts = None

    # ── Take-Profit Lab ────────────────────────────────────────────────────────
    def _tp_sim(closes: list[float]) -> dict:
        """Run all exit strategies on a price series starting at closes[0]."""
        from datetime import date as _date
        entry = closes[0]
        current = closes[-1]
        N = len(closes)
        years = N / 252  # approximate trading-day count

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
            """tiers: [(gain_threshold, fraction), ...]"""
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

        # Hold
        hold_ret  = round((current - entry) / entry * 100, 1)
        hold_cagr = _cagr(hold_ret)
        hold_dd   = _max_dd(closes)

        # Peak gain ever (rollercoaster metric)
        peak_price = max(closes)
        peak_gain  = round((peak_price - entry) / entry * 100, 1)
        is_rc = peak_gain > 20 and hold_ret < peak_gain - 15

        # Strategy results
        ts15_ret, ts15_cagr, ts15_hit, ts15_px, _ = _trailing_stop(0.15)
        ts25_ret, ts25_cagr, ts25_hit, ts25_px, _ = _trailing_stop(0.25)
        tier_ret, tier_cagr, tier_hits           = _tiered([(0.25, 1/3), (0.75, 1/3)])
        hard50_ret, hard50_cagr, hard50_hit      = _hard_exit(0.50)

        # Classification
        if hold_dd < 18 and not is_rc and hold_ret > 0:
            cls = "hold"
        elif hold_dd > 35 or is_rc:
            cls = "tier"
        elif hold_dd > 25:
            cls = "trailing"
        else:
            cls = "hold"

        # Actionable levels from current price
        action_levels = [
            {"label": "+25% tier 1",  "price": round(current * 1.25, 2)},
            {"label": "+50% milestone", "price": round(current * 1.50, 2)},
            {"label": "+75% tier 2",  "price": round(current * 1.75, 2)},
        ]

        return {
            "entry":       round(entry, 2),
            "current":     round(current, 2),
            "years":       round(years, 1),
            "strategies": [
                {"name": "Hold forever",        "ret": hold_ret,   "cagr": hold_cagr,   "max_dd": hold_dd,  "status": "holding",  "note": ""},
                {"name": "Trailing stop 15%",   "ret": ts15_ret,  "cagr": ts15_cagr,   "max_dd": None,     "status": "exited" if ts15_hit else "holding", "note": f"Exited at ${ts15_px}" if ts15_hit else "Never triggered"},
                {"name": "Trailing stop 25%",   "ret": ts25_ret,  "cagr": ts25_cagr,   "max_dd": None,     "status": "exited" if ts25_hit else "holding", "note": f"Exited at ${ts25_px}" if ts25_hit else "Never triggered"},
                {"name": "Tiered ⅓+⅓ at +25%/+75%", "ret": tier_ret, "cagr": tier_cagr, "max_dd": None, "status": "partial", "note": f"{tier_hits}/2 tiers hit"},
                {"name": "Exit all at +50%",    "ret": hard50_ret,"cagr": hard50_cagr, "max_dd": None,     "status": "exited" if hard50_hit else "holding", "note": "Milestone hit" if hard50_hit else "Not yet reached"},
            ],
            "hold_dd":     hold_dd,
            "peak_gain":   peak_gain,
            "is_rc":       is_rc,
            "cls":         cls,
            "levels":      action_levels,
        }

    try:
        from datetime import date as _d2, timedelta as _td
        take_profit: dict = {}
        for label, days in [("1Y", 365), ("3Y", 1095), ("5Y", 1825)]:
            cutoff = (_d2.today() - _td(days=days)).isoformat()
            idx = next((i for i, p in enumerate(price_history) if p["d"] >= cutoff), None)
            if idx is not None and idx < len(price_history) - 30:
                closes_slice = [p["c"] for p in price_history[idx:]]
                take_profit[label] = {
                    "entry_date": price_history[idx]["d"],
                    **_tp_sim(closes_slice),
                }
    except Exception:
        take_profit = {}

    # ── Fear Score ────────────────────────────────────────────────────────────
    def _fear_score(ph: list[dict]) -> dict | None:
        if len(ph) < 20:
            return None
        closes = [p["c"] for p in ph]
        price  = closes[-1]

        # RSI(14) — Wilder smoothed
        def _rsi14():
            gains, losses = [], []
            for i in range(1, len(closes)):
                d = closes[i] - closes[i - 1]
                gains.append(max(d, 0.0))
                losses.append(max(-d, 0.0))
            n = 14
            if len(gains) < n:
                return 50.0
            ag = sum(gains[:n]) / n
            al = sum(losses[:n]) / n
            for i in range(n, len(gains)):
                ag = (ag * (n - 1) + gains[i]) / n
                al = (al * (n - 1) + losses[i]) / n
            rs = ag / al if al > 0 else 100.0
            return 100.0 - 100.0 / (1.0 + rs)

        rsi_val   = _rsi14()
        fear_rsi  = max(0.0, min(100.0, 100.0 - rsi_val))

        # 52-week range position
        win52     = closes[-252:] if len(closes) >= 252 else closes
        hi52, lo52 = max(win52), min(win52)
        pct_range = (price - lo52) / (hi52 - lo52) if hi52 > lo52 else 0.5
        fear_52w  = max(0.0, min(100.0, (1.0 - pct_range) * 100.0))
        dd_from_hi = (price - hi52) / hi52 * 100.0  # negative or 0

        # MA200 gap
        ma_window  = closes[-200:] if len(closes) >= 200 else closes
        ma200_val  = sum(ma_window) / len(ma_window)
        gap_ma200  = (price - ma200_val) / ma200_val * 100.0
        fear_ma200 = max(0.0, min(100.0, 50.0 - gap_ma200 * 2.0))

        # VIX (market-wide)
        vix_now = None
        try:
            import yfinance as _yf2
            _vh = _yf2.Ticker("^VIX").history(period="5d")
            if not _vh.empty:
                vix_now = float(_vh["Close"].iloc[-1])
        except Exception:
            pass
        fear_vix = max(0.0, min(100.0, (vix_now - 10.0) / 40.0 * 100.0)) if vix_now else 50.0

        # Drawdown component
        fear_dd = max(0.0, min(100.0, -dd_from_hi * 2.0))

        score = round(
            0.30 * fear_rsi + 0.25 * fear_52w + 0.20 * fear_ma200
            + 0.15 * fear_vix + 0.10 * fear_dd, 1
        )

        label = (
            "Extreme Fear" if score >= 75 else
            "Fear"         if score >= 55 else
            "Neutral"      if score >= 45 else
            "Greed"        if score >= 25 else
            "Extreme Greed"
        )
        signal = (
            "Strong buy zone — market pricing in excessive pessimism" if score >= 70 else
            "Elevated fear — consider accumulating on weakness"        if score >= 55 else
            "Neutral — no strong directional sentiment signal"         if score >= 45 else
            "Caution — sentiment stretched toward greed"               if score >= 30 else
            "Trim zone — complacency historically precedes corrections"
        )

        return {
            "score": score,
            "label": label,
            "signal": signal,
            "components": [
                {"key": "rsi",      "label": "RSI(14)",           "weight": 30, "fear": round(fear_rsi,  1), "raw": round(rsi_val,  1), "raw_label": f"RSI {round(rsi_val,1)}"},
                {"key": "range",    "label": "52W range position", "weight": 25, "fear": round(fear_52w,  1), "raw": round(pct_range*100,1), "raw_label": f"{round(pct_range*100,1)}% from low"},
                {"key": "ma200",    "label": "vs MA200",           "weight": 20, "fear": round(fear_ma200,1), "raw": round(gap_ma200,1), "raw_label": f"{'+' if gap_ma200>=0 else ''}{round(gap_ma200,1)}% vs MA200"},
                {"key": "vix",      "label": "VIX",                "weight": 15, "fear": round(fear_vix,  1), "raw": round(vix_now,1) if vix_now else None, "raw_label": f"VIX {round(vix_now,1)}" if vix_now else "VIX n/a"},
                {"key": "drawdown", "label": "From 52W high",      "weight": 10, "fear": round(fear_dd,   1), "raw": round(dd_from_hi,1), "raw_label": f"{round(dd_from_hi,1)}% from high"},
            ],
        }

    try:
        fear_score = _fear_score(price_history)
    except Exception:
        fear_score = None

    # ── Lynch Classification ──────────────────────────────────────────────────
    def _lynch_classify() -> dict | None:
        sec_l = (sector or "").lower()
        ind_l = (industry or "").lower()

        # ── Multi-year revenue CAGR from annual financials ────────────────────
        rev_growth_1y = float(info.get("revenueGrowth") or 0)
        rev_cagr_3y   = rev_growth_1y
        rev_years     = 1
        try:
            fin = t.financials
            if fin is not None and not fin.empty:
                for key in ["Total Revenue", "Revenue"]:
                    if key in fin.index:
                        rev_row = fin.loc[key].dropna().sort_index()
                        revs = list(rev_row.values)
                        if len(revs) >= 3 and revs[0] and revs[-3]:
                            rev_cagr_3y = float((revs[-1] / revs[-3]) ** (1 / 3) - 1)
                            rev_years   = min(len(revs), 5)
                        break
        except Exception:
            pass

        # ── Key signals ───────────────────────────────────────────────────────
        div_yield  = float(info.get("trailingAnnualDividendYield") or info.get("dividendYield") or 0)
        if div_yield > 0.30:
            div_yield = 0.0  # yfinance sometimes returns garbage values; cap at 30%
        pe         = raw_data.get("trailing_pe")
        price_book = raw_data.get("price_book")
        beta_val   = float(raw_data.get("beta") or 1.0)
        mktcap     = float(raw_data.get("market_cap") or 0)
        tot_debt   = float(raw_data.get("total_debt") or 0)
        ebitda_v   = float(raw_data.get("ebitda") or 1) or 1
        fcf_v      = float(raw_data.get("fcf_annual") or 0)

        has_losses  = pe is None or pe < 0
        debt_ebitda = tot_debt / ebitda_v if ebitda_v > 0 else 0

        # ── Cyclical heuristics ───────────────────────────────────────────────
        CYCLICAL_SECTORS = {"energy", "material"}
        CYCLICAL_KEYS    = {
            "airline", "steel", "alum", "copper", "mining", "oil", "gas",
            "chemical", "cruise", "shipping", "auto", "fertilizer", "coal",
            "hotel", "resort", "casino",
        }
        is_cyclical = (
            any(c in sec_l for c in CYCLICAL_SECTORS) or
            any(k in ind_l for k in CYCLICAL_KEYS)
        )

        # ── Score each type ───────────────────────────────────────────────────
        scores: dict[str, int] = {}
        rationale: list[str]   = []

        # Turnaround
        if has_losses and debt_ebitda > 4:
            scores["Turnaround"] = 90
            rationale.append(f"Negative earnings with Debt/EBITDA {debt_ebitda:.1f}×")
        elif has_losses:
            scores["Turnaround"] = 65
            rationale.append("Negative or absent trailing earnings")
        elif rev_cagr_3y < -0.05:
            scores["Turnaround"] = 70
            rationale.append(f"Revenue declining {rev_cagr_3y*100:.1f}%/yr")

        # Cyclical
        if is_cyclical:
            scores["Cyclical"] = 85
            rationale.append(f"{sector} sector is economically cyclical")
        elif beta_val > 1.8 and mktcap > 5e9:
            scores["Cyclical"] = 50
            rationale.append(f"High beta ({beta_val:.1f}) suggests cyclical behaviour")

        # Asset Play
        if price_book is not None and price_book < 1.2 and not has_losses:
            scores["Asset Play"] = 75
            rationale.append(f"P/B {price_book:.2f}× — market may undervalue balance-sheet assets")
        elif mktcap > 0 and (fcf_v * 8 - tot_debt) / mktcap > 0.25:
            scores["Asset Play"] = 60
            rationale.append("Material net-cash position relative to market cap")

        # Fast Grower
        growth = max(rev_cagr_3y, rev_growth_1y)
        if growth > 0.20 and not is_cyclical and not has_losses:
            scores["Fast Grower"] = 90
            rationale.append(f"Revenue CAGR {rev_years}Y: +{rev_cagr_3y*100:.1f}% — well above Lynch's 15% threshold")
        elif growth > 0.15 and not is_cyclical:
            scores["Fast Grower"] = 70
            rationale.append(f"Revenue CAGR {rev_years}Y: +{rev_cagr_3y*100:.1f}%")

        # Slow Grower
        if rev_cagr_3y < 0.05 and div_yield > 0.025:
            scores["Slow Grower"] = 85
            rationale.append(f"Revenue +{rev_cagr_3y*100:.1f}%/yr with {div_yield*100:.1f}% dividend yield")
        elif rev_cagr_3y < 0.03 and not has_losses and not is_cyclical:
            scores["Slow Grower"] = 65
            rationale.append(f"Revenue growing just {rev_cagr_3y*100:.1f}%/yr")

        # Stalwart
        if 0.05 <= rev_cagr_3y <= 0.16 and mktcap > 20e9 and not is_cyclical and not has_losses:
            scores["Stalwart"] = 80
            rationale.append(f"Large-cap (${mktcap/1e9:.0f}B) growing steadily {rev_cagr_3y*100:.1f}%/yr")
        elif 0.05 <= rev_cagr_3y <= 0.16 and not is_cyclical and not has_losses:
            scores["Stalwart"] = 55
            rationale.append(f"Steady revenue growth {rev_cagr_3y*100:.1f}%/yr")

        if not scores:
            scores["Stalwart"] = 40
            rationale.append("No strong type signal — moderate growth, stable business")

        lynch_type = max(scores, key=scores.get)

        # ── Industry lifecycle ────────────────────────────────────────────────
        EMERGING_KEYS = {"artificial intelligence", " ai ", "generative", "quantum", "glp-1", "obesity"}
        GROWTH_KEYS   = {"software", "cloud", "digital", "ecommerce", "streaming", "electric vehicle",
                         "payment", "fintech", "biotech", "genomic", "cybersecurity", "data center"}
        DECLINING_KEYS = {"tobacco", "print", "newspaper", "coal", "department store"}

        combined = ind_l + " " + sec_l
        if any(k in combined for k in EMERGING_KEYS):
            ind_stage = "Emerging"
            ind_desc  = "Pre-consensus technology, rapid experimentation, most players still loss-making"
            ind_impl  = "Small position sizes; expect extreme volatility. Multiple outcomes still possible."
        elif any(k in combined for k in GROWTH_KEYS) or rev_cagr_3y > 0.12:
            ind_stage = "Growth"
            ind_desc  = "Industry growing faster than GDP; consolidation not yet complete"
            ind_impl  = "Execution and moat-building matter more than current valuation. Multiple winners can co-exist."
        elif any(k in combined for k in DECLINING_KEYS):
            ind_stage = "Declining"
            ind_desc  = "Structural headwinds; addressable market is shrinking"
            ind_impl  = "Own only if FCF yield is exceptional and management is returning capital aggressively."
        else:
            ind_stage = "Mature"
            ind_desc  = "Oligopoly formed; industry grows at GDP pace; pricing power is the primary moat"
            ind_impl  = "Focus on market-share gains, new geographies, or adjacent verticals as incremental growth drivers."

        # ── Corporate lifecycle ───────────────────────────────────────────────
        if has_losses and rev_cagr_3y > 0.15 and mktcap < 10e9:
            corp_stage = "Early Stage"
            corp_desc  = "Burning cash to capture market share; not yet at profitable scale"
            corp_impl  = "Track unit economics (contribution margin, payback period) — ignore GAAP earnings."
        elif rev_cagr_3y > 0.15 and fcf_v > 0:
            corp_stage = "Rapid Growth"
            corp_desc  = "Revenue accelerating, margins expanding, FCF turning strongly positive"
            corp_impl  = "Focus on revenue growth and market share. Multiple compression is a risk — don't overpay."
        elif 0.05 <= rev_cagr_3y <= 0.15 and not has_losses:
            corp_stage = "Maturity"
            corp_desc  = "Stable growth, expanding buybacks/dividends, defending rather than building market position"
            corp_impl  = "Valuation discipline paramount. Buyback yield becomes an important component of total return."
        elif div_yield > 0.03 and rev_cagr_3y < 0.05:
            corp_stage = "Cash Cow"
            corp_desc  = "Growth exhausted; returning capital via high dividends or buybacks"
            corp_impl  = "Total return = dividend + modest growth. Dividend sustainability is the critical risk."
        elif has_losses or rev_cagr_3y < -0.02:
            corp_stage = "Decline / Turnaround"
            corp_desc  = "Revenue or margins under structural pressure; management responding"
            corp_impl  = "Require a specific, time-bound catalyst. Without one, value can remain trapped indefinitely."
        else:
            corp_stage = "Maturity"
            corp_desc  = "Stable, mature business with predictable cash flows"
            corp_impl  = "Valuation discipline is paramount — buy at a fair price, not a premium."

        # ── Type metadata ─────────────────────────────────────────────────────
        TYPE_META = {
            "Fast Grower": {
                "color": "fg", "emoji": "🚀",
                "holding": "3–10+ years — hold until the story ends",
                "sell_trigger": "Revenue growth falls below 15% and appears structural (not a one-quarter blip). PEG > 2 (P/E more than 2× the growth rate). Core expansion geography saturated.",
                "watch": ["Unit count / store rollout pace", "Same-store sales or Net Revenue Retention", "Gross margin trend (expansion = pricing power)", "Market penetration in largest geography"],
                "risk": "Overpaying at peak optimism; story ending silently. Lynch: 'The key is to find the small company that's going to be big.'",
            },
            "Stalwart": {
                "color": "sw", "emoji": "🏛️",
                "holding": "2–5 years — rotate after 30–50% gain",
                "sell_trigger": "Stock up 30–50% from entry. EPS growth decelerates below 8%. Multiple expands well above historical average.",
                "watch": ["EPS growth consistency (look for 8–15%/yr)", "Operating margin trend", "Market share in core product", "Geographic expansion progress"],
                "risk": "Holding a stalwart like a forever-stock. Lynch owned these for gains, not permanence.",
            },
            "Slow Grower": {
                "color": "sg", "emoji": "🐢",
                "holding": "Income hold — exit on dividend cut or revenue decline",
                "sell_trigger": "Dividend cut or freeze. Revenue begins a sustained decline. Debt rises to fund the dividend (payout ratio > 90%).",
                "watch": ["Payout ratio (dangerous above 80%)", "FCF coverage of dividend", "Revenue trend", "Management tone on dividend safety"],
                "risk": "Dividend trap — high yield masking a deteriorating business.",
            },
            "Cyclical": {
                "color": "cy", "emoji": "🔄",
                "holding": "1–3 years — buy the trough, sell the boom",
                "sell_trigger": "Inventory builds. Capacity utilisation peaks (> 90%). Management guides for large capex expansion — classic top-of-cycle signal. Low P/E at the peak is a value trap.",
                "watch": ["Inventory-to-sales ratio", "Capacity utilisation rate", "Commodity price direction", "Order backlog trend"],
                "risk": "Holding through the full cycle. A cyclical at 8× P/E at peak earnings is far more expensive than one at 20× at trough.",
            },
            "Asset Play": {
                "color": "ap", "emoji": "🏗️",
                "holding": "1–5 years — wait for catalyst, exit once asset is recognised",
                "sell_trigger": "Asset sold, spun off, or market finally prices it in. If no catalyst materialises within 3 years, reassess thesis.",
                "watch": ["Balance sheet items: cash, real estate, IP, stakes in other companies", "Management capital allocation decisions", "Activist investor presence", "Insider buying"],
                "risk": "Value trap — catalyst never arrives; management destroys value through poor capital allocation.",
            },
            "Turnaround": {
                "color": "tu", "emoji": "🔧",
                "holding": "1–3 years — catalyst-driven with defined timeline",
                "sell_trigger": "Recovery complete (earnings normalised to historical levels) OR thesis fails (debt restructuring stalls, new CEO reverses course).",
                "watch": ["Cash burn rate and liquidity runway", "Debt refinancing timeline", "New management actions (cost cuts, asset sales)", "Gross margin recovery trajectory"],
                "risk": "Second derivative of the problem worsens. Turnarounds take longer than expected — size positions accordingly.",
            },
        }

        meta = TYPE_META.get(lynch_type, TYPE_META["Stalwart"])
        return {
            "type":         lynch_type,
            "color":        meta["color"],
            "emoji":        meta["emoji"],
            "holding":      meta["holding"],
            "sell_trigger": meta["sell_trigger"],
            "watch":        meta["watch"],
            "risk":         meta["risk"],
            "rationale":    rationale[:3],
            "signals": {
                "rev_cagr_3y":    round(rev_cagr_3y * 100, 1),
                "rev_growth_1y":  round(rev_growth_1y * 100, 1),
                "div_yield":      round(div_yield * 100, 2),
                "market_cap_b":   round(mktcap / 1e9, 1) if mktcap else None,
                "beta":           round(beta_val, 2),
                "has_losses":     has_losses,
                "is_cyclical":    is_cyclical,
            },
            "industry_cycle": {
                "stage": ind_stage,
                "desc":  ind_desc,
                "impl":  ind_impl,
            },
            "corp_cycle": {
                "stage": corp_stage,
                "desc":  corp_desc,
                "impl":  corp_impl,
            },
        }

    try:
        lynch_data = _lynch_classify()
    except Exception:
        lynch_data = None

    # ── ETF passive exposure ───────────────────────────────────────────────────
    def _etf_exposure(ticker_sym: str, sec: str, ind: str) -> list[dict]:
        """Return list of {etf, etf_name, weight_pct, rank} for relevant sector ETFs."""
        import yfinance as _yf
        ind_l = (ind or '').lower()
        sec_l = (sec or '').lower()

        # Primary ETF candidates by sector/industry (ordered by relevance)
        candidates: list[str] = []
        if 'semiconductor' in ind_l:
            candidates = ['SOXX', 'SMH']
        elif 'software' in ind_l or 'application' in ind_l:
            candidates = ['IGV', 'XLK']
        elif 'technology' in sec_l or 'electronic' in ind_l or 'consumer electronics' in ind_l:
            candidates = ['XLK', 'QQQ']
        elif 'communication' in sec_l or 'internet' in ind_l or 'media' in ind_l:
            candidates = ['XLC', 'QQQ']
        elif 'health' in sec_l or 'biotech' in ind_l or 'pharma' in ind_l:
            candidates = ['XLV', 'IBB']
        elif 'financial' in sec_l or 'bank' in ind_l:
            candidates = ['XLF', 'KBE']
        elif 'energy' in sec_l or 'oil' in ind_l:
            candidates = ['XLE', 'OIH']
        elif 'consumer discretionary' in sec_l or 'retail' in ind_l:
            candidates = ['XLY']
        elif 'consumer staples' in sec_l:
            candidates = ['XLP']
        elif 'industrial' in sec_l:
            candidates = ['XLI']
        elif 'material' in sec_l:
            candidates = ['XLB']
        elif 'utility' in sec_l or 'utilities' in sec_l:
            candidates = ['XLU']
        elif 'real estate' in sec_l:
            candidates = ['XLRE']
        else:
            candidates = ['SPY']

        results = []
        for etf_sym in candidates[:2]:  # check at most 2 ETFs
            try:
                etf_t = _yf.Ticker(etf_sym)
                holdings = etf_t.funds_data.top_holdings
                if holdings is None or holdings.empty:
                    continue
                if ticker_sym not in holdings.index:
                    continue
                row = holdings.loc[ticker_sym]
                rank = int(holdings.index.get_loc(ticker_sym)) + 1
                results.append({
                    "etf":        etf_sym,
                    "etf_name":   etf_t.info.get("shortName", etf_sym),
                    "weight_pct": round(float(row["Holding Percent"]) * 100, 2),
                    "rank":       rank,
                })
            except Exception:
                continue
        return results

    try:
        etf_exposure = _etf_exposure(ticker_str, sector, industry)
    except Exception:
        etf_exposure = []

    # ── Institutional flow ─────────────────────────────────────────────────────
    try:
        mh  = t.major_holders
        ih  = t.institutional_holders
        mh_dict = mh["Value"].to_dict() if mh is not None and not mh.empty else {}
        inst_pct    = float(mh_dict.get("institutionsPercentHeld", 0))
        insider_pct = float(mh_dict.get("insidersPercentHeld", 0))
        inst_float  = float(mh_dict.get("institutionsFloatPercentHeld", 0))
        inst_count  = int(mh_dict.get("institutionsCount", 0))

        top_holders = []
        if ih is not None and not ih.empty:
            for _, row in ih.head(10).iterrows():
                pct_chg = float(row["pctChange"]) if row["pctChange"] == row["pctChange"] else 0.0
                top_holders.append({
                    "name":       str(row["Holder"]),
                    "pct_held":   round(float(row["pctHeld"]) * 100, 2),
                    "shares":     int(row["Shares"]),
                    "value":      int(row["Value"]),
                    "pct_change": round(pct_chg * 100, 2),
                    "date":       str(row["Date Reported"].date()),
                })

        # 12M return from price_history
        ret_12m = None
        if len(price_history) >= 2:
            try:
                from datetime import date, timedelta
                cutoff = (date.today() - timedelta(days=365)).isoformat()
                start = next((p for p in price_history if p["d"] >= cutoff), None)
                if start and price_history[-1]["c"] and start["c"]:
                    ret_12m = round((price_history[-1]["c"] / start["c"] - 1) * 100, 1)
            except Exception:
                pass

        # ── Insider trading activity ────────────────────────────────────────
        insider_summary = None
        insider_txns = []
        try:
            ip = t.insider_purchases
            if ip is not None and not ip.empty:
                ip_dict = {str(r["Insider Purchases Last 6m"]): r for _, r in ip.iterrows()}

                def _ip_val(label, col):
                    row = ip_dict.get(label)
                    if row is None:
                        return None
                    v = row.get(col)
                    return None if v is None or v != v else v

                insider_summary = {
                    "buy_shares":   _ip_val("Purchases", "Shares"),
                    "buy_count":    _ip_val("Purchases", "Trans"),
                    "sell_shares":  _ip_val("Sales", "Shares"),
                    "sell_count":   _ip_val("Sales", "Trans"),
                    "net_shares":   _ip_val("Net Shares Purchased (Sold)", "Shares"),
                    "total_held":   _ip_val("Total Insider Shares Held", "Shares"),
                    "pct_net":      _ip_val("% Net Shares Purchased (Sold)", "Shares"),
                }
        except Exception:
            insider_summary = None

        try:
            itx = t.insider_transactions
            if itx is not None and not itx.empty:
                for _, row in itx.head(12).iterrows():
                    text = str(row.get("Text", "") or "")
                    text_l = text.lower()
                    if "purchase" in text_l or "buy" in text_l:
                        kind = "Buy"
                    elif "sale" in text_l:
                        kind = "Sell"
                    elif "award" in text_l or "grant" in text_l:
                        kind = "Award"
                    elif "option" in text_l and "exercise" in text_l:
                        kind = "Exercise"
                    else:
                        kind = "Other"
                    shares = row.get("Shares")
                    value = row.get("Value")
                    start_date = row.get("Start Date")
                    insider_txns.append({
                        "insider":  str(row.get("Insider", "") or "").title(),
                        "position": str(row.get("Position", "") or ""),
                        "kind":     kind,
                        "text":     text,
                        "shares":   int(shares) if shares == shares else None,
                        "value":    int(value) if value == value and value else None,
                        "date":     str(start_date.date()) if hasattr(start_date, "date") else str(start_date or ""),
                    })
        except Exception:
            insider_txns = []

        flow_data = {
            "inst_pct":       round(inst_pct * 100, 2),
            "insider_pct":    round(insider_pct * 100, 2),
            "inst_float":     round(inst_float * 100, 2),
            "inst_count":     inst_count,
            "top_holders":    top_holders,
            "etf_exposure":   etf_exposure,
            "ret_12m":        ret_12m,
            "insider_summary": insider_summary,
            "insider_txns":   insider_txns,
        }
    except Exception:
        flow_data = None

    # ── Dividend & Buyback (Capital Return) ─────────────────────────────────────
    def _capital_return() -> dict:
        import pandas as _pd
        from datetime import date as _date, timedelta as _timedelta

        market_cap_v = raw_data.get("market_cap") or 0

        # ── Dividend history & trend ─────────────────────────────────────────
        dividend: dict = {"has_dividend": False}
        try:
            divs = t.dividends
            if divs is not None and len(divs) > 0:
                s = divs.copy()
                if s.index.tz is not None:
                    s.index = s.index.tz_localize(None)
                yearly = s.groupby(s.index.year).sum()
                current_year = _date.today().year
                years_sorted = sorted(yearly.index)
                full_years = [y for y in years_sorted if y < current_year]

                history = [{"year": int(y), "total": round(float(yearly[y]), 2)} for y in years_sorted]
                ytd_total = round(float(yearly.get(current_year, 0.0)), 2)

                streak = 0
                for i in range(len(full_years) - 1, 0, -1):
                    if yearly[full_years[i]] > yearly[full_years[i - 1]]:
                        streak += 1
                    else:
                        break

                def _cagr_over(n_years):
                    if len(full_years) < n_years:
                        return None
                    y0, y1 = full_years[-n_years], full_years[-1]
                    v0, v1 = float(yearly[y0]), float(yearly[y1])
                    span = y1 - y0
                    if v0 <= 0 or span <= 0:
                        return None
                    return round(((v1 / v0) ** (1 / span) - 1) * 100, 1)

                cagr_5y  = _cagr_over(5)
                cagr_10y = _cagr_over(10)

                if len(full_years) < 2:
                    trend = "Recently Initiated"
                else:
                    last_v, prev_v = float(yearly[full_years[-1]]), float(yearly[full_years[-2]])
                    if streak >= 3:
                        trend = "Growing"
                    elif last_v < prev_v * 0.8:
                        trend = "Cut"
                    elif last_v < prev_v:
                        trend = "Declining"
                    elif abs(last_v - prev_v) / prev_v < 0.01:
                        trend = "Flat"
                    else:
                        trend = "Growing"

                # Yield and rate come from the same payment history charted above
                # (fx_normalize.trailing_dividend), per trading unit. The old
                # `> 30% -> 0` guard silently hid unit errors instead of fixing them.
                _y = info.get("trailingAnnualDividendYield")
                div_yield_pct = float(_y) * 100 if _y is not None else 0.0
                payout_ratio = info.get("payoutRatio")
                ex_div = info.get("exDividendDate")
                last_div_date = info.get("lastDividendDate")

                # If the listing's payment history is stale (yield had to be
                # estimated from the statements), the recent years are MISSING,
                # not reduced — a trend, streak or CAGR from it would read a data
                # gap as a dividend cut. Sony showed "Cut" for exactly this reason.
                if (info.get("_dividend_basis_note") or "").startswith("estimated"):
                    trend = "Unknown — recent payment history incomplete"
                    streak, cagr_5y, cagr_10y = None, None, None

                dividend = {
                    "has_dividend":     True,
                    "yield_basis":      info.get("_dividend_basis_note"),
                    "rate":             round(float(info.get("trailingAnnualDividendRate") or info.get("dividendRate") or 0), 2),
                    "yield_pct":        round(div_yield_pct, 2),
                    "payout_ratio_pct": round(payout_ratio * 100, 1) if payout_ratio and payout_ratio == payout_ratio else None,
                    "payout_note":      info.get("_payout_note"),
                    "last_div_value":   info.get("lastDividendValue"),
                    "ex_div_date":      str(_date.fromtimestamp(ex_div)) if ex_div else None,
                    "last_div_date":    str(_date.fromtimestamp(last_div_date)) if last_div_date else None,
                    "consecutive_growth_years": streak,
                    "cagr_5y_pct":      cagr_5y,
                    "cagr_10y_pct":     cagr_10y,
                    "trend":            trend,
                    "ytd_total":        ytd_total,
                    "history":          history[-15:],
                }
        except Exception:
            dividend = {"has_dividend": False}

        # No payment history on this listing (SKHY began US trading in July 2026)
        # while fx_normalize estimated a yield from dividends actually paid. Show
        # the estimate, labelled, rather than "no dividend".
        if not dividend.get("has_dividend") and (info.get("trailingAnnualDividendRate") or 0) > 0:
            _y = info.get("trailingAnnualDividendYield")
            dividend = {
                "has_dividend": True,
                "rate": round(float(info["trailingAnnualDividendRate"]), 4),
                "yield_pct": round(float(_y) * 100, 2) if _y is not None else None,
                "payout_ratio_pct": round(info["payoutRatio"] * 100, 1) if info.get("payoutRatio") else None,
                "payout_note": info.get("_payout_note"),
                "yield_basis": info.get("_dividend_basis_note"),
                "trend": "Unknown — no payment history on this listing",
                "history": [],
            }

        # ── Buyback history & trend ──────────────────────────────────────────
        buyback: dict = {"has_buybacks": False}
        try:
            cf = t.cashflow
            annual = []
            # Common stock only. "Repurchase Of Capital Stock" also counts
            # preferred shares redeemed: JPMorgan's 2025 reads $34.6bn on that
            # row, of which $3.0bn was preferred — its buyback was $31.6bn.
            _bb_row = next((r for r in ("Common Stock Payments", "Repurchase Of Capital Stock")
                            if cf is not None and not cf.empty and r in cf.index), None)
            if _bb_row:
                row = cf.loc[_bb_row].dropna().sort_index()
                for dt, val in row.items():
                    amt = abs(float(val))
                    if amt > 0:
                        annual.append({"period": f"FY{dt.year}", "amount": round(amt)})

            shares_now = info.get("sharesOutstanding")
            shares_3y_ago = None
            reduction_3y_pct = None
            buyback_share_note = None
            try:
                cutoff = (_date.today() - _timedelta(days=365 * 3)).isoformat()
                shares_series = t.get_shares_full(start=cutoff)
                reduction_3y_pct, share_note = _share_change_3y(shares_series, t.splits)
                if share_note:
                    buyback_share_note = share_note
            except Exception:
                pass

            if annual:
                latest_amt = annual[-1]["amount"]
                buyback_yield_pct = round(latest_amt / market_cap_v * 100, 2) if market_cap_v else None

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
                    "has_buybacks":      True,
                    "annual":            annual[-8:],
                    "latest_amount":     latest_amt,
                    "buyback_yield_pct": buyback_yield_pct,
                    "shares_now":        int(shares_now) if shares_now else None,
                    "shares_3y_ago":     int(shares_3y_ago) if shares_3y_ago else None,
                    "reduction_3y_pct":  reduction_3y_pct,
                    "reduction_note":    buyback_share_note,
                    "trend":             trend,
                }
        except Exception:
            buyback = {"has_buybacks": False}

        return {"dividend": dividend, "buyback": buyback}

    try:
        capital_return = _capital_return()
    except Exception:
        capital_return = None

    return {
        "ticker":      ticker_str,
        "name":        name,
        "sector":      sector,
        "industry":    industry,
        "description": description,
        "price":       price,
        "market_cap":  market_cap,
        "currency":    currency,
        "website":     website,
        "gate1": {
            "verdict": g1_verdict,
            "passed":  passed,
            "failed":  failed,
            "na":      na_count,
            "scorable": scorable,
            "checks":  checks,
        },
        "gate2": {
            "moat_hints": moat_hints,
        },
        "gate3": {
            "verdict": g3_verdict,
            "metrics": g3_metrics,
        },
        "raw":           raw_data,
        "cycle":         cycle,
        "earnings":      earnings,
        "quarterly_growth": quarterly_growth,
        "revenue_profit": revenue_profit,
        "forecasts":     forecasts,
        "fx_conversion": fx_conversion,
        "listing_basis": listing_basis,
        "val_growth":    _safe_val_growth(t, info, price_history, is_etf=False),
        "debt_structure": _safe_debt_structure(
            info, fin, bs,
            is_financial=("financial" in (sector or "").lower()
                          or any(x in (industry or "").lower() for x in ("bank", "insurance", "capital markets"))),
        ),
        "hidden_assets": _safe_hidden_assets(info, bs, market_cap),
        "cashflow_margins": _safe_cashflow_margins(fin, cf),
        "dcf": _safe_dcf(info, fin, bs, cf, market_cap, price, roic,
                         is_financial=("financial" in (sector or "").lower()
                                       or any(x in (industry or "").lower() for x in ("bank", "insurance", "capital markets")))),
        "fundamental_scores": _safe_fundamental_scores(
            info, fin, bs, cf, market_cap, price, ev,
            is_financial=("financial" in (sector or "").lower()
                          or any(x in (industry or "").lower() for x in ("bank", "insurance", "capital markets"))),
        ),
        "price_history": price_history,
        "benchmark_history": _benchmark_history(),
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "benchmark_name": BENCHMARK_NAME,
        "stage_analysis": _stage_analysis_for(ticker_str),
        "top_down": _top_down_for(ticker_str),
        "flow_data":     flow_data,
        "take_profit":   take_profit,
        "fear_score":    fear_score,
        "lynch_data":    lynch_data,
        "capital_return": capital_return,
    }

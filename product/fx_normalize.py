"""Currency normalisation for stocks that trade in one currency and report in another.

WHY THIS EXISTS
---------------
SK hynix trades on Nasdaq as SKHY in USD but files its statements in KRW. Yahoo
serves both side by side without converting, so every card that divides a
statement figure by a market figure was silently wrong by a factor of ~1,360:

    FCF yield           1,988.7%    (₩24.8tn FCF ÷ $1.25tn market cap) — true: 1.46%
    Enterprise value    −$65.3tn    ($ market cap + ₩ debt − ₩ cash)     — true: $1,198bn
    EV / FCF            −2.6×       (and the negative scored as "cheap")  — true: 65.8×
    Price / book        377.7×                                            — true: 6.5×
    Net cash            1,125% of market cap
    EPS history         "$6,037.8"  (won, printed with a dollar sign)

Gate 3 passed SKHY as undervalued on the strength of two of those numbers.

THE FIX IS AT THE SOURCE, NOT PER CARD
--------------------------------------
Patching each card would miss the next one. Instead the Ticker is wrapped:
every statement DataFrame is converted into the TRADING currency before any
card sees it, and the vendor summary (`info`) is split field by field into the
three groups Yahoo actually mixes:

  * statement currency — converted here (revenue, EBITDA, FCF, debt, cash,
    per-share revenue/cash, dividend rate)
  * trading currency   — left alone (market cap, price targets, trailing and
    forward EPS, which Yahoo does convert)
  * mixed, unusable    — recomputed from converted parts (EV, EV/revenue,
    EV/EBITDA, P/S, P/B, book value, dividend yield)

LIMITS, STATED
--------------
One spot rate converts every period. Ratios are unaffected, but a multi-year
series of absolute amounts in USD also moves with the exchange rate. The rate
and its date are surfaced on the page.
"""
from __future__ import annotations

import time
from typing import Any

_FX_CACHE: dict[str, tuple[float, float]] = {}
_FX_TTL = 6 * 3600

# Rows that must NOT be converted: counts and rates, not money.
_NO_CONVERT = ("Shares", "Share Issued", "Tax Rate")

# Yahoo `info` fields reported in the STATEMENT currency.
_INFO_STATEMENT_CCY = (
    "totalRevenue", "grossProfits", "ebitda", "freeCashflow", "operatingCashflow",
    "totalDebt", "totalCash", "netIncomeToCommon",
)
# Fields Yahoo builds from BOTH currencies, or quotes per ORDINARY share while
# price and EPS are per ADR — recomputed from totals, never trusted. TSMC showed
# why per-share fields go here: one ADR is five ordinary shares, so its dividend
# rate taken at face value gave a 0.20% yield.
_INFO_MIXED = (
    "enterpriseValue", "enterpriseToRevenue", "enterpriseToEbitda",
    "priceToSalesTrailing12Months", "priceToBook", "bookValue",
    "totalCashPerShare", "revenuePerShare",
    "trailingAnnualDividendRate", "trailingAnnualDividendYield",
    "dividendRate", "dividendYield", "fiveYearAvgDividendYield", "payoutRatio",
)


def fx_rate(from_ccy: str, to_ccy: str) -> tuple[float | None, str | None]:
    """Units of `to_ccy` per one unit of `from_ccy`, and the quote date."""
    if not from_ccy or not to_ccy or from_ccy == to_ccy:
        return 1.0, None
    key = f"{from_ccy}{to_ccy}"
    hit = _FX_CACHE.get(key)
    if hit and time.time() - hit[1] < _FX_TTL:
        return hit[0], None
    try:
        import yfinance as yf
        h = yf.Ticker(f"{from_ccy}{to_ccy}=X").history(period="5d")
        if h is None or h.empty:
            return None, None
        rate = float(h["Close"].iloc[-1])
        _FX_CACHE[key] = (rate, time.time())
        return rate, h.index[-1].date().isoformat()
    except Exception:                              # noqa: BLE001
        return None, None


def convert_frame(df: Any, rate: float) -> Any:
    """Multiply monetary rows by `rate`; leave share counts and tax rates."""
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    for row in out.index:
        if any(tag in str(row) for tag in _NO_CONVERT):
            continue
        try:
            out.loc[row] = out.loc[row].astype(float) * rate
        except (TypeError, ValueError):
            continue
    return out


def convert_info(info: dict, rate: float, equity_trading_ccy: float | None = None,
                 dividends_trading_ccy: float | None = None) -> dict:
    out = dict(info)
    for k in _INFO_STATEMENT_CCY:
        if isinstance(out.get(k), (int, float)) and not isinstance(out.get(k), bool):
            out[k] = out[k] * rate
    for k in _INFO_MIXED:
        out[k] = None
    mcap, price = out.get("marketCap"), out.get("currentPrice") or out.get("regularMarketPrice")
    debt, cash = out.get("totalDebt"), out.get("totalCash")
    rev, ebitda, shares = out.get("totalRevenue"), out.get("ebitda"), out.get("sharesOutstanding")
    if mcap is not None and debt is not None and cash is not None:
        ev = mcap + debt - cash
        out["enterpriseValue"] = ev
        if rev:
            out["enterpriseToRevenue"] = ev / rev
        if ebitda:
            out["enterpriseToEbitda"] = ev / ebitda
    if mcap and rev:
        out["priceToSalesTrailing12Months"] = mcap / rev
    if shares:
        # Per-share figures rebuilt on the SAME share count as market cap, so
        # they are per trading unit (per ADR where there is one).
        if out.get("totalCash") is not None:
            out["totalCashPerShare"] = out["totalCash"] / shares
        if rev is not None:
            out["revenuePerShare"] = rev / shares
        if equity_trading_ccy:
            out["bookValue"] = equity_trading_ccy / shares
            if price:
                out["priceToBook"] = price / out["bookValue"]
        if dividends_trading_ccy is not None:
            out["trailingAnnualDividendRate"] = dividends_trading_ccy / shares
            if price:
                out["trailingAnnualDividendYield"] = out["trailingAnnualDividendRate"] / price
    return out


class FxTicker:
    """A yfinance Ticker whose statements and summary are in the trading currency."""

    _FRAMES = ("financials", "income_stmt", "balance_sheet", "cashflow",
               "quarterly_financials", "quarterly_income_stmt",
               "quarterly_balance_sheet", "quarterly_cashflow")

    def __init__(self, ticker: Any, info: dict, rate: float, from_ccy: str, to_ccy: str,
                 rate_date: str | None) -> None:
        self._t = ticker
        self._rate = rate
        self._cache: dict[str, Any] = {}
        self.fx = {"from": from_ccy, "to": to_ccy, "rate": rate, "as_of": rate_date}
        eq = None
        try:
            qb = self.quarterly_balance_sheet
            for name in ("Stockholders Equity", "Common Stock Equity"):
                if qb is not None and not qb.empty and name in qb.index:
                    v = qb.loc[name].dropna()
                    if len(v):
                        eq = float(v.iloc[0])
                    break
        except Exception:                          # noqa: BLE001
            eq = None
        self.info = convert_info(info, rate, eq, None)

    def __getattr__(self, name: str) -> Any:
        if name in FxTicker._FRAMES:
            if name not in self._cache:
                self._cache[name] = convert_frame(getattr(self._t, name), self._rate)
            return self._cache[name]
        return getattr(self._t, name)


def trailing_dividend(ticker: Any) -> tuple[float | None, dict]:
    """Dividends actually paid per TRADING UNIT over the last year, from the
    payment history — the only dividend source that is right for ADRs.

    Yahoo's dividend-rate fields are per ORDINARY share for many ADRs while price
    is per ADR: HSBC showed a 0.71% yield against 4.09% actually paid per ADS,
    BP 0.73% vs 5.48%. The payment series is per unit held, in the currency paid.

    Frequency-aware: sums the last N payments where N is inferred from the
    typical gap (monthly 12, quarterly 4, semiannual 2, annual 1), so a trailing
    window cannot catch one annual payment twice or a quarterly payer five times.
    """
    import pandas as pd
    try:
        d = ticker.dividends
    except Exception:                              # noqa: BLE001
        return None, {"ok": False, "note": "dividend history unavailable"}
    if d is None or len(d) == 0:
        return 0.0, {"ok": True, "payments": 0, "note": "no dividends on record"}
    d = d.copy()
    if getattr(d.index, "tz", None) is not None:
        d.index = d.index.tz_localize(None)
    d = d[d > 0].sort_index()
    today = pd.Timestamp.today().normalize()
    recent = d[d.index > today - pd.Timedelta(days=400)]
    if len(recent) == 0:
        return 0.0, {"ok": True, "payments": 0, "suspended": True,
                     "last_paid": d.index[-1].date().isoformat(),
                     "note": "no payment in the last 400 days"}
    tail = d.index[-9:]
    gaps = [(b - a).days for a, b in zip(tail[:-1], tail[1:]) if (b - a).days > 0]
    if gaps:
        gaps.sort()
        per_year = max(1, min(12, round(365 / gaps[len(gaps) // 2])))
    else:
        per_year = 1
    rate = float(recent.iloc[-per_year:].sum())
    return rate, {"ok": True, "payments": int(min(per_year, len(recent))), "per_year": per_year,
                  "last_paid": recent.index[-1].date().isoformat(),
                  "note": f"last {min(per_year, len(recent))} payments per trading unit (includes any specials)"}


def normalize(ticker: Any, info: dict) -> tuple[Any, dict, dict | None]:
    """Put a listing on ONE basis before any card reads it.

    1. Share basis — shares = market cap ÷ price (trading units). Yahoo's
       `sharesOutstanding` can count a single share class: Novo Nordisk was off
       by 1.32x and Itaú by 2.04x against its own market cap, which skews every
       per-share figure (book value, DCF value per share, dividend per share).
    2. Currency — statements converted into the trading currency (FxTicker).
    3. Dividends — from payments actually made per trading unit.
    4. Payout ratio — total dividends paid ÷ net income, both from statements,
       so no per-share or currency basis can leak in.
    5. Consistency check — statement net income per unit vs vendor EPS. A large
       gap means EPS-based metrics on the page cannot be trusted.

    Returns (ticker, info, report). `report` is None only when nothing needed
    changing and nothing looked wrong.
    """
    info = dict(info)
    report: dict[str, Any] = {"notes": [], "warnings": []}
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    mcap, shares = info.get("marketCap"), info.get("sharesOutstanding")

    # 1. share basis
    if mcap and price:
        implied = mcap / price
        if shares and abs(implied / shares - 1) > 0.02:
            report["share_basis"] = {"vendor_shares": shares, "used_shares": implied,
                                     "ratio": round(implied / shares, 3)}
            report["notes"].append(
                f"Share count set to market cap ÷ price ({implied:,.0f} units); the vendor count "
                f"({shares:,.0f}) covers only part of the equity, a {implied / shares:.2f}x gap.")
            info["_sharesOutstanding_vendor"] = shares
            info["sharesOutstanding"] = implied
        elif not shares:
            info["sharesOutstanding"] = implied

    # 2. currency
    trade, fin = info.get("currency"), info.get("financialCurrency")
    if trade and fin and trade != fin:
        rate, as_of = fx_rate(fin, trade)
        if not rate:
            report["fx"] = {"from": fin, "to": trade, "rate": None, "as_of": None}
            report["warnings"].append(
                f"Reports in {fin}, trades in {trade}, and no exchange rate was available — "
                f"metrics comparing statements with price are unreliable.")
        else:
            ticker = FxTicker(ticker, info, rate, fin, trade, as_of)
            info = ticker.info
            report["fx"] = ticker.fx
            report["notes"].append(
                f"Reports in {fin}; statement figures converted to {trade} at 1 {fin} = {rate:.6g} {trade}"
                f"{' (' + as_of + ')' if as_of else ''}. One spot rate converts every period, so "
                f"multi-year absolute amounts also move with the exchange rate; ratios are unaffected.")

    units = info.get("sharesOutstanding")

    # Statement dividends and net income (already in the trading currency).
    div_fy = ni_fy = fy_col = None
    try:
        cf, fin_df = ticker.cashflow, ticker.financials
        div_row = next((cf.loc[n] for n in ("Cash Dividends Paid", "Common Stock Dividend Paid")
                        if cf is not None and not cf.empty and n in cf.index), None)
        ni_row = next((fin_df.loc[n] for n in ("Net Income Common Stockholders", "Net Income")
                       if fin_df is not None and not fin_df.empty and n in fin_df.index), None)
        if div_row is not None and len(div_row.dropna()):
            fy_col = div_row.dropna().index[0]
            div_fy = abs(float(div_row[fy_col]))
            if ni_row is not None and fy_col in ni_row.index and ni_row[fy_col] == ni_row[fy_col]:
                ni_fy = float(ni_row[fy_col])
    except Exception:                              # noqa: BLE001
        pass

    # 3. dividends — payment history first; statement fallback when the listing's
    # history is missing or stale but the company demonstrably pays. Toyota,
    # Sony, MUFG and SKHY all read 0.00% from history alone while their cash-flow
    # statements show dividends paid. Total dividends ÷ trading units needs no
    # ADR ratio and no vendor per-share field.
    rate_d, meta = trailing_dividend(ticker)
    if meta.get("ok") and not rate_d and div_fy and units and price:
        rate_d = div_fy / units
        meta = {"ok": True, "estimated": True, "payments": None,
                "note": (f"estimated — FY{fy_col.year} dividends paid ÷ trading units; this listing's "
                         f"payment history is incomplete")}
        report["notes"].append(
            f"Dividend yield estimated from FY{fy_col.year} dividends paid ÷ trading units: the payment "
            f"history for this listing is missing or stale, but the company's cash-flow statement shows dividends.")
    if meta.get("ok"):
        info["_dividend_basis_note"] = meta.get("note")
        info["trailingAnnualDividendRate"] = rate_d
        info["trailingAnnualDividendYield"] = (rate_d / price) if (price and rate_d is not None) else None
        info["dividendRate"] = rate_d
        info["dividendYield"] = None               # vendor field is sometimes %, sometimes decimal
        report["dividend_basis"] = meta

    # 4. payout ratio from totals. Above 300% it is reported as not meaningful:
    # BP paid $5.06bn on net income of $0.054bn, a true but useless "9,369%".
    if div_fy is not None:
        if ni_fy and ni_fy > 0:
            ratio = div_fy / ni_fy
            info["payoutRatio"] = ratio if ratio <= 3 else None
            if ratio > 3:
                info["_payout_note"] = (f"not meaningful — dividends paid were {ratio:.0f}x "
                                        f"FY{fy_col.year} net income (earnings depressed)")
        else:
            info["payoutRatio"] = None
            info["_payout_note"] = f"not meaningful — FY{fy_col.year} net income was not positive"

    # Book value per trading unit, from the balance sheet, for EVERY listing.
    # Yahoo's bookValue is per ordinary share for ADRs with a ratio: HSBC 4.6x,
    # BP 4.0x, Infosys 2.0x off, giving P/B of 8.25x, 8.13x, 9.77x against true
    # 1.81x, 2.02x, 4.88x. For single-class US stocks the rebuild matches the
    # vendor within a few percent, so doing it universally is safe.
    try:
        qb = ticker.quarterly_balance_sheet
        eq = next((float(qb.loc[n].dropna().iloc[0]) for n in ("Stockholders Equity", "Common Stock Equity")
                   if qb is not None and not qb.empty and n in qb.index and len(qb.loc[n].dropna())), None)
        if eq and units and price:
            vendor_bv = info.get("bookValue")
            bv = eq / units
            info["bookValue"] = bv
            info["priceToBook"] = price / bv if bv > 0 else None
            if vendor_bv and bv and abs(vendor_bv / bv - 1) > 0.10 and "fx" not in report:
                report["notes"].append(
                    f"Book value rebuilt from the balance sheet ({bv:,.2f} per trading unit); the vendor's "
                    f"figure ({vendor_bv:,.2f}) was {bv / vendor_bv:.1f}x off — typical of an ADR quoted per "
                    f"ordinary share. P/B is {info['priceToBook']:.2f}x, not {price / vendor_bv:.2f}x.")
    except Exception:                              # noqa: BLE001
        pass

    # 5. consistency: statement net income per unit vs vendor EPS
    try:
        qi = ticker.quarterly_income_stmt
        te, units = info.get("trailingEps"), info.get("sharesOutstanding")
        row = next((qi.loc[n] for n in ("Net Income Common Stockholders", "Net Income")
                    if qi is not None and not qi.empty and n in qi.index), None)
        if row is not None and te and units:
            v = row.iloc[:4]
            if not v.isna().any():
                ratio = (float(v.sum()) / units) / te
                report["eps_consistency"] = round(ratio, 3)
                if not 0.75 <= ratio <= 1.33:
                    report["warnings"].append(
                        f"Statement net income per share differs from the vendor's EPS by {abs(ratio - 1) * 100:.0f}% "
                        f"(ratio {ratio:.2f}). Earnings-based metrics — P/E, PEG, Graham Number — may be unreliable "
                        f"for this listing (common causes: discontinued operations, share-class or ADR mismatches).")
    except Exception:                              # noqa: BLE001
        pass

    if not report["notes"] and not report["warnings"] and "fx" not in report and "share_basis" not in report:
        return ticker, info, None
    return ticker, info, report

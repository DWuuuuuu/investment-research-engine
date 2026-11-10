"""Offline tests for product/fx_normalize.py and the EPS units guard.

Pins the ADR / foreign-listing faults found 2026-09-15 on SKHY, TSM, BABA, HSBC,
BP, NVO and ITUB. No network: tickers are small fakes.
"""
from __future__ import annotations

import unittest

import pandas as pd

from product.fx_normalize import convert_frame, convert_info, normalize, trailing_dividend
from product.screener import _eps_units_suspect


class FakeTicker:
    def __init__(self, dividends=None, frames=None):
        self.dividends = dividends if dividends is not None else pd.Series(dtype=float)
        frames = frames or {}
        for name in ("financials", "income_stmt", "balance_sheet", "cashflow", "quarterly_financials",
                     "quarterly_income_stmt", "quarterly_balance_sheet", "quarterly_cashflow"):
            setattr(self, name, frames.get(name, pd.DataFrame()))


def payments(dates, amt):
    today = pd.Timestamp.today().normalize()
    return pd.Series([amt] * len(dates), index=[today - pd.Timedelta(days=d) for d in dates])


class ConvertFrameTests(unittest.TestCase):
    def test_money_converted_counts_and_rates_untouched(self):
        col = pd.Timestamp("2026-06-30")
        df = pd.DataFrame({col: [1_000_000.0, 7_000_000.0, 0.21, 13_147.8]},
                          index=["Net Income", "Diluted Average Shares", "Tax Rate For Calcs", "Diluted EPS"])
        out = convert_frame(df, 0.001)
        self.assertAlmostEqual(out.loc["Net Income", col], 1_000.0)
        self.assertAlmostEqual(out.loc["Diluted EPS", col], 13.1478)
        self.assertEqual(out.loc["Diluted Average Shares", col], 7_000_000.0)
        self.assertEqual(out.loc["Tax Rate For Calcs", col], 0.21)


class ConvertInfoTests(unittest.TestCase):
    """Shaped like SKHY: USD market cap, KRW statements."""

    def info(self):
        return {"currency": "USD", "financialCurrency": "KRW", "currentPrice": 175.63,
                "marketCap": 1.2467e12, "sharesOutstanding": 7.0985e9,
                "totalDebt": 21.11e12, "totalCash": 87.98e12, "freeCashflow": 55.77e12,
                "totalRevenue": 189.17e12, "ebitda": 143.6e12, "trailingEps": 16.97,
                "enterpriseValue": -65.3e12, "priceToBook": 377.7, "bookValue": 0.465,
                "trailingAnnualDividendRate": 3750.0}

    def test_enterprise_value_rebuilt_in_one_currency(self):
        out = convert_info(self.info(), 1 / 1362.3)
        self.assertGreater(out["enterpriseValue"], 1.1e12)          # was -$65.3tn
        self.assertLess(out["enterpriseValue"], 1.3e12)

    def test_mixed_fields_never_pass_through(self):
        out = convert_info(self.info(), 1 / 1362.3)
        self.assertIsNone(out["trailingAnnualDividendRate"])        # ₩3,750 would read as $3,750
        self.assertIsNone(out["bookValue"])                         # vendor 0.465 is unit-broken
        self.assertIsNone(out["priceToBook"])                       # vendor 377.7x

    def test_book_value_rebuilt_from_converted_equity(self):
        equity_usd = 1.2467e12 / 6.5                                 # implies P/B 6.5
        out = convert_info(self.info(), 1 / 1362.3, equity_trading_ccy=equity_usd)
        self.assertAlmostEqual(out["priceToBook"], 6.5, delta=0.1)

    def test_trading_currency_fields_left_alone(self):
        out = convert_info(self.info(), 1 / 1362.3)
        self.assertEqual(out["marketCap"], 1.2467e12)
        self.assertEqual(out["trailingEps"], 16.97)


class DividendTests(unittest.TestCase):
    def test_quarterly_payer_sums_four_not_five(self):
        rate, meta = trailing_dividend(FakeTicker(payments([5, 96, 187, 278, 369, 460], 0.25)))
        self.assertEqual(meta["per_year"], 4)
        self.assertAlmostEqual(rate, 1.0)

    def test_annual_payer_never_counted_twice(self):
        rate, meta = trailing_dividend(FakeTicker(payments([10, 375, 740], 2.35)))
        self.assertEqual(meta["per_year"], 1)
        self.assertAlmostEqual(rate, 2.35)

    def test_semiannual_payer(self):
        rate, _ = trailing_dividend(FakeTicker(payments([20, 202, 384, 566], 1.5)))
        self.assertAlmostEqual(rate, 3.0)

    def test_suspended_dividend_reads_zero(self):
        rate, meta = trailing_dividend(FakeTicker(payments([500, 591, 682], 0.5)))
        self.assertEqual(rate, 0.0)
        self.assertTrue(meta["suspended"])


class ShareBasisTests(unittest.TestCase):
    def test_share_count_follows_market_cap(self):
        """Itaú: vendor share count was half of market cap ÷ price."""
        info = {"currency": "USD", "financialCurrency": "USD", "currentPrice": 7.0,
                "marketCap": 70e9, "sharesOutstanding": 5e9}
        _, out, rep = normalize(FakeTicker(), info)
        self.assertAlmostEqual(out["sharesOutstanding"], 10e9)
        self.assertEqual(rep["share_basis"]["ratio"], 2.0)

    def test_consistent_listing_is_left_unreported(self):
        info = {"currency": "USD", "financialCurrency": "USD", "currentPrice": 10.0,
                "marketCap": 100e9, "sharesOutstanding": 10e9}
        _, out, rep = normalize(FakeTicker(), info)
        self.assertIsNone(rep)
        self.assertEqual(out["sharesOutstanding"], 10e9)


class NegativeMultipleAndUnitsTests(unittest.TestCase):
    def test_units_guard(self):
        self.assertTrue(_eps_units_suspect(90.62, 4.87))     # SKHY
        self.assertTrue(_eps_units_suspect(0.30, 2.80))      # BABA-like, per share vs per ADS
        self.assertFalse(_eps_units_suspect(3.32, 3.24))     # ordinary beat
        self.assertFalse(_eps_units_suspect(-0.05, 0.30))    # real miss into a loss
        self.assertFalse(_eps_units_suspect(0.12, 0.02))     # near-zero estimate: real 6x beat possible


if __name__ == "__main__":
    unittest.main()


class ShareChangeTests(unittest.TestCase):
    """Share-count history is not split-adjusted upstream."""

    @staticmethod
    def series(points):
        return pd.Series([v for _, v in points], index=pd.to_datetime([d for d, _ in points]))

    def test_split_is_not_read_as_dilution(self):
        from product.screener import _share_change_3y
        # AVGO-like: 10-for-1 split mid-series, true count flat
        pts = [(f"2023-{m:02d}-01", 412e6) for m in range(1, 12)] + \
              [(f"2025-{m:02d}-01", 4120e6) for m in range(1, 12)]
        splits = pd.Series([10.0], index=pd.to_datetime(["2024-07-15"]))
        chg, note = _share_change_3y(self.series(pts), splits)
        self.assertAlmostEqual(chg, 0.0, places=1)
        self.assertIsNone(note)

    def test_unadjusted_split_would_have_been_flagged_not_printed(self):
        from product.screener import _share_change_3y
        pts = [(f"2023-{m:02d}-01", 412e6) for m in range(1, 12)] + \
              [(f"2025-{m:02d}-01", 4120e6) for m in range(1, 12)]
        chg, note = _share_change_3y(self.series(pts), pd.Series(dtype=float))   # split data missing
        self.assertIsNone(chg)
        self.assertIn("inconsistent", note)

    def test_single_outlier_does_not_move_the_result(self):
        from product.screener import _share_change_3y
        pts = [(f"2023-{m:02d}-01", 100e6) for m in range(1, 12)] + \
              [(f"2025-{m:02d}-01", 95e6) for m in range(1, 12)]
        pts[0] = ("2023-01-01", 900e6)                                   # vendor spike
        chg, _ = _share_change_3y(self.series(pts), pd.Series(dtype=float))
        self.assertAlmostEqual(chg, 5.0, places=1)

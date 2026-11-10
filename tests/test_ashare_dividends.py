"""A-share dividends: one row per payment, per today's share, one basis for
rate and yield. Checked against Tencent and Tushare's own yields 2026-10-12."""
import unittest
from datetime import date

from product.ashare_screener import _dividend_events, _dividend_ttm
from product.peers import _cn_roe

TODAY = date(2026, 10, 12)


def row(end, ex, cash, stk=0.0, proc="实施"):
    return {"end_date": end, "ex_date": ex, "cash_div": cash, "cash_div_tax": cash,
            "stk_div": stk, "div_proc": proc}


class Events(unittest.TestCase):
    def test_a_dividend_announced_twice_counts_once(self):
        """Moutai's FY2025 final came back as two rows: ¥80.01 instead of ¥51.98."""
        ev = _dividend_events([row("20251231", "20260626", 28.02423), row("20251231", "20260626", 28.02423),
                               row("20250930", "20251219", 23.957), row("20241231", "20250626", 27.673)])
        rate, yld = _dividend_ttm(ev, 1263.0, TODAY)
        self.assertAlmostEqual(rate, 51.98123, places=4)
        self.assertAlmostEqual(yld, 4.12, places=2)

    def test_the_same_payment_under_two_period_ends_counts_once(self):
        """CATL's special: one ex-date, one amount, two `end_date`s."""
        ev = _dividend_events([row("20241211", "20250124", 1.23), row("20240930", "20250124", 1.23)])
        self.assertEqual(len(ev), 1)

    def test_plans_that_were_not_carried_out_are_ignored(self):
        self.assertEqual(_dividend_events([row("20251231", "20260626", 5.0, proc="预案")]), [])

    def test_bonus_shares_restate_earlier_dividends(self):
        """Shanghai Yanpu: 4.9 bonus shares per 10, three years running. The
        raw ¥0.388 -> ¥0.28 reads as a cut; per today's share it is a rise."""
        ev = _dividend_events([row("20251231", "20260602", 0.28, 0.49), row("20241231", "20250522", 0.388, 0.49),
                               row("20231231", "20240520", 0.375, 0.48), row("20221231", "20230531", 0.125)])
        adj = {e["end_date"][:4]: round(e["adj"], 4) for e in ev}
        self.assertEqual(adj["2025"], round(0.28 / 1.49, 4))
        self.assertEqual(adj["2024"], round(0.388 / 1.49 / 1.49, 4))
        self.assertGreater(adj["2025"], adj["2024"])
        self.assertGreater(adj["2024"], adj["2023"])
        rate, yld = _dividend_ttm(ev, 24.66, TODAY)
        self.assertAlmostEqual(yld, 0.76, places=2)                # Tushare dv_ttm 0.759, Tencent 0.74

    def test_only_the_last_twelve_months_by_ex_date(self):
        """Wuliangye: Tushare's dv_ratio counted three payments and read 11.80%."""
        ev = _dividend_events([row("20251231", "20260716", 2.5796852), row("20250930", "20251218", 2.578),
                               row("20241231", "20250718", 3.169)])
        rate, yld = _dividend_ttm(ev, 70.56, TODAY)
        self.assertAlmostEqual(rate, 5.1577, places=4)
        self.assertAlmostEqual(yld, 7.31, places=2)

    def test_no_payment_in_the_window_is_none_not_zero(self):
        ev = _dividend_events([row("20231231", "20240712", 4.67)])
        self.assertEqual(_dividend_ttm(ev, 70.0, TODAY), (None, None))


class WeightedRoe(unittest.TestCase):
    def test_the_reported_figure_is_used(self):
        """Moutai FY2025: 32.53 weighted, as reported; 34.46 on year-end equity."""
        r = {"end_date": "20251231", "roe_waa": 32.53, "roe_yearly": 34.462}
        self.assertEqual(_cn_roe(r, annualise=False), 32.53)

    def test_a_half_year_is_doubled(self):
        r = {"end_date": "20260630", "roe_waa": 16.75, "roe_yearly": 35.9086}
        self.assertEqual(_cn_roe(r, annualise=True), 33.5)

    def test_nine_months_and_one_quarter(self):
        self.assertEqual(_cn_roe({"end_date": "20250930", "roe_waa": 24.64}, True), 32.85)
        self.assertEqual(_cn_roe({"end_date": "20260331", "roe_waa": 10.57}, True), 42.28)

    def test_falls_back_when_the_weighted_figure_is_missing(self):
        self.assertEqual(_cn_roe({"end_date": "20260630", "roe_waa": None, "roe_yearly": 11.68}, True), 11.68)

    def test_no_row_is_none(self):
        self.assertIsNone(_cn_roe(None, True))


class NewestFiling(unittest.TestCase):
    def test_a_blank_is_filled_from_the_same_filing(self):
        """CMB H1 2026: the same period twice, one row missing roe_waa."""
        from product.cn_data import newest_filing
        rows = [{"end_date": "20260630", "ann_date": "20260829", "roe": 5.84, "roe_waa": 6.71},
                {"end_date": "20260630", "ann_date": "20260829", "roe": 5.84, "roe_waa": None}]
        self.assertEqual(newest_filing(rows)["roe_waa"], 6.71)
        self.assertEqual(newest_filing(list(reversed(rows)))["roe_waa"], 6.71)

    def test_an_older_filing_never_fills_a_blank(self):
        from product.cn_data import newest_filing
        rows = [{"end_date": "20250630", "ann_date": "20250828", "eps": 5.02, "roe_waa": 9.0},
                {"end_date": "20250630", "ann_date": "20260430", "eps": 1.19, "roe_waa": None}]
        out = newest_filing(rows)
        self.assertEqual(out["eps"], 1.19)
        self.assertIsNone(out["roe_waa"])

    def test_empty(self):
        from product.cn_data import newest_filing
        self.assertEqual(newest_filing([]), {})


if __name__ == "__main__":
    unittest.main()

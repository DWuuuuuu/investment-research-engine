"""Revenue and profit levels — the trend card beneath every stock.

No network: the US tests hand in small pandas frames shaped like yfinance's,
the A-share tests hand in Tushare-shaped year-to-date rows.
"""
import unittest

import pandas as pd

from product import rev_profit as rp
from product.screener import _fiscal_quarter_label


def frame(cols, **rows):
    return pd.DataFrame(rows, index=[pd.Timestamp(c) for c in cols]).T


def q_label(d):
    """The screener's own fiscal labeler on a December year end — the real one,
    so these tests also pin the +45-day shift that turns a statement column's
    period END into the announcement date it expects."""
    return _fiscal_quarter_label(d, 12)


class Cagr(unittest.TestCase):
    def test_plain_case(self):
        self.assertEqual(rp.cagr(100.0, 200.0, 2)[0], 41.4)

    def test_a_loss_at_either_end_has_no_growth_rate(self):
        self.assertEqual(rp.cagr(-10.0, 50.0, 3), (None, "negative_base"))
        self.assertEqual(rp.cagr(50.0, -10.0, 3), (None, "turned_to_loss"))

    def test_zero_base_is_refused_not_divided_by(self):
        self.assertEqual(rp.cagr(0.0, 50.0, 3), (None, "negative_base"))


class UsAnnual(unittest.TestCase):
    def setUp(self):
        self.fin = frame(["2023-12-31", "2022-12-31", "2021-12-31"],
                         **{"Total Revenue": [200.0, 150.0, 100.0],
                            "Operating Income": [40.0, 30.0, 20.0],
                            "Net Income": [30.0, 20.0, 10.0]})

    def test_oldest_to_newest_with_growth_and_margins(self):
        out = rp.us_series(self.fin, None, q_label, 12)
        rows = out["annual"]["rows"]
        self.assertEqual([r["label"] for r in rows], ["FY2021", "FY2022", "FY2023"])
        self.assertIsNone(rows[0]["rev_yoy"])                  # nothing before it
        self.assertEqual(rows[1]["rev_yoy"], 50.0)
        self.assertEqual(rows[2]["net_margin"], 15.0)

    def test_compound_growth_spans_the_whole_window(self):
        t = rp.us_series(self.fin, None, q_label, 12)["annual"]["revenue"]
        self.assertEqual(t["span"], {"from": "FY2021", "to": "FY2023", "years": 2.0})
        self.assertEqual(t["cagr"], 41.4)                      # 100 -> 200 over two years

    def test_a_loss_year_blocks_the_rate_but_not_the_level(self):
        fin = frame(["2023-12-31", "2022-12-31"],
                    **{"Total Revenue": [200.0, 150.0], "Net Income": [30.0, -20.0]})
        out = rp.us_series(fin, None, q_label, 12)["annual"]
        self.assertEqual(out["net"]["latest"], 30.0)
        self.assertEqual(out["net"]["yoy_reason"], "negative_base")
        self.assertEqual(out["rows"][0]["net_margin"], -13.3)

    def test_a_missing_revenue_column_is_dropped_not_zeroed(self):
        fin = frame(["2023-12-31", "2022-12-31"],
                    **{"Total Revenue": [200.0, None], "Net Income": [30.0, 20.0]})
        rows = rp.us_series(fin, None, q_label, 12)["annual"]["rows"]
        self.assertEqual([r["label"] for r in rows], ["FY2023"])

    def test_no_revenue_row_means_no_card(self):
        fin = frame(["2023-12-31"], **{"Net Income": [30.0]})
        self.assertIsNone(rp.us_series(fin, None, q_label, 12))

    def test_off_calendar_year_end_is_called_out(self):
        out = rp.us_series(self.fin, None, q_label, 10)
        self.assertTrue(any("month 10" in x for x in out["limits"]))
        self.assertFalse(any("month 12" in x for x in rp.us_series(self.fin, None, q_label, 12)["limits"]))


class UsQuarterly(unittest.TestCase):
    def setUp(self):
        self.q = frame(["2024-03-31", "2023-12-31", "2023-09-30", "2023-06-30", "2023-03-31"],
                       **{"Total Revenue": [55.0, 50.0, 45.0, 40.0, 35.0],
                          "Operating Income": [11.0, 10.0, 9.0, 8.0, 7.0],
                          "Net Income": [8.0, 7.0, 6.0, 5.0, 4.0]})

    def test_statement_columns_are_labelled_as_their_own_quarter(self):
        """A period END is not an announcement date. The column ending
        2024-03-31 is Q1 2024, not Q4 2023."""
        rows = rp.us_series(None, self.q, q_label, 12)["quarterly"]["rows"]
        self.assertEqual([r["label"] for r in rows],
                         ["Q1 2023", "Q2 2023", "Q3 2023", "Q4 2023", "Q1 2024"])

    def test_quarterly_change_is_against_the_same_quarter_a_year_earlier(self):
        """Not the quarter before. Moutai sells a third of its year in Q1, so a
        sequential comparison would print seasonality as a collapse."""
        q = frame(["2024-03-31", "2023-12-31", "2023-09-30", "2023-06-30", "2023-03-31"],
                  **{"Total Revenue": [55.0, 50.0, 45.0, 40.0, 35.0]})
        rows = {r["label"]: r for r in rp.us_series(None, q, q_label, 12)["quarterly"]["rows"]}
        self.assertEqual(rows["Q1 2024"]["rev_yoy"], 57.1)       # 35 -> 55, four back
        self.assertIsNone(rows["Q4 2023"]["rev_yoy"])            # nothing four back yet

    def test_ttm_is_the_last_four_quarters(self):
        t = rp.us_series(None, self.q, q_label, 12)["ttm"]
        self.assertEqual(t["revenue"], 190.0)                  # 40+45+50+55
        self.assertEqual(t["net"], 26.0)
        self.assertEqual(t["label"], "TTM to Q1 2024")

    def test_no_ttm_across_a_missing_quarter(self):
        """Berkshire's real shape: Yahoo has no Q3 2025 column, so the four rows
        left span fifteen months and must not be added up as a year."""
        q = frame(["2026-06-30", "2026-03-31", "2025-12-31", "2025-06-30", "2025-03-31"],
                  **{"Total Revenue": [117.0, 92.0, 111.0, 98.0, 83.0]})
        out = rp.us_series(None, q, q_label, 12)
        self.assertEqual(len(out["quarterly"]["rows"]), 5)     # the rows are still charted
        self.assertIsNone(out["ttm"])                          # the sum is not

    def test_no_ttm_from_three_quarters(self):
        q = frame(["2024-03-31", "2023-12-31", "2023-09-30"],
                  **{"Total Revenue": [55.0, 50.0, 45.0]})
        self.assertIsNone(rp.us_series(None, q, q_label, 12)["ttm"])

    def test_ttm_is_withheld_when_a_quarter_is_missing_a_line(self):
        """Summing three numbers and a None would understate the year."""
        q = frame(["2024-03-31", "2023-12-31", "2023-09-30", "2023-06-30"],
                  **{"Total Revenue": [55.0, 50.0, 45.0, 40.0],
                     "Net Income": [8.0, 7.0, None, 5.0]})
        t = rp.us_series(None, q, q_label, 12)["ttm"]
        self.assertEqual(t["revenue"], 190.0)
        self.assertIsNone(t["net"])


_Y = 1e8   # Tushare reports in yuan; the restatement detector rounds to the
           # nearest thousand, so toy values would round to zero and never
           # register as a restatement.


class AsFiled(unittest.TestCase):
    """Figures a reader can look up in the filing. Checked against SEC EDGAR
    and Eastmoney on 2026-10-12."""

    def test_operating_income_is_the_companys_own_figure(self):
        """Coca-Cola 2024: the vendor's normalised row says $14.0bn, the 10-K
        says $9.99bn. The 10-K wins."""
        fin = frame(["2024-12-31"], **{"Total Revenue": [47.061], "Operating Income": [14.022],
                                       "Total Operating Income As Reported": [9.992], "Net Income": [10.631]})
        self.assertEqual(rp.us_series(fin, None, q_label, 12)["annual"]["rows"][0]["operating"], 9.992)

    def test_falls_back_when_the_as_reported_row_is_absent(self):
        fin = frame(["2024-12-31"], **{"Total Revenue": [47.061], "Operating Income": [14.022]})
        self.assertEqual(rp.us_series(fin, None, q_label, 12)["annual"]["rows"][0]["operating"], 14.022)

    def test_net_income_is_before_preferred_dividends(self):
        """JPMorgan 2025: $57.0bn reported; $55.7bn is what is left for common."""
        fin = frame(["2025-12-31"], **{"Total Revenue": [181.8], "Net Income": [57.048],
                                       "Net Income Common Stockholders": [55.681]})
        self.assertEqual(rp.us_series(fin, None, q_label, 12)["annual"]["rows"][0]["net"], 57.048)

    def test_a_bank_gets_the_revenue_caveat_and_others_do_not(self):
        fin = frame(["2025-12-31"], **{"Total Revenue": [181.8], "Net Income": [57.0]})
        self.assertTrue(any("bank or insurer" in x for x in rp.us_series(fin, None, q_label, 12, True)["limits"]))
        self.assertFalse(any("bank or insurer" in x for x in rp.us_series(fin, None, q_label, 12)["limits"]))

    def test_a_share_net_profit_is_attributable_to_shareholders(self):
        """CXMT FY2025: ¥7.14bn including minorities, ¥1.87bn to shareholders."""
        row = {"end_date": "20251231", "f_ann_date": "20260709", "update_flag": "1",
               "total_revenue": 618e8, "operate_profit": 80e8, "n_income": 71.4e8, "n_income_attr_p": 18.7e8}
        out = rp.cn_series([row])
        self.assertEqual(out["annual"]["rows"][0]["net"], 18.7e8)
        self.assertEqual(out["net_margin_label"], "Margin to shareholders")
        self.assertEqual(out["annual"]["rows"][0]["net_margin"], 3.0)

    def test_a_share_quarters_are_de_cumulated_on_the_same_line(self):
        rows = [{"end_date": "20260331", "f_ann_date": "20260425", "update_flag": "1", "total_revenue": 100e8,
                 "operate_profit": 30e8, "n_income": 26e8, "n_income_attr_p": 25e8},
                {"end_date": "20260630", "f_ann_date": "20260820", "update_flag": "1", "total_revenue": 220e8,
                 "operate_profit": 66e8, "n_income": 58e8, "n_income_attr_p": 55e8}]
        q = {r["label"]: r for r in rp.cn_series(rows)["quarterly"]["rows"]}
        self.assertEqual(q["Q2 2026"]["net"], 30e8)

    def test_without_the_attributable_line_it_says_what_it_is_showing(self):
        row = {"end_date": "20251231", "f_ann_date": "20260709", "update_flag": "1",
               "total_revenue": 618e8, "operate_profit": 80e8, "n_income": 71.4e8}
        out = rp.cn_series([row])
        self.assertEqual(out["annual"]["rows"][0]["net"], 71.4e8)
        self.assertNotIn("net_margin_label", out)
        self.assertIn("including minority", out["basis"]["net"])


class NetLineWarnings(unittest.TestCase):
    """The bottom line is not always the business earning money."""

    def test_net_above_revenue_is_called_out(self):
        """Yahoo's SK hynix Q2 2026: ₩93.8tn of net income on ₩79.3tn of
        revenue. A 118% net margin printed without comment is the failure."""
        fin = frame(["2026-06-30"], **{"Total Revenue": [79.3],
                                       "Operating Income": [60.5], "Net Income": [93.8]})
        out = rp.us_series(fin, None, q_label, 12)["annual"]
        self.assertEqual([w["code"] for w in out["warnings"]], ["net_above_revenue"])
        self.assertIn("net_above_revenue", out["rows"][0]["flags"])
        self.assertEqual(out["rows"][0]["net_margin"], 118.3)      # still reported

    def test_a_one_off_gain_over_an_operating_loss_is_called_out(self):
        """Nebius Q2 2025: +$584m net on a $111m operating loss."""
        fin = frame(["2025-06-30"], **{"Total Revenue": [105.1],
                                       "Operating Income": [-111.2], "Net Income": [584.4]})
        codes = [w["code"] for w in rp.us_series(fin, None, q_label, 12)["annual"]["warnings"]]
        self.assertEqual(codes, ["net_above_revenue"])

    def test_profit_short_of_the_gap_is_not_flagged(self):
        """Broadcom FY2025: net below operating. Nothing to say."""
        fin = frame(["2025-10-31"], **{"Total Revenue": [63.9],
                                       "Operating Income": [26.1], "Net Income": [23.1]})
        out = rp.us_series(fin, None, q_label, 12)["annual"]
        self.assertEqual(out["warnings"], [])
        self.assertNotIn("flags", out["rows"][0])

    def test_a_loss_making_period_raises_nothing(self):
        fin = frame(["2024-12-31"], **{"Total Revenue": [100.0],
                                       "Operating Income": [-20.0], "Net Income": [-30.0]})
        self.assertEqual(rp.us_series(fin, None, q_label, 12)["annual"]["warnings"], [])


def cn_row(end, filed, rev, op, net, flag="1"):
    return {"end_date": end, "f_ann_date": filed, "ann_date": filed, "update_flag": flag,
            "total_revenue": rev * _Y, "operate_profit": op * _Y, "n_income": net * _Y}


class CnSeries(unittest.TestCase):
    def rows(self):
        return [
            cn_row("20240331", "20240420", 100.0, 30.0, 25.0),
            cn_row("20240630", "20240820", 220.0, 66.0, 55.0),
            cn_row("20240930", "20241025", 330.0, 99.0, 82.0),
            cn_row("20241231", "20250330", 460.0, 140.0, 115.0),
        ]

    def test_quarters_are_de_cumulated_from_year_to_date_filings(self):
        q = rp.cn_series(self.rows())["quarterly"]["rows"]
        self.assertEqual([r["label"] for r in q], ["Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024"])
        self.assertEqual([r["revenue"] / _Y for r in q], [100.0, 120.0, 110.0, 130.0])
        self.assertEqual([r["net"] / _Y for r in q], [25.0, 30.0, 27.0, 33.0])

    def test_the_full_year_comes_from_the_december_filing_not_a_sum(self):
        a = rp.cn_series(self.rows())["annual"]["rows"]
        self.assertEqual([(r["label"], r["revenue"] / _Y, r["net"] / _Y) for r in a],
                         [("FY2024", 460.0, 115.0)])

    def test_the_newest_vintage_of_a_restated_year_wins(self):
        rows = self.rows() + [cn_row("20241231", "20260415", 300.0, 90.0, 70.0)]
        a = rp.cn_series(rows)["annual"]["rows"]
        self.assertEqual(a[-1]["revenue"] / _Y, 300.0)

    def test_a_quarter_straddling_a_restatement_is_dropped_from_the_chart(self):
        """H1 restated minus Q1 as originally filed is not a quarter. The growth
        table withholds it; this chart must not plot it either."""
        rows = self.rows() + [cn_row("20240630", "20260415", 120.0, 36.0, 30.0)]
        out = rp.cn_series(rows)
        labels = [r["label"] for r in out["quarterly"]["rows"]]
        self.assertNotIn("Q2 2024", labels)
        self.assertEqual(out["withheld"]["Q2 2024"], "restatement_mix")
        # Q3 is lost too: its prior cumulative is now the restated H1.
        self.assertNotIn("Q3 2024", labels)
        self.assertTrue(any("withheld" in x for x in out["limits"]))

    def test_no_ttm_across_a_hole(self):
        rows = self.rows() + [cn_row("20240630", "20260415", 120.0, 36.0, 30.0)]
        self.assertIsNone(rp.cn_series(rows)["ttm"])

    def test_ttm_across_four_consecutive_quarters(self):
        t = rp.cn_series(self.rows())["ttm"]
        self.assertEqual(t["revenue"] / _Y, 460.0)
        self.assertEqual(t["label"], "TTM to Q4 2024")


    def wuliangye(self):
        return [
            cn_row("20231231", "20240425", 830.0, 300.0, 230.0),
            cn_row("20240331", "20240429", 250.0, 90.0, 70.0),
            cn_row("20240630", "20240828", 500.0, 180.0, 140.0),
            cn_row("20241231", "20250426", 890.0, 320.0, 250.0),
            # April 2026: the 2025 interims are refiled, much lower...
            cn_row("20250331", "20250428", 250.0, 90.0, 70.0),
            cn_row("20250630", "20250828", 500.0, 180.0, 140.0),
            cn_row("20250630", "20260430", 230.0, 85.0, 65.0),
            # ...and FY2025 is filed once, already on those figures.
            cn_row("20251231", "20260430", 405.0, 140.0, 93.0),
        ]

    def test_with_no_comparative_a_year_across_a_restatement_is_withheld(self):
        """The fallback. Nothing says whether the prior year is on the same
        basis, so no rate is printed and the bar is marked."""
        out = rp.cn_series(self.wuliangye())
        fy = {r["label"]: r for r in out["annual"]["rows"]}
        self.assertEqual(fy["FY2025"]["revenue"] / _Y, 405.0)
        self.assertIsNone(fy["FY2025"]["rev_yoy"])
        self.assertEqual(fy["FY2025"]["rev_yoy_reason"], "restatement_mix")
        self.assertEqual(fy["FY2025"]["basis_break"], "restatement_mix")
        self.assertIsNone(out["annual"]["revenue"]["cagr"])
        self.assertEqual(fy["FY2024"]["rev_yoy"], 7.2)              # 830 -> 890, untouched
        self.assertNotIn("basis_break", fy["FY2024"])

    def test_the_companys_own_comparative_settles_it(self):
        """Wuliangye, as it actually is. The FY2025 report restates FY2024 as
        the SAME ¥89.2bn, so the two years are on one basis and the -54.5% is
        the reported change — Eastmoney prints -54.55%. An earlier version of
        this code withheld it as "a collapse that never happened"; the
        comparative shows it did, on the company's own figures."""
        comp = [cn_row("20241231", "20260430", 890.0, 320.0, 250.0)]
        out = rp.cn_series(self.wuliangye(), comp)
        fy = {r["label"]: r for r in out["annual"]["rows"]}
        self.assertEqual(fy["FY2025"]["rev_yoy"], -54.5)
        self.assertIsNone(fy["FY2025"]["rev_yoy_reason"])
        self.assertNotIn("basis_break", fy["FY2025"])               # no hatching: same basis
        self.assertNotIn("base_restated", fy["FY2025"])
        self.assertIsNotNone(out["annual"]["revenue"]["cagr"])

    def test_a_restated_comparative_is_the_base_and_says_so(self):
        """Ping An and IFRS 17: 2022 is ¥1,110.6bn as filed, ¥880.4bn as
        restated beside 2023's ¥913.8bn. -17.7% against the original; +3.8%
        as reported."""
        rows = [cn_row("20221231", "20230316", 11105.7, 1000.0, 837.7),
                cn_row("20231231", "20240322", 9137.9, 1100.0, 856.6),
                cn_row("20241231", "20250320", 10289.2, 1500.0, 1266.1)]
        comp = [cn_row("20221231", "20240322", 8803.5, 1300.0, 1110.1),
                cn_row("20231231", "20250320", 9137.9, 1100.0, 856.6)]
        out = rp.cn_series(rows, comp)
        fy = {r["label"]: r for r in out["annual"]["rows"]}
        self.assertEqual(fy["FY2023"]["rev_yoy"], 3.8)
        self.assertEqual(fy["FY2023"]["net_yoy"], -22.8)            # 856.6 against restated 1,110.1
        self.assertAlmostEqual(fy["FY2023"]["base_restated"]["revenue"] / _Y, 8803.5)
        self.assertEqual(fy["FY2022"]["revenue"] / _Y, 11105.7)     # the chart still shows what was filed
        self.assertEqual(fy["FY2024"]["rev_yoy"], 12.6)             # unrestated comparative: plain growth
        self.assertNotIn("base_restated", fy["FY2024"])
        self.assertIsNone(out["annual"]["revenue"]["cagr"])          # ends are on different bases
        self.assertTrue(any("as the company restated it" in x for x in out["limits"]))

    def test_a_comparative_published_later_than_the_report_is_not_used(self):
        """Only the comparative printed beside the year counts as its base."""
        rows = [cn_row("20221231", "20230316", 1000.0, 100.0, 80.0),
                cn_row("20231231", "20240322", 1100.0, 110.0, 90.0)]
        comp = [cn_row("20221231", "20250320", 500.0, 50.0, 40.0)]      # restated a year too late
        fy = {r["label"]: r for r in rp.cn_series(rows, comp)["annual"]["rows"]}
        self.assertEqual(fy["FY2023"]["rev_yoy"], 10.0)

    def test_empty_input_means_no_card(self):
        self.assertIsNone(rp.cn_series([]))


class QuarterlyAcrossARestatement(unittest.TestCase):
    """Wuliangye, in ¥100m rounded to one decimal (so growth is good to ±0.1pp):
    2025 interims restated on 2026-04-30."""

    def rows(self):
        return [cn_row("20240331", "20240429", 348.3, 194.5, 140.5),
                cn_row("20240630", "20240829", 506.5, 263.9, 190.6),
                cn_row("20240930", "20241031", 679.2, 345.1, 249.3),
                cn_row("20241231", "20250426", 891.8, 442.0, 318.5),
                cn_row("20250331", "20250426", 369.4, 204.5, 148.6),
                cn_row("20250630", "20250828", 527.7, 267.5, 194.9),
                cn_row("20250630", "20260430", 235.1, 64.1, 46.2),
                cn_row("20250930", "20251031", 609.5, 294.8, 215.1),
                cn_row("20250930", "20260430", 306.4, 89.1, 64.8),
                cn_row("20251231", "20260430", 405.3, 122.5, 89.5),
                cn_row("20260331", "20260430", 228.4, 106.6, 80.6),
                cn_row("20260630", "20260829", 284.2, 119.9, 87.5)]

    def singles(self):
        return [cn_row("20250630", "20260430", 64.2, 2.55, 2.08)]

    def published(self, **over):
        pub = {"20250930": -58.72, "20251231": -53.48, "20260331": 33.67, "20260630": -13.16}
        pub.update(over)
        return [{"end_date": e, "ann_date": "20260829", "update_flag": "1", "q_sales_yoy": v}
                for e, v in pub.items()]

    def q(self, **kw):
        return {r["label"]: r for r in rp.cn_series(self.rows(), **kw)["quarterly"]["rows"]}

    def test_unconfirmed_growth_across_the_restatement_stays_withheld(self):
        q = self.q()
        self.assertIsNone(q["Q3 2025"]["rev_yoy"])
        self.assertEqual(q["Q3 2025"]["rev_yoy_reason"], "restatement_mix")

    def test_growth_is_released_when_it_equals_the_published_like_for_like(self):
        q = self.q(single_rows=self.singles(), indicator_rows=self.published())
        self.assertAlmostEqual(q["Q3 2025"]["rev_yoy"], -58.7, delta=0.15)
        self.assertAlmostEqual(q["Q1 2026"]["rev_yoy"], 33.7, delta=0.15)
        self.assertAlmostEqual(q["Q2 2026"]["rev_yoy"], -13.2, delta=0.15)
        self.assertNotIn("basis_break", q["Q2 2026"])

    def test_a_published_figure_that_disagrees_releases_nothing(self):
        """The published number is a check on ours, never a substitute for it."""
        q = self.q(single_rows=self.singles(), indicator_rows=self.published(**{"20260630": 5.0}))
        self.assertIsNone(q["Q2 2026"]["rev_yoy"])
        self.assertEqual(q["Q2 2026"]["rev_yoy_reason"], "restatement_mix")

    def test_only_the_lines_that_were_confirmed_are_released(self):
        q = self.q(single_rows=self.singles(), indicator_rows=self.published())
        self.assertIsNone(q["Q2 2026"]["op_yoy"])            # no q_op_yoy was published
        self.assertEqual(q["Q2 2026"]["op_yoy_reason"], "restatement_mix")

    def test_the_updated_indicator_row_wins_over_the_original(self):
        rows = self.published() + [{"end_date": "20260630", "ann_date": "20260829",
                                    "update_flag": "0", "q_sales_yoy": 5.0}]
        q = self.q(single_rows=self.singles(), indicator_rows=rows)
        self.assertAlmostEqual(q["Q2 2026"]["rev_yoy"], -13.2, delta=0.15)

    def test_trailing_twelve_months_returns_once_the_quarters_are_whole(self):
        out = rp.cn_series(self.rows(), single_rows=self.singles(), indicator_rows=self.published())
        self.assertAlmostEqual(out["ttm"]["revenue"] / _Y, 306.4 - 235.1 + 405.3 - 306.4 + 284.2, places=1)


class YearAgoIsFoundByDate(unittest.TestCase):
    def test_a_missing_quarter_does_not_shift_the_comparison(self):
        """Four rows back from the far side of a hole is a different quarter."""
        ends = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-09-30"]
        rows = [{"label": e, "end": e, "revenue": 100.0 + 10 * i} for i, e in enumerate(ends)]
        out = rp._finish(rows, 4.0)["rows"]
        self.assertAlmostEqual(out[-1]["rev_yoy"], 25.0)     # 150 on Q3 2024's 120 — not on Q2's 110
        self.assertIsNone(rp._finish([dict(r) for r in rows[:4]] + [{"label": "x", "end": "2025-06-30", "revenue": 1.0}] , 4.0)["rows"][0]["rev_yoy"])


if __name__ == "__main__":
    unittest.main()

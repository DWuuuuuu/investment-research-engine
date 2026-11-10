"""Offline tests for product/quarterly_growth.py and the growth rule in peers.py.

Each test pins a failure actually found while building this (2026-09-15).
"""
from __future__ import annotations

import unittest
from datetime import datetime

from product.quarterly_growth import cn_quarterly, label_key, pct_growth, yoy_rows
from product.peers import _growth as peers_growth
from product.screener import _fiscal_quarter_label, _fy_end_month


def inc(end, filed, rev, eps, upd="1"):
    return {"end_date": end, "ann_date": filed, "f_ann_date": filed, "update_flag": upd,
            "total_revenue": rev, "diluted_eps": eps, "basic_eps": eps}


class GrowthRuleTests(unittest.TestCase):
    def test_ordinary_growth(self):
        self.assertEqual(pct_growth(3.32, 1.69), (96.4, None))    # AVGO adjusted EPS

    def test_loss_base_is_not_meaningful(self):
        self.assertEqual(pct_growth(0.42, -0.10), (None, "negative_base"))

    def test_profit_to_loss_is_not_meaningful(self):
        """Intel +0.02 -> -0.10 printed as '-600%'."""
        self.assertEqual(pct_growth(-0.10, 0.02), (None, "turned_to_loss"))

    def test_peer_table_uses_the_same_rule(self):
        for cur, prv in ((3.32, 1.69), (0.42, -0.10), (-0.10, 0.02)):
            self.assertEqual(pct_growth(cur, prv), peers_growth(cur, prv))


class PairingAndTrendTests(unittest.TestCase):
    def test_pairs_same_fiscal_quarter_a_year_earlier(self):
        eps = {"Q3 2026": 3.32, "Q2 2026": 2.44, "Q3 2025": 1.69, "Q2 2025": 1.58}
        rows = yoy_rows(eps, {}, ["Q3 2026", "Q2 2026"])
        self.assertEqual(rows[0]["prior_label"], "Q3 2025")
        self.assertEqual(rows[0]["eps_yoy"], 96.4)
        self.assertEqual(rows[0]["eps_yoy_trend"], "up")          # 96.4 vs 54.4

    def test_unparseable_label_never_pairs(self):
        self.assertIsNone(label_key("Sep 2026"))
        rows = yoy_rows({"Sep 2026": 1.0}, {}, ["Sep 2026"])
        self.assertIsNone(rows[0]["eps_yoy"])

    def test_no_trend_arrow_across_a_restatement(self):
        eps = {"Q2 2026": 2.0, "Q1 2026": 1.0, "Q2 2025": 1.0, "Q1 2025": 1.0}
        rows = yoy_rows(eps, {}, ["Q2 2026", "Q1 2026"], restated={"Q1 2025"})
        self.assertTrue(rows[1]["restated"])
        self.assertIsNone(rows[0]["eps_yoy_trend"])


class RestatementTests(unittest.TestCase):
    """Synthetic data shaped like Wuliangye 000858.SZ: 2025 restated on
    2026-04-30, 2024 not restated, Q1 2025 present only in its original form."""

    @staticmethod
    def rows():
        return [
            inc("20240331", "20240429", 34.83e9, 3.618),
            inc("20240630", "20240829", 50.65e9, 4.910),
            inc("20240930", "20241031", 67.92e9, 6.423),
            inc("20241231", "20250426", 89.18e9, 8.206),
            inc("20250331", "20250426", 36.94e9, 3.828),                 # original only
            inc("20250630", "20250828", 52.77e9, 5.022),                 # original
            inc("20250630", "20260430", 23.51e9, 1.191),                 # restated
            inc("20250930", "20251031", 60.95e9, 5.542),                 # original
            inc("20250930", "20260430", 30.64e9, 1.668),                 # restated
            inc("20251231", "20260430", 40.53e9, 2.307),                 # born restated
            inc("20260331", "20260430", 22.84e9, 2.077),
            inc("20260630", "20260829", 28.42e9, 2.255),
        ]

    def test_restated_periods_and_event_detected(self):
        q = cn_quarterly(self.rows())
        self.assertEqual(q["restated_periods"], ["20250630", "20250930"])
        self.assertEqual(q["restatement_events"], ["20260430"])

    def test_mixed_vintage_quarter_is_withheld_not_negative(self):
        """H1 2025 (restated, 23.51bn) minus Q1 2025 (original, 36.94bn) would
        be -13.4bn of quarterly revenue."""
        q = cn_quarterly(self.rows())
        by = {r["label"]: r for r in q["rows"]}
        self.assertIsNone(by["Q2 2025"]["rev"])
        self.assertEqual(by["Q2 2025"]["rev_yoy_reason"], "restatement_mix")
        for r in q["rows"]:
            self.assertTrue(r["rev"] is None or r["rev"] >= 0)

    def test_quarter_born_on_restated_basis_is_still_flagged(self):
        """FY2025 was filed once, restated from birth — the first version only
        flagged periods filed twice and missed this."""
        by = {r["label"]: r for r in cn_quarterly(self.rows())["rows"]}
        self.assertTrue(by["Q4 2025"]["restated"])
        self.assertTrue(by["Q2 2026"]["restated"])

    def test_a_first_quarter_superseded_by_the_restatement_is_withheld(self):
        """Q1 2025 is ¥36.9bn as filed. The company then restated H1 and 9M
        2025 downward and never refiled Q1, so the figure on file is the
        superseded one — Eastmoney carries ¥17.1bn, and -50.9% where the
        original gives +6.1%. Printing +6.1% was printing a number the company
        has withdrawn. (This test used to assert exactly that +6.1%.)"""
        q = cn_quarterly(self.rows())
        by = {r["label"]: r for r in q["rows"]}
        self.assertIsNone(by["Q1 2025"]["rev"])
        self.assertIsNone(by["Q1 2025"]["rev_yoy"])
        self.assertEqual(by["Q1 2025"]["rev_yoy_reason"], "superseded_by_restatement")
        # ...and the quarter a year later has no base to stand on.
        self.assertEqual(by["Q1 2026"]["rev_yoy_reason"], "superseded_by_restatement")

    def test_a_first_quarter_in_a_year_with_no_restatement_is_untouched(self):
        clean = [inc("20240331", "20240429", 34.83e9, 3.618), inc("20250331", "20250426", 36.94e9, 3.828)]
        by = {r["label"]: r for r in cn_quarterly(clean)["rows"]}
        self.assertAlmostEqual(by["Q1 2025"]["rev_yoy"], 6.1, places=1)

    def test_restated_quarters_against_an_unrestated_prior_year_match_the_published_figure(self):
        """Eastmoney's single-quarter change for Q3 and Q4 2025: -58.72%, -53.48%."""
        by = {r["label"]: r for r in cn_quarterly(self.rows())["rows"]}
        self.assertAlmostEqual(by["Q3 2025"]["rev_yoy"], -58.7, places=1)
        self.assertAlmostEqual(by["Q4 2025"]["rev_yoy"], -53.5, places=1)

    def test_clean_company_has_no_flags(self):
        clean = [inc(f"{y}{p}", f"{y}{p}", 10e9 * (i + 1), 1.0 * (i + 1))
                 for y in ("2025", "2026") for i, p in enumerate(("0331", "0630", "0930", "1231"))]
        q = cn_quarterly(clean)
        self.assertEqual(q["restated_periods"], [])
        self.assertFalse(any(r.get("restated") for r in q["rows"]))


def single(end, filed, rev, net, upd="1"):
    """A single-quarter statement (Tushare income report_type 2): no EPS."""
    return {"end_date": end, "ann_date": filed, "f_ann_date": filed, "update_flag": upd,
            "total_revenue": rev, "n_income_attr_p": net, "diluted_eps": None, "basic_eps": None}


class RestatedQuarterFillTests(unittest.TestCase):
    """The quarters a restatement makes underivable by subtraction, recovered
    from the company's restated single-quarter statement. Wuliangye figures."""

    @staticmethod
    def rows():
        rows = RestatementTests.rows()
        net = {("20250630", "20260430"): 4.624e9, ("20250331", "20250426"): 14.86e9}
        for r in rows:
            r["n_income_attr_p"] = net.get((r["end_date"], r["f_ann_date"]))
        return rows

    @staticmethod
    def singles():
        return [single("20250331", "20250426", 36.94e9, 14.86e9, "0"),      # superseded, never refiled
                single("20250630", "20250828", 15.83e9, 4.632e9, "0"),      # original
                single("20250630", "20260430", 6.424e9, 0.208e9)]           # restated

    def by(self, singles):
        return {r["label"]: r for r in cn_quarterly(self.rows(), singles)["rows"]}

    def test_restated_second_quarter_comes_from_its_own_statement(self):
        by = self.by(self.singles())
        self.assertAlmostEqual(by["Q2 2025"]["rev"], 6.424e9)
        self.assertAlmostEqual(by["Q2 2025"]["rev_yoy"], -59.4, places=1)       # Eastmoney -59.38

    def test_restated_first_quarter_is_half_year_less_second_quarter(self):
        by = self.by(self.singles())
        self.assertAlmostEqual(by["Q1 2025"]["rev"], 23.51e9 - 6.424e9)          # ¥17.1bn, not ¥36.9bn
        self.assertAlmostEqual(by["Q1 2025"]["rev_yoy"], -50.9, places=1)

    def test_the_following_year_now_has_a_base(self):
        by = self.by(self.singles())
        self.assertAlmostEqual(by["Q1 2026"]["rev_yoy"], 33.7, places=1)         # published +33.67
        self.assertAlmostEqual(by["Q2 2026"]["rev_yoy"], -13.2, places=0)        # published -13.16

    def test_eps_of_a_filled_quarter_uses_the_restated_filing_share_count(self):
        by = self.by(self.singles())
        self.assertAlmostEqual(by["Q2 2025"]["eps"], 0.208e9 * 1.191 / 4.624e9, places=3)
        self.assertAlmostEqual(by["Q1 2025"]["eps"] + by["Q2 2025"]["eps"], 1.191, places=3)

    def test_an_original_vintage_statement_never_fills_a_restated_quarter(self):
        """Only the statement filed with the restated half-year may stand in."""
        by = self.by(self.singles()[:2])
        self.assertIsNone(by["Q2 2025"]["rev"])
        self.assertEqual(by["Q2 2025"]["rev_yoy_reason"], "restatement_mix")
        self.assertEqual(by["Q1 2025"]["rev_yoy_reason"], "superseded_by_restatement")

    def test_without_statements_nothing_changes(self):
        self.assertEqual(cn_quarterly(self.rows())["rows"], cn_quarterly(self.rows(), [])["rows"])
        self.assertNotIn("filled", cn_quarterly(self.rows()))

    def test_filled_quarters_are_named(self):
        q = cn_quarterly(self.rows(), self.singles())
        self.assertEqual(set(q["filled"]), {"Q1 2025", "Q2 2025"})


class FiscalYearEndTests(unittest.TestCase):
    def test_helper_returns_a_month_not_none(self):
        """A broad except once swallowed a missing-import NameError here and
        returned None for every ticker, reverting all quarter labels."""
        self.assertEqual(_fy_end_month({"lastFiscalYearEnd": 1762041600}), 11)   # AVGO, 2025-11-02

    def test_labels_depend_on_fiscal_calendar(self):
        self.assertEqual(_fiscal_quarter_label(datetime(2026, 9, 2), 11), "Q3 2026")   # AVGO
        self.assertEqual(_fiscal_quarter_label(datetime(2026, 8, 26), 1), "Q2 2027")   # NVDA


if __name__ == "__main__":
    unittest.main()


class ClarityTests(unittest.TestCase):
    """Added 2026-09-15: every unscored growth figure must say WHY."""

    def test_missing_year_ago_period_is_explained(self):
        rows = yoy_rows({"Q1 2026": 0.41}, {"Q1 2026": 50.8e9}, ["Q1 2026"])
        self.assertEqual(rows[0]["eps_yoy_reason"], "no_prior_period")
        self.assertEqual(rows[0]["rev_yoy_reason"], "no_prior_period")

    def test_gate_check_never_shows_a_bare_na_for_a_known_reason(self):
        from product.screener import _gate_growth_check
        for reason in ("negative_base", "turned_to_loss", "restatement_mix",
                       "negative_discrete_revenue", "missing_prior_filing", "no_prior_period"):
            c = _gate_growth_check("EPS Growth", None, reason, False, "note")
            self.assertIn(c["display"], ("n/m", "n/a"), reason)
            self.assertNotEqual(c["note"], "note", reason)

    def test_cross_restatement_value_is_shown_but_not_scored(self):
        from product.screener import _gate_growth_check
        c = _gate_growth_check("EPS Growth", 12.0, None, True, "note")
        self.assertIsNone(c["pass"])
        self.assertIn("⚠", c["display"])

    def test_fiscal_year_label_names_the_end_month_not_a_year_number(self):
        import pandas as pd
        from product.screener import _fy_ended_label
        df = pd.DataFrame({pd.Timestamp("2025-10-31"): [1.0]})
        self.assertEqual(_fy_ended_label(df), "FY ended Oct 2025")
        self.assertIsNone(_fy_ended_label(None))

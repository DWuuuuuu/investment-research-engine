"""US analyst forecasts — the shaping rules, on AAOI's numbers of 2026-10-12."""
import unittest
from datetime import date

from product import us_forecasts as uf

TODAY = date(2026, 10, 12)


def firm(d, name, to="Buy", frm="Buy", action="main", target=0.0, prior=0.0, ta="Maintains"):
    return {"date": d, "firm": name, "to": to, "from": frm, "action": action,
            "target_action": ta, "target": target, "prior_target": prior}


class Targets(unittest.TestCase):
    PT = {"current": 104.0, "high": 220.0, "low": 109.0, "mean": 163.4, "median": 178.0}

    def test_each_level_is_set_against_todays_price_not_the_feeds(self):
        t = uf.targets(self.PT, 5, 109.64)
        self.assertEqual(t["upside_pct"], 62.3)             # 178 / 109.64
        self.assertEqual(t["upside_low_pct"], -0.6)
        self.assertEqual((t["n"], t["basis"]), (5, "median"))

    def test_mean_stands_in_when_there_is_no_median(self):
        t = uf.targets({"mean": 150.0, "low": 100.0, "high": 200.0}, None, 100.0)
        self.assertEqual((t["basis"], t["upside_pct"], t["n"]), ("mean", 50.0, None))

    def test_a_summary_whose_middle_is_outside_its_range_is_dropped(self):
        self.assertIsNone(uf.targets({"median": 300.0, "low": 100.0, "high": 200.0}, 3, 100.0))

    def test_nothing_in_nothing_out(self):
        self.assertIsNone(uf.targets({}, 3, 100.0))
        self.assertIsNone(uf.targets(None, None, None))


class Ratings(unittest.TestCase):
    ROWS = [{"period": "0m", "strongBuy": 2, "buy": 1, "hold": 3, "sell": 0, "strongSell": 0},
            {"period": "-1m", "strongBuy": 2, "buy": 2, "hold": 2, "sell": 0, "strongSell": 0},
            {"period": "-3m", "strongBuy": 1, "buy": 1, "hold": 3, "sell": 1, "strongSell": 0}]

    def test_counts_are_grouped(self):
        r = uf.ratings(self.ROWS)
        self.assertEqual((r["positive"], r["neutral"], r["negative"], r["n"]), (3, 3, 0, 6))

    def test_compared_with_the_oldest_month_on_file(self):
        b = uf.ratings(self.ROWS)["before"]
        self.assertEqual((b["months_ago"], b["positive"], b["negative"]), (3, 2, 1))

    def test_no_current_month_means_no_tile(self):
        self.assertIsNone(uf.ratings(self.ROWS[1:]))
        self.assertIsNone(uf.ratings([{"period": "0m", "strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}]))

    def test_rating_words_are_bucketed(self):
        self.assertEqual(uf.rating_bucket("Overweight"), "positive")
        self.assertEqual(uf.rating_bucket("Sector Perform"), "neutral")
        self.assertEqual(uf.rating_bucket("Underperform"), "negative")
        self.assertIsNone(uf.rating_bucket("Speculative"))


class Firms(unittest.TestCase):
    ROWS = [firm("2026-08-07", "Rosenblatt", target=220.0, prior=220.0),
            firm("2026-08-07", "Needham", target=190.0, prior=220.0, ta="Lowers"),
            firm("2026-07-22", "Needham", target=220.0, prior=260.0, ta="Lowers"),
            firm("2026-02-27", "B. Riley Securities", to="Neutral", frm="Sell", action="up", target=54.0, prior=15.0),
            firm("2025-06-01", "Old Firm", target=20.0)]
    PRICES = {"2026-08-07": 135.63, "2026-02-27": 84.23}

    def out(self, price=103.94):
        return uf.firms(self.ROWS, price, TODAY, self.PRICES.get)

    def test_one_row_per_firm_its_latest(self):
        f = self.out()
        self.assertEqual([x["firm"] for x in f["items"]], ["Rosenblatt", "Needham", "B. Riley Securities"])
        self.assertEqual(next(x for x in f["items"] if x["firm"] == "Needham")["target"], 190.0)

    def test_a_view_more_than_a_year_old_is_not_a_current_view(self):
        self.assertNotIn("Old Firm", [x["firm"] for x in self.out()["items"]])

    def test_each_target_carries_its_age_and_what_the_stock_did_since(self):
        r = self.out()["items"][0]
        self.assertEqual((r["age_days"], r["price_then"], r["stock_since_pct"]), (66, 135.63, -23.4))
        self.assertEqual(r["target_vs_price_pct"], 111.7)

    def test_unchanged_prior_target_is_not_shown_as_a_change(self):
        items = {x["firm"]: x for x in self.out()["items"]}
        self.assertIsNone(items["Rosenblatt"]["prior_target"])
        self.assertEqual(items["Needham"]["prior_target"], 220.0)

    def test_stale_when_the_newest_change_is_old_or_the_stock_has_moved(self):
        self.assertTrue(self.out()["stale"])                                    # 66 days, -23%
        fresh = uf.firms([firm("2026-10-06", "Barclays", target=665.0)], 451.0, TODAY, {"2026-10-06": 482.3}.get)
        self.assertFalse(fresh["stale"])                                        # 6 days, -6%
        moved = uf.firms([firm("2026-10-06", "Barclays", target=665.0)], 380.0, TODAY, {"2026-10-06": 482.3}.get)
        self.assertTrue(moved["stale"])                                         # 6 days, -21%

    def test_a_zero_target_is_a_missing_target(self):
        f = uf.firms([firm("2026-09-01", "Stifel", action="init", target=0.0)], 100.0, TODAY)
        self.assertIsNone(f["items"][0]["target"])
        self.assertEqual((f["n_with_target"], f["items"][0]["action"]), (0, "initiated"))

    def test_an_upgrade_is_marked_as_a_change_of_rating(self):
        b = next(x for x in self.out()["items"] if x["firm"].startswith("B. Riley"))
        self.assertTrue(b["changed_rating"])
        self.assertEqual((b["action"], b["from_rating"], b["rating_bucket"]), ("upgraded", "Sell", "neutral"))


def period(code, end, eps, rev, trend=None, revisions=None):
    return {"period": code, "end": end, "eps": eps, "revenue": rev, "trend": trend, "revisions": revisions}


class Estimates(unittest.TestCase):
    P = [period("0y", "2026-12-31",
                {"avg": 0.68, "low": 0.47, "high": 0.85, "yearAgoEps": -0.26, "numberOfAnalysts": 6},
                {"avg": 1038.4e6, "low": 1000e6, "high": 1092e6, "yearAgoRevenue": 455.7e6, "numberOfAnalysts": 5},
                {"current": 0.68, "7daysAgo": 0.68, "30daysAgo": 0.888, "90daysAgo": 0.888},
                {"upLast30days": 2, "downLast30days": 4}),
         period("+1y", "2027-12-31",
                {"avg": 4.602, "low": 3.52, "high": 8.08, "yearAgoEps": 0.68, "numberOfAnalysts": 5},
                {"avg": 2663.3e6, "yearAgoRevenue": 1038.4e6, "numberOfAnalysts": 4}),
         period("0q", "2026-09-30", {"avg": 0.143, "yearAgoEps": -0.09, "numberOfAnalysts": 6}, None)]

    def est(self, **kw):
        return {e["period"]: e for e in uf.estimates(self.P, 103.94, **kw)}

    def test_no_growth_percentage_off_a_loss(self):
        """Yahoo prints +362% for $0.68 against a loss of $0.26."""
        e = self.est()["0y"]["eps"]
        self.assertIsNone(e["growth"])
        self.assertEqual(e["growth_reason"], "negative_base")

    def test_growth_off_a_profit_is_given(self):
        e = self.est()
        self.assertAlmostEqual(e["+1y"]["eps"]["growth"], 576.8, places=1)
        self.assertAlmostEqual(e["0y"]["revenue"]["growth"], 127.9, places=1)

    def test_forward_pe_uses_todays_price(self):
        e = self.est()
        self.assertEqual((e["0y"]["pe"], e["+1y"]["pe"]), (152.9, 22.6))
        self.assertNotIn("pe", e["0q"])                      # a quarter's EPS is not a year's

    def test_no_pe_on_a_forecast_loss_or_across_currencies(self):
        loss = uf.estimates([period("0y", "2026-12-31", {"avg": -0.5}, None)], 10.0)[0]
        self.assertEqual(loss["pe_reason"], "loss_forecast")
        self.assertNotIn("pe", self.est(same_currency=False)["0y"])

    def test_labels_come_from_the_period_end(self):
        e = self.est(quarter_label=lambda d: f"Q{(d.month - 1) // 3 + 1} {d.year}")
        self.assertEqual((e["0y"]["label"], e["+1y"]["label"], e["0q"]["label"]), ("FY2026E", "FY2027E", "Q3 2026E"))

    def test_estimate_drift_and_revision_counts(self):
        e = self.est()["0y"]
        self.assertEqual((e["eps_trend"]["direction_90"], e["eps_trend"]["change_90_pct"]), ("down", -23.4))
        self.assertEqual(e["revisions_30"], {"up": 2, "down": 4})

    def test_a_cent_of_drift_is_flat(self):
        p = [period("0y", "2026-12-31", {"avg": 24.131}, None, {"current": 24.131, "30daysAgo": 24.137, "90daysAgo": 22.81})]
        tr = uf.estimates(p, 300.0)[0]["eps_trend"]
        self.assertEqual((tr["direction_30"], tr["direction_90"]), ("flat", "up"))

    def test_order_is_quarter_then_year(self):
        self.assertEqual([e["period"] for e in uf.estimates(self.P, 100.0)], ["0q", "0y", "+1y"])


class EarningsDates(unittest.TestCase):
    def test_next_date_and_days_to_it(self):
        n = uf.next_earnings({"Earnings Date": [date(2026, 11, 6)], "Earnings Average": 0.143}, TODAY, True)
        self.assertEqual((n["date"], n["days"], n["estimated"], n["eps_avg"]), ("2026-11-06", 25, True, 0.143))

    def test_a_date_already_past_is_not_next(self):
        self.assertIsNone(uf.next_earnings({"Earnings Date": [date(2026, 8, 6)]}, TODAY))

    def test_surprise_percentage_only_against_a_real_estimate(self):
        s = uf.surprises([{"quarter": "2026-06-30", "actual": 0.06, "estimate": 0.015},
                          {"quarter": "2026-03-31", "actual": -0.07, "estimate": -0.048},
                          {"quarter": "2025-12-31", "actual": 4.31, "estimate": 3.887}])
        by = {x["end"]: x for x in s["items"]}
        self.assertEqual((by["2026-06-30"]["result"], by["2026-06-30"]["surprise_pct"]), ("beat", None))
        self.assertEqual(by["2026-03-31"]["result"], "miss")
        self.assertEqual(by["2025-12-31"]["surprise_pct"], 10.9)
        self.assertEqual((s["beats"], s["misses"], s["n"]), (2, 1, 3))


class Paths(unittest.TestCase):
    EST = uf.estimates(Estimates.P, 103.94)
    HIST = [{"end": "2023-12-31", "revenue": 217.6e6}, {"end": "2024-12-31", "revenue": 249.4e6},
            {"end": "2025-12-31", "revenue": 455.7e6}]

    def test_reported_years_then_estimates(self):
        pts = uf.revenue_path(self.HIST, self.EST)
        self.assertEqual([p["label"] for p in pts], ["FY2023", "FY2024", "FY2025", "FY2026E", "FY2027E"])
        self.assertEqual([p["est"] for p in pts], [False, False, False, True, True])

    def test_a_year_the_statements_have_not_loaded_is_filled_from_the_estimate_table(self):
        """Costco, October 2026: FY2026 filed, statements still end at FY2025."""
        pts = uf.revenue_path(self.HIST[:2], self.EST)
        self.assertEqual([p["label"] for p in pts], ["FY2023", "FY2024", "FY2025", "FY2026E", "FY2027E"])
        self.assertTrue(pts[2]["from_estimate_table"])
        self.assertEqual(pts[2]["v"], 455.7e6)

    def test_eps_never_mixes_in_reported_gaap(self):
        pts = uf.eps_path(self.EST)
        self.assertEqual([(p["label"], p["v"]) for p in pts], [("FY2025", -0.26), ("FY2026E", 0.68), ("FY2027E", 4.602)])


class Build(unittest.TestCase):
    def test_statement_history_is_dropped_when_revenue_estimates_are_in_another_currency(self):
        """TSMC: price and statements in USD, revenue estimates in TWD."""
        out = uf.build(price=451.0, today=TODAY, periods=Estimates.P, history=Paths.HIST,
                       currency="USD", estimate_currency="USD", revenue_currency="TWD")
        self.assertEqual([p["label"] for p in out["revenue_path"]], ["FY2025", "FY2026E", "FY2027E"])
        self.assertTrue(out["revenue_path"][0]["from_estimate_table"])
        self.assertIn("pe", out["estimates"][1])              # EPS and price share a currency

    def test_a_stock_nobody_covers_has_no_card(self):
        self.assertIsNone(uf.build(price=10.0, today=TODAY))


if __name__ == "__main__":
    unittest.main()

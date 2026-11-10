"""Per-stock forecasts: broker consensus out of the shared report_rc cache, and
the company's own guidance. No network — rows are handed in."""
import unittest

from product import cn_forecasts as cf
from product.cn_reports import _rating_bucket

CODE = "688825.SH"
WAN = 10_000.0


def rc(org, day, year, np=None, rev=None, eps=None, rating="买入", lo=None, hi=None,
       code=CODE, rtype="个股", title=None):
    return {"ts_code": code, "org_name": org, "report_date": day, "quarter": f"{year}Q4",
            "np": np, "op_rt": rev, "eps": eps, "rating": rating, "min_price": lo, "max_price": hi,
            "report_type": rtype, "report_title": title or f"{org} {day}"}


def run(rows, **kw):
    kw.setdefault("market_cap", 1_000e8)
    kw.setdefault("price", 100.0)
    kw.setdefault("actual", {"year": 2025, "revenue": 600e8, "net_parent": 70e8})
    return cf.broker_forecasts(CODE, rows, bucket=_rating_bucket, **kw)


class Consensus(unittest.TestCase):
    def test_units_are_wan_and_the_median_is_across_brokers(self):
        out = run([rc("中信", "20260901", 2026, np=1_000_000, rev=7_000_000, eps=2.0),
                   rc("华泰", "20260902", 2026, np=1_200_000, rev=7_400_000, eps=2.4),
                   rc("国君", "20260903", 2026, np=1_100_000, rev=7_200_000, eps=2.2)])
        y = out["years"][0]
        self.assertEqual(y["label"], "FY2026E")
        self.assertEqual(y["net_profit"]["median"], 1_100_000 * WAN)      # ¥11.0bn
        self.assertEqual((y["net_profit"]["low"], y["net_profit"]["high"]),
                         (1_000_000 * WAN, 1_200_000 * WAN))
        self.assertEqual(y["net_profit"]["state"], "consensus")
        self.assertEqual(y["revenue"]["median"], 7_200_000 * WAN)

    def test_a_broker_counts_once_by_its_latest_report(self):
        """Three updates from one desk are one view, not three."""
        out = run([rc("中信", "20260601", 2026, np=500_000),
                   rc("中信", "20260801", 2026, np=900_000),
                   rc("中信", "20260901", 2026, np=1_000_000),
                   rc("华泰", "20260902", 2026, np=1_200_000)])
        y = out["years"][0]
        self.assertEqual(y["n_brokers"], 2)
        self.assertEqual(y["net_profit"]["median"], 1_100_000 * WAN)
        self.assertEqual(out["n_brokers"], 2)
        self.assertEqual(out["n_reports"], 4)

    def test_one_broker_is_not_a_consensus(self):
        y = run([rc("中信", "20260901", 2026, np=1_000_000)])["years"][0]
        self.assertEqual(y["net_profit"]["state"], "single")

    def test_a_unit_error_is_set_aside_not_averaged_in(self):
        """One broker filing in 亿元 where the rest file in 万元."""
        y = run([rc("中信", "20260901", 2026, np=1_000_000),
                 rc("华泰", "20260902", 2026, np=1_200_000),
                 rc("野村", "20260903", 2026, np=110)])["years"][0]
        self.assertEqual(y["net_profit"]["excluded"], ["野村"])
        self.assertEqual(y["net_profit"]["median"], 1_100_000 * WAN)

    def test_two_brokers_ten_times_apart_publish_nothing(self):
        y = run([rc("中信", "20260901", 2026, np=1_000_000),
                 rc("野村", "20260903", 2026, np=50)])["years"][0]
        self.assertEqual(y["net_profit"]["state"], "inconsistent")
        self.assertIsNone(y["net_profit"]["median"])
        self.assertIsNone(y["pe"])

    def test_a_closed_year_is_not_a_forecast(self):
        out = run([rc("中信", "20260901", 2025, np=700_000),
                   rc("中信", "20260901", 2026, np=1_000_000),
                   rc("华泰", "20260901", 2026, np=1_000_000)])
        self.assertEqual([y["year"] for y in out["years"]], [2026])

    def test_sector_pieces_and_other_stocks_are_ignored(self):
        out = run([rc("中信", "20260901", 2026, np=1_000_000, rtype="非个股"),
                   rc("华泰", "20260901", 2026, np=1_000_000, code="600519.SH")])
        self.assertEqual(out["status"], "empty")


class Derived(unittest.TestCase):
    def setUp(self):
        self.rows = [rc("中信", "20260901", 2026, np=1_000_000, rev=7_000_000),
                     rc("华泰", "20260902", 2026, np=1_000_000, rev=7_000_000),
                     rc("中信", "20260901", 2027, np=1_500_000, rev=9_000_000),
                     rc("华泰", "20260902", 2027, np=1_500_000, rev=9_000_000)]

    def test_multiple_is_recomputed_from_todays_market_cap(self):
        """Not the vendor's `pe`, which is each broker's own number on its own day."""
        y = run(self.rows, market_cap=1_000e8)["years"]
        self.assertEqual(y[0]["pe"], 10.0)                    # ¥100bn / ¥10bn
        self.assertEqual(y[1]["pe"], 6.7)

    def test_first_year_grows_off_the_actual_and_later_years_off_each_other(self):
        y = run(self.rows)["years"]
        self.assertEqual((y[0]["np_growth"], y[0]["vs"]), (42.9, "FY2025 actual"))   # 70 -> 100
        self.assertEqual((y[1]["np_growth"], y[1]["vs"]), (50.0, "FY2026E"))

    def test_growth_off_a_loss_year_is_refused(self):
        y = run(self.rows, actual={"year": 2025, "revenue": 600e8, "net_parent": -23e8})["years"]
        self.assertIsNone(y[0]["np_growth"])
        self.assertEqual(y[0]["np_growth_reason"], "negative_base")

    def test_no_like_for_like_actual_means_no_first_year_growth(self):
        y = run(self.rows, actual={"year": 2025, "revenue": None, "net_parent": None})["years"]
        self.assertIsNone(y[0]["np_growth"])
        self.assertEqual(y[1]["np_growth"], 50.0)             # consensus on consensus still stands

    def test_a_forecast_loss_has_no_multiple(self):
        y = run([rc("中信", "20260901", 2026, np=-100_000), rc("华泰", "20260902", 2026, np=-120_000)])["years"][0]
        self.assertIsNone(y["pe"])
        self.assertEqual(y["pe_reason"], "loss_forecast")


class RatingsTargetsRevisions(unittest.TestCase):
    def test_ratings_take_each_brokers_newest_report(self):
        out = run([rc("中信", "20260601", 2026, np=1, rating="中性"),
                   rc("中信", "20260901", 2026, np=1, rating="买入"),
                   rc("华泰", "20260902", 2026, np=1, rating="无")])
        self.assertEqual(out["ratings"]["positive"], 1)
        self.assertEqual(out["ratings"]["unrated"], 1)
        self.assertEqual(out["ratings"]["neutral"], 0)

    def test_targets_say_how_few_brokers_gave_one(self):
        out = run([rc("华泰", "20260901", 2026, np=1_000_000, lo=120, hi=140),
                   rc("野村", "20260902", 2026, np=1_000_000, hi=150),
                   rc("中信", "20260903", 2026, np=1_000_000)], price=100.0)
        t = out["targets"]
        self.assertEqual((t["n"], t["of"]), (2, 3))
        self.assertEqual(t["median"], 140.0)                  # mids 130 and 150
        self.assertEqual(t["upside_pct"], 40.0)
        self.assertEqual((t["low"], t["high"]), (120, 150))

    def test_no_targets_is_reported_as_none_of_n(self):
        t = run([rc("中信", "20260903", 2026, np=1), rc("华泰", "20260903", 2026, np=1)])["targets"]
        self.assertEqual((t["n"], t["of"]), (0, 2))

    def test_revisions_compare_each_broker_with_itself(self):
        out = run([rc("中信", "20260601", 2026, np=800_000), rc("中信", "20260901", 2026, np=1_000_000),
                   rc("华泰", "20260601", 2026, np=1_200_000), rc("华泰", "20260902", 2026, np=1_000_000),
                   rc("国君", "20260601", 2026, np=1_000_000), rc("国君", "20260903", 2026, np=1_004_000),
                   rc("野村", "20260904", 2026, np=1_000_000)])
        self.assertEqual(out["revisions"], {"year": 2026, "up": 1, "down": 1, "flat": 1, "n": 3})


class Coverage(unittest.TestCase):
    def test_empty_carries_how_far_back_it_could_see(self):
        """Uncovered and not-yet-fetched look identical without this."""
        out = cf.broker_forecasts(CODE, [], market_cap=1, price=1, actual=None,
                                  coverage={"covered_from": "20261006", "complete": False,
                                            "covered_days": 7, "horizon_days": 180})
        self.assertEqual(out["status"], "empty")
        self.assertEqual(out["coverage"]["covered_from"], "20261006")
        self.assertFalse(out["coverage"]["complete"])


def g(end, ann, lo, hi, prior=None, kind="预增"):
    return {"end_date": end, "ann_date": ann, "type": kind, "net_profit_min": lo,
            "net_profit_max": hi, "last_parent_net": prior, "change_reason": ""}


class Guidance(unittest.TestCase):
    def test_cxmt_shape_range_in_yuan_and_no_percentage_off_a_loss(self):
        """Tushare prints +2,244% for this row. Off a loss, that is not a number."""
        out = cf.company_guidance([g("20260630", "20260724", 5_000_000, 5_700_000, -233_205.82, "扭亏")],
                                  latest_reported_end="20260331")
        it = out["items"][0]
        self.assertEqual((it["period"], it["type_en"]), ("H1 2026", "turning profitable"))
        self.assertEqual((it["np_low"], it["np_high"]), (500e8, 570e8))
        self.assertIsNone(it["growth_low"])
        self.assertEqual(it["growth_reason"], "negative_base")
        self.assertTrue(it["pending"])
        self.assertTrue(out["has_pending"])

    def test_guidance_for_a_reported_period_is_history(self):
        out = cf.company_guidance([g("20241231", "20250113", 8_570_000, 8_570_000, 7_473_407)],
                                  latest_reported_end="20260630")
        self.assertFalse(out["items"][0]["pending"])
        self.assertEqual(out["items"][0]["growth_low"], 14.7)

    def test_the_latest_filing_for_a_period_wins_and_is_marked_revised(self):
        out = cf.company_guidance([g("20260630", "20260517", 4_000_000, 4_500_000, 100.0),
                                   g("20260630", "20260724", 5_000_000, 5_700_000, 100.0)], "20260331")
        it = out["items"][0]
        self.assertEqual(it["np_low"], 500e8)
        self.assertTrue(it["revised"])
        self.assertEqual((it["first_announced"], it["announced"]), ("20260517", "20260724"))

    def test_a_restated_filing_with_the_same_numbers_is_not_a_revision(self):
        out = cf.company_guidance([g("20260630", "20260517", 5_000_000, 5_700_000, 1.0),
                                   g("20260630", "20260724", 5_000_000, 5_700_000, 1.0)], "20260331")
        self.assertFalse(out["items"][0]["revised"])

    def test_a_missing_base_is_taken_from_the_earlier_filing(self):
        out = cf.company_guidance([g("20241231", "20250103", 8_570_000, 8_570_000, 7_473_407),
                                   g("20241231", "20250113", 8_570_000, 8_570_000, None)], "20260630")
        self.assertEqual(out["items"][0]["growth_low"], 14.7)

    def test_how_the_period_turned_out(self):
        rows = [g("20241231", "20250113", 8_500_000, 8_700_000, 7_473_407)]
        within = cf.company_guidance(rows, "20260630", actuals={"20241231": 862e8})["items"][0]
        above = cf.company_guidance(rows, "20260630", actuals={"20241231": 900e8})["items"][0]
        below = cf.company_guidance(rows, "20260630", actuals={"20241231": 800e8})["items"][0]
        self.assertEqual((within["outcome"], above["outcome"], below["outcome"]),
                         ("within", "above", "below"))

    def test_no_guidance_is_none(self):
        self.assertIsNone(cf.company_guidance([], "20260630"))


if __name__ == "__main__":
    unittest.main()

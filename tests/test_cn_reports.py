"""Sell-side research cache — the fetch plan that left the portal empty for
seven weeks, and the per-stock forecasts read out of the same cache.

No network and no real sleeping: the vendor call and the clock are replaced.
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from product import cn_data, cn_reports as cr

TODAY = date(2026, 10, 12)


class Sandbox(unittest.TestCase):
    """Every test gets its own data directory and a clean module state."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(cr, "_DATA_DIR", d),
            mock.patch.object(cr, "_RAW_CACHE_FILE", d / "raw.json"),
            mock.patch.object(cr, "_BACKFILL_STATE_FILE", d / "plan.json"),
            mock.patch.object(cr, "_ERROR_STATE_FILE", d / "err.json"),
            mock.patch.object(cr, "_PACE_FILE", d / "pace.json"),
            mock.patch.object(cr.time, "sleep", lambda s: self.slept.append(s)),
            mock.patch.object(cr, "_resume_after", lambda s: self.resumes.append(s)),
        ]
        self.resumes = []
        for p in self.patches:
            p.start()
        self.slept = []
        self.saved = dict(cr._state)
        cr._state.update({"building": False, "ts": 0.0, "error": None, "error_ts": 0.0,
                          "progress": 0, "total": 0, "unmapped_ratings": [],
                          "truncated_days": []})
        cr._RAW_MEM.update({"mtime": None, "rows": []})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        cr._state.clear(); cr._state.update(self.saved)
        cr._RAW_MEM.update({"mtime": None, "rows": []})
        self.tmp.cleanup()

    def vendor(self, fn):
        """Replace the Tushare call; record every (start, end) asked for."""
        self.calls = []
        def call(api, params, fields):
            self.calls.append((params["start_date"], params["end_date"]))
            return fn(params["start_date"], params["end_date"])
        return mock.patch.object(cn_data, "call", call)


def row(day, org="中信证券", code="600519.SH", title="t", quarter="2026Q4", **kw):
    return {"report_date": day, "org_name": org, "ts_code": code, "report_title": title,
            "quarter": quarter, "name": "x", **kw}


class Grid(Sandbox):
    def test_newest_first(self):
        g = cr._grid_windows(TODAY)
        self.assertGreater(g[0][0], g[1][0])
        self.assertEqual(sorted(g, reverse=True), g)

    def test_the_same_window_has_the_same_name_tomorrow(self):
        """The bug: windows were measured back from today, so no key ever
        matched the next day and saved progress could not be resumed."""
        today, tomorrow = set(cr._grid_windows(TODAY)), set(cr._grid_windows(TODAY + timedelta(days=1)))
        self.assertGreater(len(today & tomorrow), len(today) - 3)

    def test_only_settled_windows_are_offered(self):
        newest_end = cr._parse_ymd(cr._grid_windows(TODAY)[0][1])
        self.assertLessEqual(newest_end, TODAY - timedelta(days=cr._SETTLE_DAYS + 1))

    def test_reaches_the_horizon_and_no_further(self):
        g = cr._grid_windows(TODAY)
        horizon = TODAY - timedelta(days=cr._HORIZON_DAYS)
        self.assertLessEqual(cr._parse_ymd(g[-1][0]), horizon)
        self.assertGreater(cr._parse_ymd(g[-1][1]), horizon - timedelta(days=cr._GRID_DAYS))

    def test_windows_tile_without_gap_or_overlap(self):
        g = cr._grid_windows(TODAY)
        for (s_new, _), (_, e_old) in zip(g, g[1:]):
            self.assertEqual(cr._parse_ymd(s_new) - cr._parse_ymd(e_old), timedelta(days=1))

    def test_topup_meets_the_grid_exactly(self):
        s, e = cr._topup_range(TODAY)
        newest_end = cr._parse_ymd(cr._grid_windows(TODAY)[0][1])
        self.assertEqual(cr._parse_ymd(s), newest_end + timedelta(days=1))
        self.assertEqual(e, cr._ymd(TODAY))
        self.assertLessEqual((TODAY - cr._parse_ymd(s)).days + 1, cr._GRID_DAYS + cr._SETTLE_DAYS)


class Fetching(Sandbox):
    def test_a_full_response_is_split_without_refetching_the_middle_day(self):
        def fn(s, e):
            return [row(s)] * (cr._ROW_CAP if (s, e) == ("20251027", "20251103") else 10)
        with self.vendor(fn):
            cr._call_rc("20251027", "20251103")
        self.assertEqual(self.calls, [("20251027", "20251103"),
                                      ("20251027", "20251030"), ("20251031", "20251103")])

    def test_an_ordinary_response_costs_one_call(self):
        with self.vendor(lambda s, e: [row(s)] * 4634):          # the real late-October week
            cr._call_rc("20251019", "20251026")
        self.assertEqual(len(self.calls), 1)

    def test_a_single_day_that_is_still_full_is_flagged_not_hidden(self):
        with self.vendor(lambda s, e: [row(s)] * cr._ROW_CAP):
            rows = cr._call_rc("20260430", "20260430")
        self.assertEqual(len(rows), cr._ROW_CAP)
        self.assertEqual(cr._state["truncated_days"], ["20260430"])

    def test_calls_are_paced_before_they_are_made(self):
        """Back-to-back calls used to be refused once each, then retried."""
        clock = [1_000_000.0]
        with self.vendor(lambda s, e: []), \
             mock.patch.object(cr.time, "time", lambda: clock[0]):
            cr._fetch("20261001", "20261005")
            clock[0] += 5                                           # five seconds later
            cr._fetch("20260926", "20260930")
        self.assertEqual(len(self.slept), 1)
        self.assertAlmostEqual(self.slept[0], cr._MIN_GAP - 5, places=3)

    def test_the_gap_survives_a_restart(self):
        """The last call time is on disk: a fresh process must not fire at once."""
        clock = [1_000_000.0]
        with self.vendor(lambda s, e: []), \
             mock.patch.object(cr.time, "time", lambda: clock[0]):
            cr._fetch("20261001", "20261005")
            clock[0] += 10
            self.assertAlmostEqual(cr._load_pace()["last_call"], 1_000_000.0)   # read back from disk
            cr._pace()
        self.assertAlmostEqual(self.slept[-1], cr._MIN_GAP - 10, places=3)

    def test_an_hourly_cap_is_learned_and_never_retried(self):
        """Told '10 an hour', stop now and space every later call six minutes
        apart. A retry would itself be a counted request."""
        def fn(s, e):
            raise cn_data.TushareError("抱歉，您访问接口(report_rc)频率超限(10次/小时)")
        with self.vendor(fn), self.assertRaises(cn_data.TushareError):
            cr._fetch("20261001", "20261005")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(cr._load_pace()["gap"], 365.0)
        self.assertEqual(cr._cooldown_for_error("频率超限(10次/小时)"), 65 * 60)

    def test_a_learned_limit_is_forgotten_after_a_week(self):
        cr._save_pace({"gap": 365.0, "gap_ts": cr.time.time() - 8 * 86400, "last_call": 0.0})
        self.assertEqual(cr._load_pace()["gap"], cr._MIN_GAP)

    def test_a_smaller_limit_never_narrows_the_gap(self):
        cr._learn_cap("频率超限(10次/小时)")
        cr._learn_cap("频率超限(1次/分钟)")
        self.assertEqual(cr._load_pace()["gap"], 365.0)

    def test_a_per_minute_refusal_is_retried(self):
        n = {"i": 0}
        def fn(s, e):
            n["i"] += 1
            if n["i"] == 1:
                raise cn_data.TushareError("抱歉，您访问接口(report_rc)频率超限(1次/分钟)")
            return [row(s)]
        with self.vendor(fn):
            self.assertEqual(len(cr._fetch("20261001", "20261005")), 1)
        self.assertEqual(len(self.calls), 2)

    def test_a_daily_cap_is_not_retried(self):
        """Retrying a spent daily quota five times spends it five more times."""
        def fn(s, e):
            raise cn_data.TushareError("抱歉，您访问接口(report_rc)频率超限(10次/天)")
        with self.vendor(fn), self.assertRaises(cn_data.TushareError):
            cr._fetch("20261001", "20261005")
        self.assertEqual(len(self.calls), 1)


class Build(Sandbox):
    def run_build(self, fn, today=TODAY):
        with self.vendor(fn), mock.patch.object(cr, "date", wraps=date) as d:
            d.today.return_value = today
            cr._state["building"] = True
            cr._build()
        return self.calls

    def test_the_recent_days_are_fetched_before_anything_else(self):
        calls = self.run_build(lambda s, e: [row(s)])
        self.assertEqual(calls[0], cr._topup_range(TODAY))
        self.assertEqual(calls[1], cr._grid_windows(TODAY)[0])
        self.assertEqual(calls[1:], cr._grid_windows(TODAY))         # then newest to oldest

    def test_a_quota_hit_keeps_everything_fetched_so_far(self):
        n = {"i": 0}
        def fn(s, e):
            n["i"] += 1
            if n["i"] > 4:
                raise cn_data.TushareError("抱歉，您访问接口(report_rc)频率超限(10次/天)")
            return [row(s)]
        self.run_build(fn)
        plan = cr._load_plan()
        self.assertEqual(len(plan["done"]), 3)                        # top-up + three windows landed
        self.assertEqual(len(cr._load_raw()), 4)
        self.assertIn("次/天", cr._state["error"])
        self.assertFalse(cr._state["building"])
        self.assertEqual(self.resumes, [24 * 3600])                   # and it will come back by itself

    def test_the_next_run_resumes_instead_of_starting_over(self):
        """The bug: after the first window was written, every later run was a
        five-day top-up and the rest of the backfill was never fetched."""
        n = {"i": 0}
        def capped(s, e):
            n["i"] += 1
            if n["i"] > 4:
                raise cn_data.TushareError("频率超限(10次/天)")
            return [row(s)]
        first = list(self.run_build(capped))
        second = list(self.run_build(lambda s, e: [row(s)], today=TODAY + timedelta(days=1)))
        fetched_first = set(first[1:4])
        self.assertFalse(fetched_first & set(second), "windows already in were fetched again")
        self.assertTrue(cr._coverage(TODAY + timedelta(days=1), cr._load_plan())["complete"])

    def test_progress_is_not_believed_when_the_cache_file_is_gone(self):
        self.run_build(lambda s, e: [row(s)])
        cr._RAW_CACHE_FILE.unlink()
        cr._RAW_MEM.update({"mtime": None, "rows": []})
        calls = self.run_build(lambda s, e: [row(s)])
        self.assertEqual(len(calls), len(cr._grid_windows(TODAY)) + 1)

    def test_old_progress_file_format_is_discarded_not_misread(self):
        cr._BACKFILL_STATE_FILE.write_text(json.dumps(["20251012_20251026"]))
        self.assertEqual(cr._load_plan()["done"], [])

    def test_rows_past_the_horizon_are_dropped(self):
        cr._save_raw([row("20251015"), row("20261001")])             # a year old, and last week
        self.run_build(lambda s, e: [])
        self.assertEqual([r["report_date"] for r in cr._load_raw()], ["20261001"])

    def test_nothing_to_do_means_no_thread(self):
        self.run_build(lambda s, e: [row(s)])
        with mock.patch.object(cr.threading, "Thread") as th, \
             mock.patch.object(cr, "date", wraps=date) as d:
            d.today.return_value = TODAY
            cr.ensure_reports_cache()
        th.assert_not_called()

    def test_a_cooldown_blocks_a_new_attempt(self):
        cr._state.update({"error": "频率超限(10次/天)", "error_ts": cr.time.time()})
        with mock.patch.object(cr.threading, "Thread") as th:
            cr.ensure_reports_cache()
        th.assert_not_called()


class Resume(Sandbox):
    def test_a_restart_inside_a_cooldown_waits_out_what_is_left(self):
        cr._state.update({"error": "频率超限(10次/小时)", "error_ts": cr.time.time() - 20 * 60})
        with mock.patch.object(cr, "ensure_reports_cache") as ens:
            cr.resume_on_startup()
        ens.assert_not_called()
        self.assertEqual(len(self.resumes), 1)
        self.assertAlmostEqual(self.resumes[0], 45 * 60, delta=5)     # 65 minutes less the 20 gone

    def test_a_restart_after_the_cooldown_carries_straight_on(self):
        cr._state.update({"error": "频率超限(10次/小时)", "error_ts": cr.time.time() - 3 * 3600})
        with mock.patch.object(cr, "ensure_reports_cache") as ens:
            cr.resume_on_startup()
        ens.assert_called_once()
        self.assertEqual(self.resumes, [])


class Coverage(Sandbox):
    def test_nothing_fetched_is_nothing_covered(self):
        c = cr._coverage(TODAY, cr._load_plan())
        self.assertEqual((c["covered_from"], c["complete"]), (None, False))

    def test_coverage_is_the_unbroken_run_back_from_today(self):
        g = cr._grid_windows(TODAY)
        cr._save_raw([row("20261010")])
        plan = {"done": [f"{s}_{e}" for s, e in g[:3]] + [f"{g[5][0]}_{g[5][1]}"],
                "topup_ts": 1.0, "topup_from": cr._topup_range(TODAY)[0]}
        c = cr._coverage(TODAY, plan)
        self.assertEqual(c["covered_from"], g[2][0])                 # stops at the hole
        self.assertFalse(c["complete"])
        self.assertEqual(c["done"], 4)

    def test_complete_only_when_every_window_is_in(self):
        g = cr._grid_windows(TODAY)
        cr._save_raw([row("20261010")])
        plan = {"done": [f"{s}_{e}" for s, e in g], "topup_ts": 1.0, "topup_from": "20261008"}
        c = cr._coverage(TODAY, plan)
        self.assertTrue(c["complete"])
        self.assertGreaterEqual(c["covered_days"], cr._HORIZON_DAYS)


if __name__ == "__main__":
    unittest.main()

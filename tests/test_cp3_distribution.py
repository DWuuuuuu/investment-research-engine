"""Tests for product/cp3_distribution.py — spec section 5, market_top_spec.md v3.

All offline: `evaluate` and `classify` are pure functions over bar dicts.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from product.cp3_distribution import (
    CHURN_WARN, DIST_EXPIRE_N, DIST_WARN, _alive, _last_completed, _loc,
    classify, evaluate,
)

NY = ZoneInfo("America/New_York")


def bar(d, c, v, o=None, h=None, l=None):
    return {"d": d, "o": o if o is not None else c, "h": h if h is not None else c * 1.004,
            "l": l if l is not None else c * 0.996, "c": c, "v": v}


def ramp(n, start="2026-01-01", c0=100.0, step=0.05, v=1000.0):
    """A quiet rising tape: no distribution, volume alternating downward."""
    from datetime import timedelta
    d0 = datetime.fromisoformat(start)
    out = []
    for i in range(n):
        out.append(bar((d0 + timedelta(days=i)).date().isoformat(),
                       c0 + i * step, v - (i % 3)))
    return out


class ClassifyTests(unittest.TestCase):
    def test_distribution_day(self):
        prev, cur = bar("2026-01-01", 100.0, 1000), bar("2026-01-02", 99.5, 1200)
        self.assertEqual(classify(prev, cur), "dist")

    def test_down_day_on_lighter_volume_is_clear(self):
        prev, cur = bar("2026-01-01", 100.0, 1200), bar("2026-01-02", 99.5, 1000)
        self.assertEqual(classify(prev, cur), "clear")

    def test_churn_needs_weak_close(self):
        """Flat + heavy volume + close in the lower half -> churn."""
        prev = bar("2026-01-01", 100.0, 1000)
        weak = bar("2026-01-02", 100.05, 1200, h=101.0, l=100.0)     # rho ~ 0.05
        strong = bar("2026-01-02", 100.05, 1200, h=100.1, l=99.0)    # rho ~ 0.95
        self.assertEqual(classify(prev, weak), "churn")
        self.assertEqual(classify(prev, strong), "clear")

    def test_dead_zone_is_covered(self):
        """A day down 0.1% on heavier volume with a weak close.

        The published rules define distribution on r <= -0.2% and stalling on UP
        days under +0.2%, so this bar was counted by neither.  Using |r| closes
        the gap.  This is an AUTHORED deviation from source, not a source rule.
        """
        prev = bar("2026-01-01", 100.0, 1000)
        cur = bar("2026-01-02", 99.9, 1200, h=100.6, l=99.85)
        self.assertLess(cur["c"] / prev["c"] - 1, 0)          # it IS a down day
        self.assertGreater(cur["c"] / prev["c"] - 1, -0.002)  # too small for dist
        self.assertEqual(classify(prev, cur), "churn")

    def test_missing_volume_is_unknown_never_clear(self):
        """market_context writes `f(vol,i) or 0`; a zero would make V_t > V_{t-1}
        false and silently SUPPRESS a distribution flag — an outage would read as
        market calm."""
        prev, cur = bar("2026-01-01", 100.0, 1000), bar("2026-01-02", 99.0, None)
        self.assertEqual(classify(prev, cur), "unknown")

    def test_rho_guard_when_high_equals_low(self):
        self.assertEqual(_loc({"h": 5.0, "l": 5.0, "c": 5.0}), 0.5)


class ExpiryTests(unittest.TestCase):
    def test_both_rules_are_enforced(self):
        rows = ramp(60)
        self.assertFalse(_alive(rows, 1, 1 + DIST_EXPIRE_N)[0])
        self.assertEqual(_alive(rows, 1, 1 + DIST_EXPIRE_N)[1], "aged_out")

    def test_five_percent_rally_retires_a_day(self):
        rows = [bar("2026-01-01", 100.0, 1000), bar("2026-01-02", 99.0, 1200)]
        for i in range(3, 12):
            rows.append(bar(f"2026-01-{i:02d}", 99.0 * (1 + 0.01 * (i - 2)), 1000))
        alive, why = _alive(rows, 1, len(rows) - 1)
        self.assertFalse(alive)
        self.assertEqual(why, "rallied_5pct")

    def test_dropping_the_five_percent_rule_would_keep_it_alive(self):
        """Regression witness: age alone would not have retired that day."""
        rows = [bar("2026-01-01", 100.0, 1000), bar("2026-01-02", 99.0, 1200)]
        for i in range(3, 12):
            rows.append(bar(f"2026-01-{i:02d}", 99.0 * (1 + 0.01 * (i - 2)), 1000))
        self.assertLess(len(rows) - 1 - 1, DIST_EXPIRE_N)


class PartialBarTests(unittest.TestCase):
    def test_todays_bar_dropped_before_the_close(self):
        rows = ramp(3, start="2026-08-28")
        rows[-1]["d"] = "2026-08-30"
        now = datetime(2026, 8, 30, 11, 0, tzinfo=NY)
        self.assertEqual(_last_completed(rows, now), len(rows) - 2)

    def test_todays_bar_kept_after_the_close(self):
        rows = ramp(3, start="2026-08-28")
        rows[-1]["d"] = "2026-08-30"
        now = datetime(2026, 8, 30, 16, 30, tzinfo=NY)
        self.assertEqual(_last_completed(rows, now), len(rows) - 1)

    def test_partial_bar_cannot_suppress_a_flag(self):
        """A live bar reads low volume, which would turn a real distribution day
        into 'clear'.  Dropping it is what prevents that."""
        rows = ramp(200)
        rows.append(bar("2026-12-31", rows[-1]["c"] * 0.99, 5.0))   # tiny live volume
        now = datetime(2026, 12, 31, 10, 0, tzinfo=NY)
        r = evaluate(rows, now)
        self.assertTrue(r["partial_bar_dropped"])
        self.assertNotIn("2026-12-31", [e["d"] for e in r["events"]])


class EvaluateTests(unittest.TestCase):
    def test_tri_state_on_short_history(self):
        r = evaluate(ramp(40))
        self.assertEqual(r["state"], "insufficient_history")
        self.assertIn("needs", r["note"])

    def test_quiet_tape_produces_no_flags(self):
        r = evaluate(ramp(200))
        self.assertEqual(r["state"], "ok")
        self.assertEqual(r["d_total"], 0)
        self.assertEqual(r["level"], "noise")
        self.assertFalse(r["fires"])

    def test_clustered_distribution_is_counted_and_levels_up(self):
        rows = ramp(200)
        c = rows[-1]["c"]
        for i in range(6):                       # six consecutive distribution days
            c *= 0.995
            rows.append(bar(f"2027-01-{i + 1:02d}", c, 2000 + i * 100))
        r = evaluate(rows)
        self.assertGreaterEqual(r["d_total"], DIST_WARN)
        self.assertIn(r["level"], ("warning", "pressure", "elevated"))
        self.assertTrue(r["fires"])

    def test_advisory_flags_are_hard_coded(self):
        """CP3 is display-only. It must not grow an exposure channel by accident."""
        r = evaluate(ramp(200))
        self.assertTrue(r["advisory"])
        self.assertIsNone(r["exposure_effect"])

    def test_decline_is_never_reported_as_clear_or_churn(self):
        """v1's |res|/eff rated a crash as the healthiest possible reading."""
        rows = ramp(140)
        c = rows[-1]["c"]
        for i in range(70):
            c *= 0.995
            rows.append(bar(f"2027-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", c, 1500))
        r = evaluate(rows)
        self.assertLess(r["result_pct"], -3.0)
        self.assertEqual(r["er_state"], "DECLINE")

    def test_churn_state_needs_volume_expansion_and_flat_price(self):
        rows = ramp(70, v=1000.0)
        c = rows[-1]["c"]
        for i in range(70):                      # flat price, 40% heavier volume
            rows.append(bar(f"2027-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                            c + (0.02 if i % 2 else -0.02), 1400.0))
        r = evaluate(rows)
        self.assertGreaterEqual(r["effort"], 1.15)
        self.assertLessEqual(abs(r["result_pct"]), 3.0)
        self.assertEqual(r["er_state"], "CHURN")

    def test_percentile_is_reported_alongside_the_absolute_count(self):
        """The textbook thresholds fire on most windows in live data, so the
        count alone is not a reading."""
        rows = ramp(300)
        r = evaluate(rows)
        self.assertIn("d_pctl", r)
        self.assertIn("d_median_own_history", r)
        self.assertGreater(r["d_history_n"], 0)


if __name__ == "__main__":
    unittest.main()

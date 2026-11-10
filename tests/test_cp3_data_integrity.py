"""Data-integrity checks for the CP3 index feed.

These hit the network, so they are SKIPPED unless CP3_NETWORK_TESTS=1:

    CP3_NETWORK_TESTS=1 python3 -m unittest tests.test_cp3_data_integrity -v

They exist because half of every CP3 rule is a volume comparison, and
"the magnitude looks about right" is not evidence.  Each test below is a
falsifiable structural fingerprint that real exchange tape volume must carry and
a synthetic or constituent-sum proxy would not.
"""
from __future__ import annotations

import os
import statistics as st
import unittest
from datetime import date

NETWORK = os.environ.get("CP3_NETWORK_TESTS") == "1"
US = ("^GSPC", "^IXIC", "^NYA")


def _rel_volume(symbol: str, period: str = "10y"):
    """Each session's volume over its own trailing 50-session median."""
    import yfinance as yf
    df = yf.Ticker(symbol).history(period=period)
    df = df[df["Volume"] > 0]
    v = df["Volume"]
    med = v.rolling(50).median()
    rel = (v / med).dropna()
    return [(d.date(), float(x)) for d, x in zip(rel.index, rel)]


def _is_triple_witching(d: date) -> bool:
    """3rd Friday of Mar/Jun/Sep/Dec — index futures, index options, and single
    stock options all expire together and composite volume spikes."""
    return d.month in (3, 6, 9, 12) and d.weekday() == 4 and 15 <= d.day <= 21


def _is_half_day(d: date) -> bool:
    """The two reliable US early closes: the Friday after Thanksgiving (4th
    Thursday of November) and Christmas Eve on a weekday."""
    if d.month == 11 and d.weekday() == 4 and 23 <= d.day <= 29:
        return True
    return d.month == 12 and d.day == 24 and d.weekday() < 5


@unittest.skipUnless(NETWORK, "set CP3_NETWORK_TESTS=1 to run network checks")
class VolumeAuthenticityTests(unittest.TestCase):
    """Is the index Volume series real tape volume, or a synthetic filler?"""

    def test_triple_witching_volume_spikes(self):
        """Measured 2026-08-30: 1.77x / 1.61x / 1.77x against 1.00x baseline.

        A flat or synthetic series cannot produce this. Threshold set at 1.25x —
        well below what was observed, so the test asserts the fingerprint exists
        rather than pinning today's exact number.
        """
        for sym in US:
            rel = _rel_volume(sym)
            tw = [x for d, x in rel if _is_triple_witching(d)]
            other = [x for d, x in rel if not _is_triple_witching(d)]
            self.assertGreaterEqual(len(tw), 20, f"{sym}: too few events to test")
            ratio = st.median(tw) / st.median(other)
            self.assertGreater(ratio, 1.25,
                               f"{sym}: triple-witching volume only {ratio:.2f}x normal — "
                               f"the series may not be real tape volume")

    def test_half_day_volume_collapses(self):
        """The mirror fingerprint: early closes must show LOW volume."""
        for sym in US:
            rel = _rel_volume(sym)
            hd = [x for d, x in rel if _is_half_day(d)]
            other = [x for d, x in rel if not _is_half_day(d)]
            self.assertGreaterEqual(len(hd), 10, f"{sym}: too few half-days")
            ratio = st.median(hd) / st.median(other)
            self.assertLess(ratio, 0.85,
                            f"{sym}: half-day volume {ratio:.2f}x normal — expected a "
                            f"collapse; the series may not track session length")


@unittest.skipUnless(NETWORK, "set CP3_NETWORK_TESTS=1 to run network checks")
class FeedShapeTests(unittest.TestCase):
    def test_history_depth_supports_a_multi_regime_baseline(self):
        """The percentile baseline is only meaningful across regimes.  Minimums
        chosen from what the feed actually carries: volume starts 1950 (^GSPC),
        1984 (^IXIC), 2001 (^NYA)."""
        from product.cp3_distribution import _fetch
        for sym, min_bars in (("^GSPC", 15000), ("^IXIC", 9000), ("^NYA", 5000)):
            rows = _fetch(sym)["rows"]
            self.assertGreater(len(rows), min_bars, f"{sym}: only {len(rows)} bars")
            self.assertIsNotNone(rows[0]["v"], f"{sym}: leading bars must carry volume")

    def test_no_zero_volume_leaks_into_rows(self):
        """A zero would make V_t > V_{t-1} false and SUPPRESS a flag."""
        from product.cp3_distribution import _fetch
        for sym in US:
            for r in _fetch(sym)["rows"]:
                self.assertTrue(r["v"] is None or r["v"] > 0,
                                f"{sym} {r['d']}: zero volume must be None")

    def test_peers_within_a_market_agree_on_the_last_session(self):
        """If one index lags its peers it has dropped a session, which shortens
        the 25-day window.  A uniform lag across all peers is a source
        characteristic, not a dropped bar."""
        from product.cp3_distribution import _latest_by_market, INSTRUMENTS, _fetch, _last_completed
        latest = _latest_by_market()
        for meta in INSTRUMENTS:
            raw = _fetch(meta["t"])
            if raw.get("status") != "ready":
                continue
            rows = raw["rows"]
            td = _last_completed(rows)
            self.assertEqual(rows[td]["d"], latest[meta["market"]],
                             f"{meta['t']} lags its market peers — dropped session")


if __name__ == "__main__":
    unittest.main()

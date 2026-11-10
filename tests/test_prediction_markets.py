from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from product.clients import KalshiClient, PolymarketClient
from product.classify import classify_market
from product.db import init_db, upsert_snapshots
from product.models import MarketSnapshot
from product.radar import radar_items


class ClientParsingTests(unittest.TestCase):
    def test_classifier_does_not_match_substrings_inside_words(self) -> None:
        self.assertNotEqual(classify_market("Eurovision Winner 2026"), "macro")
        self.assertEqual(classify_market("Will euro strengthen against dollar?"), "macro")

    def test_polymarket_event_normalization_extracts_yes_probability(self) -> None:
        client = PolymarketClient()
        events = [
            {
                "title": "Will the Fed cut rates in June?",
                "slug": "fed-cut-june",
                "category": "macro",
                "markets": [
                    {
                        "id": "123",
                        "question": "Will the Fed cut rates in June?",
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": '["0.42", "0.58"]',
                        "volume": "10000",
                        "liquidity": "5000",
                        "active": True,
                        "closed": False,
                    }
                ],
            }
        ]

        snapshots = list(client._normalize_events(events))

        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].source, "polymarket")
        self.assertEqual(snapshots[0].market_id, "123")
        self.assertAlmostEqual(snapshots[0].probability or 0, 0.42)
        self.assertEqual(snapshots[0].category, "macro")

    def _kalshi(self, market: dict) -> MarketSnapshot:
        return KalshiClient()._normalize_market(
            dict({"ticker": "KXFEDCUT-26JUN", "title": "Fed cuts rates by June?"}, **market),
            {"event_ticker": "KXFEDCUT"},
            "Fed rate cuts in 2026",
            "2026-05-06T00:00:00+00:00",
        )

    def test_kalshi_market_normalization_uses_midpoint_when_no_last_price(self) -> None:
        snapshot = self._kalshi({"yes_bid": 41, "yes_ask": 45, "volume": 8000})

        self.assertEqual(snapshot.source, "kalshi")
        self.assertAlmostEqual(snapshot.probability or 0, 0.43)
        self.assertEqual(snapshot.category, "macro")
        self.assertEqual(snapshot.event_title, "Fed rate cuts in 2026")
        self.assertEqual(snapshot.url, "https://kalshi.com/markets/KXFEDCUT")

    def test_kalshi_reads_the_dollar_string_prices_the_events_api_returns(self) -> None:
        snapshot = self._kalshi({"yes_bid_dollars": "0.3400", "yes_ask_dollars": "0.3600",
                                 "last_price_dollars": "0.3500"})

        self.assertAlmostEqual(snapshot.probability or 0, 0.35)
        self.assertAlmostEqual(snapshot.yes_bid or 0, 0.34)

    def test_kalshi_zero_last_price_means_never_traded_not_zero_percent(self) -> None:
        """An untraded market reports last price "0.0000". 129 of 3,490 stored
        snapshots were 0% beside live quotes, one of them bid 0.90 / ask 0.98."""
        snapshot = self._kalshi({"yes_bid_dollars": "0.9000", "yes_ask_dollars": "0.9800",
                                 "last_price_dollars": "0.0000"})

        self.assertAlmostEqual(snapshot.probability or 0, 0.94)

    def test_kalshi_one_sided_quote_is_used_when_that_is_all_there_is(self) -> None:
        snapshot = self._kalshi({"yes_ask_dollars": "0.0500", "last_price_dollars": "0.0000"})

        self.assertAlmostEqual(snapshot.probability or 0, 0.05)


class RadarTests(unittest.TestCase):
    def test_radar_ranks_probability_moves(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        init_db(connection)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        old = (now - timedelta(hours=25)).isoformat()
        latest = now.isoformat()

        upsert_snapshots(
            connection,
            [
                MarketSnapshot(
                    source="polymarket",
                    market_id="btc",
                    title="Will Bitcoin hit $100k?",
                    event_title="Bitcoin price",
                    category="crypto",
                    observed_at=old,
                    probability=0.30,
                    volume=1000,
                ),
                MarketSnapshot(
                    source="polymarket",
                    market_id="btc",
                    title="Will Bitcoin hit $100k?",
                    event_title="Bitcoin price",
                    category="crypto",
                    observed_at=latest,
                    probability=0.55,
                    volume=1200,
                ),
            ],
        )

        items = radar_items(connection, lookback_hours=24, limit=5)

        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0].probability_change or 0, 0.25)


if __name__ == "__main__":
    unittest.main()

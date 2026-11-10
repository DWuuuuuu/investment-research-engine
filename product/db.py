from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .classify import classify_market
from .models import MarketSnapshot


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS markets (
    source TEXT NOT NULL,
    market_id TEXT NOT NULL,
    title TEXT NOT NULL,
    event_title TEXT,
    category TEXT,
    url TEXT,
    close_time TEXT,
    raw_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (source, market_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    market_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    probability REAL,
    yes_bid REAL,
    yes_ask REAL,
    volume REAL,
    liquidity REAL,
    open_interest REAL,
    FOREIGN KEY (source, market_id) REFERENCES markets (source, market_id)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_market_time
ON snapshots (source, market_id, observed_at);

CREATE INDEX IF NOT EXISTS idx_markets_category
ON markets (category);

CREATE TABLE IF NOT EXISTS macro_cache (
    series_id    TEXT PRIMARY KEY,
    value        REAL,
    date         TEXT,
    fetched_at   REAL,
    history_json TEXT
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def upsert_snapshots(connection: sqlite3.Connection, snapshots: list[MarketSnapshot]) -> int:
    init_db(connection)
    with connection:
        for snapshot in snapshots:
            raw_json = json.dumps(snapshot.raw or {}, ensure_ascii=False, sort_keys=True)
            connection.execute(
                """
                INSERT INTO markets (
                    source, market_id, title, event_title, category, url, close_time,
                    raw_json, first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, market_id) DO UPDATE SET
                    title = excluded.title,
                    event_title = excluded.event_title,
                    category = COALESCE(excluded.category, markets.category),
                    url = COALESCE(excluded.url, markets.url),
                    close_time = COALESCE(excluded.close_time, markets.close_time),
                    raw_json = excluded.raw_json,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    snapshot.source,
                    snapshot.market_id,
                    snapshot.title,
                    snapshot.event_title,
                    snapshot.category,
                    snapshot.url,
                    snapshot.close_time,
                    raw_json,
                    snapshot.observed_at,
                    snapshot.observed_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO snapshots (
                    source, market_id, observed_at, probability, yes_bid, yes_ask,
                    volume, liquidity, open_interest
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.source,
                    snapshot.market_id,
                    snapshot.observed_at,
                    snapshot.probability,
                    snapshot.yes_bid,
                    snapshot.yes_ask,
                    snapshot.volume,
                    snapshot.liquidity,
                    snapshot.open_interest,
                ),
            )
    return len(snapshots)


def reclassify_markets(connection: sqlite3.Connection) -> int:
    init_db(connection)
    rows = connection.execute("SELECT source, market_id, title, event_title FROM markets").fetchall()
    with connection:
        for row in rows:
            category = classify_market(row["title"], row["event_title"])
            connection.execute(
                """
                UPDATE markets
                SET category = ?
                WHERE source = ? AND market_id = ?
                """,
                (category, row["source"], row["market_id"]),
            )
    return len(rows)

"""
FRED API client with SQLite caching.

Fetches macro time-series from the St. Louis Fed (api.stlouisfed.org).
Results are cached in the macro_cache table to avoid hammering the API on
every dashboard load.  Daily series expire after 6 h; monthly series after
24 h.  When the API key is absent or the network fails, the last cached
value is returned so the UI stays functional.
"""
from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Cache TTLs in seconds
TTL_DAILY = 6 * 3600
TTL_MONTHLY = 24 * 3600


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_fred(series_id: str, api_key: str, limit: int = 60) -> list[dict[str, str]]:
    """Call the FRED REST API and return a list of observations (newest first).
    Raises urllib.error.URLError / ValueError on failure."""
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    })
    url = f"{FRED_BASE}?{params}"
    with urllib.request.urlopen(url, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [o for o in data.get("observations", []) if o.get("value", ".") != "."]


def _init_cache(connection: sqlite3.Connection) -> None:
    """Ensure macro_cache table exists (called lazily)."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS macro_cache (
            series_id   TEXT PRIMARY KEY,
            value       REAL,
            date        TEXT,
            fetched_at  REAL,
            history_json TEXT
        )
    """)
    connection.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_series(
    connection: sqlite3.Connection,
    series_id: str,
    api_key: str,
    limit: int = 36,
    ttl: int = TTL_DAILY,
) -> list[dict[str, Any]]:
    """Return up to *limit* observations (newest first), using cache when fresh.

    Each observation is a dict with keys ``value`` (float) and ``date`` (str).
    Returns an empty list when no data is available.
    """
    _init_cache(connection)
    now = time.time()

    row = connection.execute(
        "SELECT value, date, fetched_at, history_json FROM macro_cache WHERE series_id = ?",
        (series_id,),
    ).fetchone()

    cache_fresh = row and (now - row["fetched_at"]) < ttl

    if cache_fresh:
        return json.loads(row["history_json"] or "[]")

    if not api_key:
        # No key — return stale cache if present
        return json.loads(row["history_json"] or "[]") if row else []

    try:
        obs = _fetch_fred(series_id, api_key, limit=limit)
        if not obs:
            return json.loads(row["history_json"] or "[]") if row else []

        latest = obs[0]
        connection.execute(
            """
            INSERT INTO macro_cache (series_id, value, date, fetched_at, history_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(series_id) DO UPDATE SET
                value        = excluded.value,
                date         = excluded.date,
                fetched_at   = excluded.fetched_at,
                history_json = excluded.history_json
            """,
            (series_id, float(latest["value"]), latest["date"], now, json.dumps(obs)),
        )
        connection.commit()
        return obs

    except (urllib.error.URLError, ValueError, KeyError):
        # Network / parse error — return stale cache
        return json.loads(row["history_json"] or "[]") if row else []


def get_latest(
    connection: sqlite3.Connection,
    series_id: str,
    api_key: str,
    ttl: int = TTL_DAILY,
) -> tuple[float | None, str | None]:
    """Return ``(value, date)`` for the most recent observation, or ``(None, None)``."""
    obs = get_series(connection, series_id, api_key, limit=5, ttl=ttl)
    if obs:
        return float(obs[0]["value"]), obs[0]["date"]
    return None, None


def get_recent_values(
    connection: sqlite3.Connection,
    series_id: str,
    api_key: str,
    n: int = 12,
    ttl: int = TTL_MONTHLY,
) -> list[float]:
    """Return the *n* most recent numeric values (newest first)."""
    obs = get_series(connection, series_id, api_key, limit=n, ttl=ttl)
    return [float(o["value"]) for o in obs[:n]]

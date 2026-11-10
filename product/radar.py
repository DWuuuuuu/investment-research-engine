from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .classify import relevance_note


@dataclass(frozen=True)
class RadarItem:
    source: str
    market_id: str
    title: str
    event_title: str | None
    category: str
    url: str | None
    latest_at: str
    latest_probability: float | None
    previous_probability: float | None
    probability_change: float | None
    volume: float | None
    liquidity: float | None
    note: str
    change_1h: float | None = None
    change_4h: float | None = None
    persistence_label: str = "stable"


def _persistence_label(
    change_1h: float | None,
    change_4h: float | None,
    change_24h: float | None,
    threshold: float = 0.05,
) -> str:
    a1 = abs(change_1h) if change_1h is not None else 0.0
    a4 = abs(change_4h) if change_4h is not None else 0.0
    a24 = abs(change_24h) if change_24h is not None else 0.0
    if max(a1, a4, a24) < threshold:
        return "stable"
    # Very recent spike not yet confirmed
    if a1 >= threshold and a4 < threshold * 0.6:
        return "fresh_spike"
    # Had big move but now reversing
    if a24 >= threshold and change_1h is not None and change_24h is not None:
        if (change_1h * change_24h < 0) and a1 >= threshold * 0.4:
            return "reversing"
    # Move has held for 4h+
    if a4 >= threshold:
        return "persistent"
    return "active"


def radar_items(
    connection: sqlite3.Connection,
    lookback_hours: int = 24,
    limit: int = 25,
    min_volume: float | None = None,
    min_probability: float | None = None,
    category: str | None = None,
    investment_only: bool = True,
    max_per_event: int | None = 2,
) -> list[RadarItem]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    cutoff_iso = cutoff.replace(microsecond=0).isoformat()
    cutoff_1h = (now - timedelta(hours=1)).replace(microsecond=0).isoformat()
    cutoff_4h = (now - timedelta(hours=4)).replace(microsecond=0).isoformat()
    params: list[object] = [cutoff_1h, cutoff_4h, cutoff_iso]
    filters = []
    if min_volume is not None:
        filters.append("(latest.volume IS NULL OR latest.volume >= ?)")
        params.append(min_volume)
    if min_probability is not None:
        filters.append("latest.probability >= ?")
        params.append(min_probability)
    if category:
        filters.append("m.category = ?")
        params.append(category)
    if investment_only:
        filters.append(
            "COALESCE(m.category, 'uncategorized') IN "
            "('macro', 'markets', 'geopolitics', 'ai_tech', 'crypto', 'politics_policy')"
        )
    where_clause = " AND " + " AND ".join(filters) if filters else ""
    sql_limit = limit if not max_per_event else max(limit * max_per_event * 4, limit)
    params.append(sql_limit)

    rows = connection.execute(
        f"""
        WITH latest AS (
            SELECT s.*
            FROM snapshots s
            JOIN (
                SELECT source, market_id, MAX(observed_at) AS observed_at
                FROM snapshots
                GROUP BY source, market_id
            ) newest
            ON s.source = newest.source
               AND s.market_id = newest.market_id
               AND s.observed_at = newest.observed_at
        ),
        snap_1h AS (
            SELECT s.*
            FROM snapshots s
            JOIN (
                SELECT source, market_id, MAX(observed_at) AS observed_at
                FROM snapshots
                WHERE observed_at <= ?
                GROUP BY source, market_id
            ) t ON s.source = t.source AND s.market_id = t.market_id AND s.observed_at = t.observed_at
        ),
        snap_4h AS (
            SELECT s.*
            FROM snapshots s
            JOIN (
                SELECT source, market_id, MAX(observed_at) AS observed_at
                FROM snapshots
                WHERE observed_at <= ?
                GROUP BY source, market_id
            ) t ON s.source = t.source AND s.market_id = t.market_id AND s.observed_at = t.observed_at
        ),
        previous AS (
            SELECT s.*
            FROM snapshots s
            JOIN (
                SELECT source, market_id, MAX(observed_at) AS observed_at
                FROM snapshots
                WHERE observed_at <= ?
                GROUP BY source, market_id
            ) older
            ON s.source = older.source
               AND s.market_id = older.market_id
               AND s.observed_at = older.observed_at
        )
        SELECT
            m.source,
            m.market_id,
            m.title,
            m.event_title,
            COALESCE(m.category, 'uncategorized') AS category,
            m.url,
            latest.observed_at AS latest_at,
            latest.probability AS latest_probability,
            previous.probability AS previous_probability,
            CASE
                WHEN latest.probability IS NULL OR previous.probability IS NULL THEN NULL
                ELSE latest.probability - previous.probability
            END AS probability_change,
            CASE
                WHEN latest.probability IS NULL OR snap_1h.probability IS NULL THEN NULL
                ELSE latest.probability - snap_1h.probability
            END AS change_1h,
            CASE
                WHEN latest.probability IS NULL OR snap_4h.probability IS NULL THEN NULL
                ELSE latest.probability - snap_4h.probability
            END AS change_4h,
            latest.volume,
            latest.liquidity
        FROM latest
        JOIN markets m
          ON latest.source = m.source AND latest.market_id = m.market_id
        LEFT JOIN previous
          ON latest.source = previous.source AND latest.market_id = previous.market_id
        LEFT JOIN snap_1h
          ON latest.source = snap_1h.source AND latest.market_id = snap_1h.market_id
        LEFT JOIN snap_4h
          ON latest.source = snap_4h.source AND latest.market_id = snap_4h.market_id
        WHERE 1=1 {where_clause}
        ORDER BY
            CASE WHEN probability_change IS NULL THEN 1 ELSE 0 END,
            ABS(probability_change) DESC,
            CASE COALESCE(m.category, 'uncategorized')
                WHEN 'macro' THEN 1
                WHEN 'markets' THEN 2
                WHEN 'geopolitics' THEN 3
                WHEN 'ai_tech' THEN 4
                WHEN 'crypto' THEN 5
                WHEN 'politics_policy' THEN 6
                ELSE 9
            END,
            COALESCE(latest.volume, 0) DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    items = [
        RadarItem(
            source=row["source"],
            market_id=row["market_id"],
            title=row["title"],
            event_title=row["event_title"],
            category=row["category"],
            url=row["url"],
            latest_at=row["latest_at"],
            latest_probability=row["latest_probability"],
            previous_probability=row["previous_probability"],
            probability_change=row["probability_change"],
            volume=row["volume"],
            liquidity=row["liquidity"],
            note=relevance_note(row["category"], row["title"], row["event_title"]),
            change_1h=row["change_1h"],
            change_4h=row["change_4h"],
            persistence_label=_persistence_label(
                row["change_1h"], row["change_4h"], row["probability_change"]
            ),
        )
        for row in rows
    ]

    if not max_per_event:
        return items[:limit]

    filtered: list[RadarItem] = []
    event_counts: dict[str, int] = {}
    for item in items:
        event_key = item.url or item.event_title or f"{item.source}:{item.market_id}"
        count = event_counts.get(event_key, 0)
        if count >= max_per_event:
            continue
        event_counts[event_key] = count + 1
        filtered.append(item)
        if len(filtered) >= limit:
            break
    return filtered


def format_probability(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def format_change(value: float | None) -> str:
    if value is None:
        return "new/no baseline"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f} pts"


def format_markdown(items: list[RadarItem], lookback_hours: int) -> str:
    lines = [
        f"# Forecast Radar ({lookback_hours}h)",
        "",
        "Ranked by absolute probability move, then volume.",
        "",
    ]
    if not items:
        lines.append("No markets found. Run ingestion first or loosen filters.")
        return "\n".join(lines)

    for index, item in enumerate(items, start=1):
        title = item.title
        if item.event_title and item.event_title != item.title:
            title = f"{item.event_title}: {item.title}"
        lines.extend(
            [
                f"## {index}. {title}",
                f"- Source: {item.source} | Category: {item.category}",
                f"- Probability: {format_probability(item.latest_probability)} ({format_change(item.probability_change)} over lookback)",
                f"- Volume: {item.volume if item.volume is not None else 'n/a'} | Liquidity: {item.liquidity if item.liquidity is not None else 'n/a'}",
                f"- Why it matters: {item.note}",
            ]
        )
        if item.url:
            lines.append(f"- URL: {item.url}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

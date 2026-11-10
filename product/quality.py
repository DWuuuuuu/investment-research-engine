"""Signal Quality scoring.

Scores each prediction market on observable data quality and liquidity
signals from the DB. This is the first tier of the wallet/order-flow layer.

Full wallet-level signals (skilled-wallet identification, whale concentration,
maker/taker PnL) require individual trade data from:
  - Polymarket: CLOB activity API (GET /activity?market={id})
  - Kalshi: Trade history endpoint

Quality Score (0-100):
  Volume tier   0-40 pts   (>$1M=40, >$100K=30, >$10K=20, >$1K=10)
  Liquidity     0-25 pts   (>$100K=25, >$10K=15, >$1K=8)
  Spread        0-15 pts   (Kalshi only; <2pp=15, <5pp=10, <10pp=5)
  History depth 0-10 pts   (>=10 snaps=10, >=3=5, >=2=2)
  Cross-platform 0-10 pts  (matched on another platform=10)

Flags:
  low_liquidity    liquidity < $1K or missing
  wide_spread      spread > 10pp (Kalshi only)
  thin_history     fewer than 2 snapshots
  tail_probability probability < 5% (longshot bias risk per CESifo paper)
  no_oi            open interest unavailable
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .divergence import normalise


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class QualityResult:
    source: str
    market_id: str
    title: str
    event_title: str | None
    category: str
    url: str | None
    probability: float | None
    volume: float | None
    liquidity: float | None
    spread: float | None          # yes_ask - yes_bid (Kalshi only)
    snapshot_count: int
    score: int                    # 0-100
    flags: list[str] = field(default_factory=list)
    cross_platform: bool = False  # matched title on other platform


# ── Scoring ───────────────────────────────────────────────────────────────────

def _volume_pts(v: float | None) -> int:
    if v is None:
        return 0
    if v >= 1_000_000:
        return 40
    if v >= 100_000:
        return 30
    if v >= 10_000:
        return 20
    if v >= 1_000:
        return 10
    return 0


def _liquidity_pts(liq: float | None) -> int:
    if liq is None:
        return 0
    if liq >= 100_000:
        return 25
    if liq >= 10_000:
        return 15
    if liq >= 1_000:
        return 8
    return 0


def _spread_pts(spread: float | None) -> int:
    if spread is None:
        return 0
    if spread <= 0.02:
        return 15
    if spread <= 0.05:
        return 10
    if spread <= 0.10:
        return 5
    return 0


def _history_pts(count: int) -> int:
    if count >= 10:
        return 10
    if count >= 3:
        return 5
    if count >= 2:
        return 2
    return 0


def _flags(
    probability: float | None,
    liquidity: float | None,
    spread: float | None,
    snapshot_count: int,
    has_oi: bool,
) -> list[str]:
    flags = []
    if liquidity is None or liquidity < 1_000:
        flags.append("low_liquidity")
    if spread is not None and spread > 0.10:
        flags.append("wide_spread")
    if snapshot_count < 2:
        flags.append("thin_history")
    if probability is not None and probability < 0.05:
        flags.append("tail_probability")
    if not has_oi:
        flags.append("no_oi")
    return flags


# ── Main query ────────────────────────────────────────────────────────────────

def quality_scores(
    connection: sqlite3.Connection,
    limit: int = 50,
    category: str | None = None,
    min_score: int = 0,
) -> list[QualityResult]:
    """Return markets ranked by quality score descending."""

    cat_filter = "AND COALESCE(m.category, 'uncategorized') = ?" if category else ""
    params: list[object] = []
    if category:
        params.append(category)
    params.append(limit)

    rows = connection.execute(
        f"""
        WITH latest AS (
            SELECT s.*
            FROM snapshots s
            JOIN (
                SELECT source, market_id, MAX(observed_at) AS observed_at
                FROM snapshots GROUP BY source, market_id
            ) n ON s.source=n.source AND s.market_id=n.market_id AND s.observed_at=n.observed_at
        ),
        snap_counts AS (
            SELECT source, market_id, COUNT(*) AS cnt
            FROM snapshots GROUP BY source, market_id
        )
        SELECT
            m.source, m.market_id, m.title, m.event_title,
            COALESCE(m.category, 'uncategorized') AS category, m.url,
            l.probability, l.volume, l.liquidity,
            CASE WHEN l.yes_bid IS NOT NULL AND l.yes_ask IS NOT NULL
                 THEN l.yes_ask - l.yes_bid ELSE NULL END AS spread,
            l.open_interest,
            sc.cnt AS snapshot_count
        FROM markets m
        JOIN latest l ON m.source=l.source AND m.market_id=l.market_id
        JOIN snap_counts sc ON m.source=sc.source AND m.market_id=sc.market_id
        WHERE COALESCE(m.category, 'uncategorized') IN
              ('macro','markets','geopolitics','ai_tech','crypto','politics_policy','earnings')
        {cat_filter}
        ORDER BY COALESCE(l.volume, 0) DESC
        LIMIT ?
        """,
        params,
    ).fetchall()

    # Build cross-platform title set for matching
    all_titles_by_source: dict[str, set[str]] = {}
    for row in rows:
        all_titles_by_source.setdefault(row["source"], set()).add(normalise(row["title"]))

    other_sources: dict[str, set[str]] = {}
    for src, titles in all_titles_by_source.items():
        combined = set()
        for other_src, other_titles in all_titles_by_source.items():
            if other_src != src:
                combined |= other_titles
        other_sources[src] = combined

    results: list[QualityResult] = []
    for row in rows:
        spread = row["spread"]
        liq = row["liquidity"]
        vol = row["volume"]
        prob = row["probability"]
        cnt = row["snapshot_count"]
        has_oi = row["open_interest"] is not None

        norm = normalise(row["title"])
        cross = norm in other_sources.get(row["source"], set())

        score = (
            _volume_pts(vol)
            + _liquidity_pts(liq)
            + _spread_pts(spread)
            + _history_pts(cnt)
            + (10 if cross else 0)
        )

        if score < min_score:
            continue

        results.append(
            QualityResult(
                source=row["source"],
                market_id=row["market_id"],
                title=row["title"],
                event_title=row["event_title"],
                category=row["category"],
                url=row["url"],
                probability=prob,
                volume=vol,
                liquidity=liq,
                spread=spread,
                snapshot_count=cnt,
                score=score,
                flags=_flags(prob, liq, spread, cnt, has_oi),
                cross_platform=cross,
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    return results

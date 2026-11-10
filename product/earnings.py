"""Earnings module.

Two sections:
  1. Earnings cards — EPS/Revenue beat/miss prediction markets (active during
     earnings seasons: Q1 Apr-May, Q2 Jul-Aug, Q3 Oct-Nov, Q4 Jan-Feb).
  2. Market intelligence — IPO timing and market-cap milestone markets from the
     'markets' category. These are investment-relevant year-round and show
     liquid, high-volume crowd-sourced views on key corporate events.

Research note: Polymarket earnings markets show ~77% accuracy vs ~62% for
sell-side analysts (LBS/Yale SSRN paper — verify exact figures in PDF).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field


# ── Parsing helpers ───────────────────────────────────────────────────────────

# Quarter patterns
_QUARTER_RE = re.compile(r"Q([1-4])\s*(?:FY\s*)?\d{4}|Q([1-4])\s+(?:earnings|eps|revenue)", re.IGNORECASE)

# Company name extraction: "Will [Company] beat..." or "[Company] Q1..."
_COMPANY_PREFIXES = re.compile(r"^will\s+|^whether\s+", re.IGNORECASE)

# Known earnings-related verbs/phrases
_BEAT_RE = re.compile(r"\b(beat|exceed|above|surpass|top)\b", re.IGNORECASE)
_MISS_RE = re.compile(r"\b(miss|below|under|fail)\b", re.IGNORECASE)
_EPS_RE = re.compile(r"\beps\b|\bearnings per share\b|\bearnings estimate\b", re.IGNORECASE)
_REV_RE = re.compile(r"\brevenue\b|\bsales\b|\btop.line\b", re.IGNORECASE)

# EPS / consensus value extraction: "$X.XX" or "X.XX per share"
_VALUE_RE = re.compile(r"\$\s*([\d,.]+)\s*(?:billion|million|B|M)?|\b([\d.]+)\s+per share", re.IGNORECASE)

# Map title fragments → canonical company names
_TICKER_MAP = {
    "aapl": "Apple", "apple": "Apple",
    "msft": "Microsoft", "microsoft": "Microsoft",
    "amzn": "Amazon", "amazon": "Amazon",
    "googl": "Alphabet", "goog": "Alphabet", "alphabet": "Alphabet",
    "meta": "Meta",
    "nvda": "Nvidia", "nvidia": "Nvidia",
    "tsla": "Tesla", "tesla": "Tesla",
    "nflx": "Netflix", "netflix": "Netflix",
    "dis": "Disney", "disney": "Disney",
    "jpm": "JPMorgan", "jpmorgan": "JPMorgan",
    "gs": "Goldman Sachs", "goldman": "Goldman Sachs",
    "bac": "Bank of America",
    "xom": "ExxonMobil", "exxon": "ExxonMobil",
    "tsm": "TSMC", "tsmc": "TSMC",
    "uber": "Uber",
    "lyft": "Lyft",
    "snap": "Snap",
    "twtr": "Twitter/X",
    "coin": "Coinbase", "coinbase": "Coinbase",
    "arkk": "ARK",
    "spy": "S&P 500",
    "qqq": "Nasdaq-100",
}


def _extract_company(title: str) -> str | None:
    t = title.lower()
    t = _COMPANY_PREFIXES.sub("", t).strip()
    # Try ticker map first
    for key, name in _TICKER_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", t):
            return name
    # Fallback: first word(s) before "beat", "miss", "q1", etc.
    m = re.match(r"^([a-z][a-z\s&.]{1,30}?)\s+(?:beat|miss|q[1-4]|earn|eps|revenue)", t)
    if m:
        raw = m.group(1).strip().title()
        if len(raw) > 1:
            return raw
    return None


def _extract_quarter(title: str) -> str | None:
    m = _QUARTER_RE.search(title)
    if m:
        q = m.group(1) or m.group(2)
        return f"Q{q}"
    if re.search(r"\bannual\b|\bfull.?year\b|\bfy\b", title, re.IGNORECASE):
        return "FY"
    return None


def _metric(title: str) -> str:
    t = title.lower()
    if _EPS_RE.search(t):
        return "EPS"
    if _REV_RE.search(t):
        return "Revenue"
    return "Earnings"


def _outcome_type(title: str) -> str:
    """Returns 'beat', 'miss', or 'threshold'."""
    t = title.lower()
    if _BEAT_RE.search(t):
        return "beat"
    if _MISS_RE.search(t):
        return "miss"
    return "threshold"


def _consensus_value(title: str) -> str | None:
    m = _VALUE_RE.search(title)
    if m:
        raw = m.group(1) or m.group(2)
        if raw:
            return f"${raw}"
    return None


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class EarningsMarket:
    source: str
    market_id: str
    title: str
    url: str | None
    probability: float
    volume: float | None
    company: str
    quarter: str | None
    metric: str           # EPS / Revenue / Earnings
    outcome_type: str     # beat / miss / threshold
    consensus: str | None # e.g. "$1.23" if in title


@dataclass
class EarningsCard:
    company: str
    quarter: str | None
    markets: list[EarningsMarket] = field(default_factory=list)

    @property
    def beat_prob(self) -> float | None:
        """Best beat-probability market for this company/quarter."""
        beats = [m for m in self.markets if m.outcome_type == "beat"]
        if beats:
            return max(b.probability for b in beats)
        return None

    @property
    def miss_prob(self) -> float | None:
        misses = [m for m in self.markets if m.outcome_type == "miss"]
        if misses:
            return max(m.probability for m in misses)
        return None


# ── Main fetch ────────────────────────────────────────────────────────────────

_LATEST_CTE = """
WITH latest AS (
    SELECT s.*
    FROM snapshots s
    JOIN (
        SELECT source, market_id, MAX(observed_at) AS observed_at
        FROM snapshots GROUP BY source, market_id
    ) n ON s.source=n.source AND s.market_id=n.market_id AND s.observed_at=n.observed_at
)
"""

_EARNINGS_KEYWORDS = (
    "beat", "miss", "eps", "earnings estimate", "analyst estimate",
    "quarterly earnings", "q1 earnings", "q2 earnings", "q3 earnings", "q4 earnings",
    "revenue estimate", "beat revenue", "exceed.*estimate",
)


def earnings_markets(
    connection: sqlite3.Connection,
    limit: int = 50,
) -> list[EarningsCard]:
    """Return grouped earnings cards sorted by beat probability desc."""

    # Build SQL: look for earnings category OR keyword matches in title/event_title
    # Use space-padded keywords to avoid substring false-positives (e.g. "eps" in "epstein")
    kw_conditions = " OR ".join(
        f"LOWER(m.title) LIKE '%{kw}%'" for kw in [
            " beat ", " miss ", " eps ", "eps estimate", "eps beat",
            "earnings estimate", "quarterly earnings",
            "q1 earnings", "q2 earnings", "q3 earnings", "q4 earnings",
        ]
    )

    rows = connection.execute(
        _LATEST_CTE + f"""
        SELECT m.source, m.market_id, m.title, m.event_title, m.category, m.url,
               l.probability, l.volume
        FROM markets m
        JOIN latest l ON m.source=l.source AND m.market_id=l.market_id
        WHERE (
            m.category = 'earnings'
            OR ({kw_conditions})
        )
        AND l.probability IS NOT NULL
        AND LOWER(m.title) NOT LIKE '%roshan%'
        AND LOWER(m.title) NOT LIKE '%dota%'
        AND LOWER(m.title) NOT LIKE '%esport%'
        ORDER BY l.volume DESC NULLS LAST, l.probability DESC
        LIMIT {limit}
        """
    ).fetchall()

    # Group into cards
    cards: dict[str, EarningsCard] = {}
    for row in rows:
        title = row["title"]
        company = _extract_company(title) or _extract_company(row["event_title"] or "")
        if not company:
            continue
        quarter = _extract_quarter(title) or _extract_quarter(row["event_title"] or "")
        card_key = f"{company}|{quarter or 'unknown'}"

        if card_key not in cards:
            cards[card_key] = EarningsCard(company=company, quarter=quarter)

        cards[card_key].markets.append(
            EarningsMarket(
                source=row["source"],
                market_id=row["market_id"],
                title=title,
                url=row["url"],
                probability=row["probability"],
                volume=row["volume"],
                company=company,
                quarter=quarter,
                metric=_metric(title),
                outcome_type=_outcome_type(title),
                consensus=_consensus_value(title),
            )
        )

    result = list(cards.values())
    result.sort(key=lambda c: c.beat_prob or 0, reverse=True)
    return result


# ── Market Intelligence (IPO + market cap milestones) ─────────────────────────

@dataclass
class IntelMarket:
    source: str
    market_id: str
    title: str
    url: str | None
    probability: float
    volume: float | None
    kind: str     # "ipo" | "market_cap" | "other"


@dataclass
class IntelCard:
    company: str
    markets: list[IntelMarket] = field(default_factory=list)

    @property
    def top_probability(self) -> float:
        return max((m.probability for m in self.markets), default=0.0)

    @property
    def summary_kind(self) -> str:
        kinds = {m.kind for m in self.markets}
        if "ipo" in kinds:
            return "ipo"
        if "market_cap" in kinds:
            return "market_cap"
        return "other"


_IPO_RE = re.compile(r"\bipo\b", re.IGNORECASE)
_MCAP_RE = re.compile(r"\bmarket cap\b|\blargest company\b|\bmarket value\b|\bvaluation\b", re.IGNORECASE)

def _intel_kind(title: str) -> str:
    if _IPO_RE.search(title):
        return "ipo"
    if _MCAP_RE.search(title):
        return "market_cap"
    return "other"


_WILL_STRIP_RE = re.compile(r"^(?:will|when will|whether)\s+", re.IGNORECASE)

def _intel_company(title: str) -> str | None:
    """Extract company name from IPO/market cap title."""
    # Strip leading "Will/When will" before trying patterns
    t = _WILL_STRIP_RE.sub("", title).strip()

    # "SpaceX IPO before 2027?" → match one or more TitleCase words before IPO
    # This correctly rejects "Airtable officially announce an IPO?" → "Airtable"
    m = re.match(
        r"^([A-Z][A-Za-z0-9&.']*(?:\s+[A-Z][A-Za-z0-9&.']*)*)"
        r"\s+(?:\([^)]+\)\s+)?(?:IPO|ipo)\b",
        t,
    )
    if m:
        name = m.group(1).strip()
        # Reject obvious sentence fragments ("Not" etc.) or multi-company names
        if len(name) >= 2 and not re.search(r"\b(or|and|Not|No|The)\b", name):
            return name

    # "SpaceX's market cap be..." → "SpaceX"
    m3 = re.search(r"^([A-Za-z][A-Za-z0-9 &.']{1,30?})(?:'s)?\s+(?:market cap|be the largest|market value)", t, re.IGNORECASE)
    if m3:
        return m3.group(1).strip()

    # "Alphabet be the largest..." → "Alphabet"
    m4 = re.match(r"^([A-Za-z][A-Za-z0-9 &.']{1,30?})\s+be the largest", t, re.IGNORECASE)
    if m4:
        return m4.group(1).strip()

    # Ticker map fallback
    t_lower = title.lower()
    for key, name in _TICKER_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", t_lower):
            return name
    return None


def market_intelligence(
    connection: sqlite3.Connection,
    limit: int = 50,
) -> list[IntelCard]:
    """Return grouped IPO and market-cap milestone cards sorted by probability."""
    rows = connection.execute(
        _LATEST_CTE + """
        SELECT m.source, m.market_id, m.title, m.event_title, m.url,
               l.probability, l.volume
        FROM markets m
        JOIN latest l ON m.source=l.source AND m.market_id=l.market_id
        WHERE m.category = 'markets'
          AND l.probability IS NOT NULL
          AND (
              LOWER(m.title) LIKE '%ipo%'
              OR LOWER(m.title) LIKE '%market cap%'
              OR LOWER(m.title) LIKE '%largest company%'
          )
        ORDER BY COALESCE(l.volume, 0) DESC, l.probability DESC
        LIMIT 200
        """
    ).fetchall()

    cards: dict[str, IntelCard] = {}
    for row in rows:
        title = row["title"]
        company = _intel_company(title) or _intel_company(row["event_title"] or "")
        if not company or len(company) < 2:
            continue
        # Normalise company key
        company_key = company.lower().strip()

        if company_key not in cards:
            cards[company_key] = IntelCard(company=company)

        cards[company_key].markets.append(
            IntelMarket(
                source=row["source"],
                market_id=row["market_id"],
                title=title,
                url=row["url"],
                probability=row["probability"],
                volume=row["volume"],
                kind=_intel_kind(title),
            )
        )

    result = list(cards.values())
    result.sort(key=lambda c: c.top_probability, reverse=True)
    return result[:limit]

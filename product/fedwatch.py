"""FOMC Rate Path module.

Parses Polymarket Fed Decision markets from the local DB into per-meeting
probability distributions and a year-end rate path.

CME FedWatch comparison context:
  - CME blocks programmatic API access.
  - Instead we use FRED data as a cross-check:
      * DFF (effective fed funds rate) → infer current target range
      * DGS2 (2Y Treasury yield) → implied terminal rate via the expectations
        hypothesis (2Y yield ≈ avg of fed funds over next 2 years)
  - Research shows prediction-market probabilities are incrementally
    informative over CME for FOMC outcomes (Wolfers & Zitzewitz 2004).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class MeetingProbs:
    meeting: str           # e.g. "June 2026"
    hold: float            # probability of no change
    cut_25: float          # probability of -25 bps
    cut_50: float          # probability of -50+ bps
    hike_25: float         # probability of +25 bps
    hike_50: float         # probability of +50+ bps
    source: str = "polymarket"
    note: str = ""


@dataclass
class YearEndRate:
    rate: str              # e.g. "3.75%"
    rate_value: float      # numeric upper bound
    probability: float
    source: str = "polymarket"


@dataclass
class CutsDistEntry:
    cuts: int                  # number of cuts (0 = no cuts, -1 = special)
    label: str                 # e.g. "0 cuts", "1 cut", "2+ cuts"
    probability: float


@dataclass
class FredContext:
    """FRED-derived market context for the rate path view."""
    dff: float | None = None            # Effective fed funds rate
    dff_date: str | None = None
    t2y: float | None = None            # 2Y Treasury yield
    t2y_date: str | None = None
    current_target_range: str = ""      # Inferred e.g. "3.50–3.75%"
    implied_terminal_rate: float | None = None   # 2Y minus expected term premium (~0.30%)
    implied_cuts_from_current: float | None = None  # How many 25bp cuts implied by 2Y


@dataclass
class FedWatchPayload:
    meetings: list[MeetingProbs] = field(default_factory=list)
    year_end_rates: list[YearEndRate] = field(default_factory=list)
    cuts_distribution: list[CutsDistEntry] = field(default_factory=list)
    current_rate: str = ""              # Dynamic from FRED DFF
    fred_context: FredContext = field(default_factory=FredContext)
    cme_note: str = (
        "CME FedWatch requires proprietary API access. Shown here: "
        "Polymarket-implied probabilities (research shows these are "
        "incrementally informative over CME). FRED cross-check: 2Y Treasury "
        "yield implies an independent rate path estimate."
    )


# ── Parsing helpers ───────────────────────────────────────────────────────────

_OUTCOME_MAP = {
    # hold
    "no change": "hold",
    # cuts
    "decrease.*25 bps": "cut_25",
    "cut.*25 bps": "cut_25",
    "decrease.*50": "cut_50",
    "cut.*50": "cut_50",
    # hikes
    "increase.*25 bps": "hike_25",
    "hike.*25 bps": "hike_25",
    "increase.*50": "hike_50",
    "hike.*50": "hike_50",
}

_MONTH_RE = re.compile(
    r"Fed Decision in (January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\??",
    re.IGNORECASE,
)

_RATE_RE = re.compile(
    r"upper bound.*?([\d.]+)%|be ([\d.]+)%|be (≤|≥|>=|<=)?([\d.]+)%",
    re.IGNORECASE,
)


def _outcome_key(title: str) -> str | None:
    t = title.lower()
    for pattern, key in _OUTCOME_MAP.items():
        if re.search(pattern, t):
            return key
    return None


def _parse_rate_value(title: str) -> tuple[str, float] | None:
    """Return (display_label, numeric_upper_bound) from a rate title."""
    # Handle ≤ and ≥ prefixes
    if "≤" in title or "<=" in title:
        m = re.search(r"([\d.]+)%", title)
        return (f"≤{m.group(1)}%", float(m.group(1))) if m else None
    if "≥" in title or ">=" in title:
        m = re.search(r"([\d.]+)%", title)
        return (f"≥{m.group(1)}%", float(m.group(1)) + 0.01) if m else None  # sort above
    m = re.search(r"([\d.]+)%", title)
    if m:
        return (f"{m.group(1)}%", float(m.group(1)))
    return None


# ── Main fetch ────────────────────────────────────────────────────────────────

_LATEST_CTE = """
WITH latest AS (
    SELECT s.*
    FROM snapshots s
    JOIN (
        SELECT source, market_id, MAX(observed_at) AS observed_at
        FROM snapshots
        GROUP BY source, market_id
    ) n ON s.source=n.source AND s.market_id=n.market_id AND s.observed_at=n.observed_at
)
"""

_MONTH_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


_CUTS_RE = re.compile(r"Will (\d+|no) Fed rate cut", re.IGNORECASE)
_CUTS_MANY_RE = re.compile(r"(\d+) or more Fed rate cut", re.IGNORECASE)


def _infer_target_range(dff: float) -> str:
    """Infer the FOMC target range from the effective fed funds rate.

    The effective rate (DFF) typically trades 5–15 bps below the upper bound.
    We round DFF + 0.12 to the nearest 25bp increment to find the upper bound.
    """
    upper_raw = dff + 0.12
    upper = round(upper_raw * 4) / 4  # nearest 25bp
    lower = upper - 0.25
    return f"{lower:.2f}–{upper:.2f}%"


def fedwatch_data(
    connection: sqlite3.Connection,
    fred_key: str = "",
) -> FedWatchPayload:
    rows = connection.execute(
        _LATEST_CTE + """
        SELECT m.source, m.market_id, m.title, m.event_title, l.probability
        FROM markets m
        JOIN latest l ON m.source=l.source AND m.market_id=l.market_id
        WHERE (
            LOWER(m.event_title) LIKE '%fed decision%'
            OR LOWER(m.event_title) LIKE '%what will the fed rate%'
            OR LOWER(m.event_title) LIKE '%how many fed rate%'
        )
        AND l.probability IS NOT NULL
        ORDER BY m.event_title, l.probability DESC
        """
    ).fetchall()

    # ── Per-meeting distributions ──────────────────────────────────────────
    meeting_buckets: dict[str, dict[str, float]] = {}  # meeting → {outcome → prob}

    for row in rows:
        event = row["event_title"] or ""
        m = _MONTH_RE.search(event)
        if not m:
            continue
        month = m.group(1).capitalize()
        meeting_key = f"{month} 2026"

        key = _outcome_key(row["title"])
        if key is None:
            continue

        bucket = meeting_buckets.setdefault(
            meeting_key,
            {"hold": 0.0, "cut_25": 0.0, "cut_50": 0.0, "hike_25": 0.0, "hike_50": 0.0},
        )
        bucket[key] = max(bucket[key], row["probability"])

    def month_sort(k: str) -> int:
        month = k.split()[0]
        return _MONTH_ORDER.index(month) if month in _MONTH_ORDER else 99

    meetings = [
        MeetingProbs(
            meeting=meeting,
            hold=b.get("hold", 0.0),
            cut_25=b.get("cut_25", 0.0),
            cut_50=b.get("cut_50", 0.0),
            hike_25=b.get("hike_25", 0.0),
            hike_50=b.get("hike_50", 0.0),
        )
        for meeting, b in sorted(meeting_buckets.items(), key=lambda x: month_sort(x[0]))
    ]

    # ── Year-end rate distribution ─────────────────────────────────────────
    year_end: list[YearEndRate] = []
    for row in rows:
        event = row["event_title"] or ""
        if "what will the fed rate" not in event.lower():
            continue
        parsed = _parse_rate_value(row["title"])
        if parsed is None:
            continue
        label, val = parsed
        year_end.append(YearEndRate(rate=label, rate_value=val, probability=row["probability"]))

    year_end.sort(key=lambda r: r.probability, reverse=True)

    # ── "How many cuts" distribution ──────────────────────────────────────
    cuts_raw: dict[int, float] = {}  # cuts count → prob
    for row in rows:
        event = row["event_title"] or ""
        if "how many fed rate" not in event.lower():
            continue
        title = row["title"]
        # "12 or more"
        many_m = _CUTS_MANY_RE.search(title)
        if many_m:
            cuts_raw[int(many_m.group(1))] = row["probability"]
            continue
        # "no Fed rate cuts" → 0 cuts
        if re.search(r"\bno fed rate cut", title, re.IGNORECASE):
            cuts_raw[0] = row["probability"]
            continue
        # "Will N Fed rate cuts"
        n_m = _CUTS_RE.search(title)
        if n_m:
            n = n_m.group(1)
            if n.lower() == "no":
                cuts_raw[0] = row["probability"]
            else:
                cuts_raw[int(n)] = row["probability"]

    cuts_dist: list[CutsDistEntry] = []
    for n, prob in sorted(cuts_raw.items()):
        if n == 0:
            label = "0 cuts (hold all year)"
        elif n == 1:
            label = "1 cut (−25 bps)"
        elif n >= 12:
            label = f"{n}+ cuts"
        else:
            label = f"{n} cuts (−{n * 25} bps)"
        cuts_dist.append(CutsDistEntry(cuts=n, label=label, probability=prob))
    cuts_dist.sort(key=lambda e: e.probability, reverse=True)

    # ── FRED cross-check: dynamic current rate + 2Y implied path ──────────
    fred_ctx = FredContext()
    if fred_key:
        try:
            from .fred_client import get_latest, TTL_DAILY
            dff, dff_date = get_latest(connection, "DFF", fred_key, ttl=TTL_DAILY)
            t2y, t2y_date = get_latest(connection, "DGS2", fred_key, ttl=TTL_DAILY)

            if dff is not None:
                fred_ctx.dff = round(dff, 2)
                fred_ctx.dff_date = dff_date
                fred_ctx.current_target_range = _infer_target_range(dff)

            if t2y is not None:
                fred_ctx.t2y = round(t2y, 2)
                fred_ctx.t2y_date = t2y_date
                # Expectations hypothesis: 2Y yield ≈ avg short rate over 2 yrs
                # Subtract ~0.25% term premium → approximate terminal rate expectation
                if dff is not None:
                    term_premium = 0.25
                    implied_terminal = t2y - term_premium
                    fred_ctx.implied_terminal_rate = round(implied_terminal, 2)
                    # Implied cuts from current: (current - terminal) / 0.25
                    implied_cuts = (dff - implied_terminal) / 0.25
                    fred_ctx.implied_cuts_from_current = round(implied_cuts, 1)
        except Exception:
            pass  # Graceful degradation

    # Dynamic current rate string from FRED (fallback to static)
    current_rate = fred_ctx.current_target_range or "—"

    return FedWatchPayload(
        meetings=meetings,
        year_end_rates=year_end,
        cuts_distribution=cuts_dist,
        current_rate=current_rate,
        fred_context=fred_ctx,
    )

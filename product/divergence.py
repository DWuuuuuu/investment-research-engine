"""Cross-platform divergence detection.

Finds pairs of markets from different sources (Polymarket / Kalshi) that appear
to cover the same underlying event and flags probability gaps above a threshold.

Matching strategy (in order of confidence):
  1. Exact title match (case-insensitive)
  2. Normalised title match — strip dates, punctuation, common stop-prefixes
  3. Jaccard token overlap — shared meaningful words ≥ JACCARD_THRESHOLD

Each pair is returned as a DivergencePair dataclass.
The `alert` flag is True when the absolute probability gap is ≥ ALERT_THRESHOLD (10pp).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# Pairs with delta >= this are flagged as alerts (default: 10pp)
ALERT_THRESHOLD = 0.10

# Minimum Jaccard similarity for tier-3 matching
JACCARD_THRESHOLD = 0.65

# Minimum number of meaningful tokens each side must have for Jaccard matching
JACCARD_MIN_TOKENS = 4

# Words shorter than this are excluded from token sets (noise words)
JACCARD_MIN_WORD_LEN = 3

# Stop-words excluded from Jaccard token sets
_JACCARD_STOPWORDS = frozenset({
    "will", "the", "be", "a", "an", "is", "are", "was", "were", "has", "have",
    "had", "who", "what", "when", "how", "which", "than", "for", "of", "in",
    "on", "at", "to", "by", "or", "and", "not", "no", "yes", "any", "all",
})


# ── Normalisation ─────────────────────────────────────────────────────────────

_DATE_PATTERNS = [
    r"\s+by\s+\w+\s+\d{1,2},?\s+\d{4}",       # "by May 14, 2026"
    r"\s+before\s+\w+\s+\d{1,2},?\s+\d{4}",   # "before Jan 1, 2027"
    r"\s+before\s+\w+\s+\d{4}",                # "before 2027"
    r"\s+by\s+\w+\s+\d{4}",                    # "by 2027"
    r"\s+in\s+\d{4}",                          # "in 2026"
    r"\s+\d{4}\??\s*$",                        # trailing year
    r"\s+q[1-4]\s+\d{4}",                      # Q1 2026
]
_DATE_RE = re.compile("|".join(_DATE_PATTERNS), re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]")
_SPACES_RE = re.compile(r"\s+")

_STOP_PREFIXES = ("will ", "who will ", "what will ", "when will ", "is ", "are ", "does ")


def normalise(title: str) -> str:
    t = title.lower()
    t = _DATE_RE.sub("", t)
    t = _PUNCT_RE.sub(" ", t)
    t = _SPACES_RE.sub(" ", t).strip()
    for prefix in _STOP_PREFIXES:
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    return t


def _token_set(title: str) -> frozenset[str]:
    """Extract meaningful tokens for Jaccard comparison."""
    t = _PUNCT_RE.sub(" ", title.lower())
    t = _DATE_RE.sub("", t)
    tokens = {
        w for w in _SPACES_RE.split(t)
        if len(w) >= JACCARD_MIN_WORD_LEN and w not in _JACCARD_STOPWORDS
    }
    return frozenset(tokens)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_WORD_RE = re.compile(r"\b([A-Z][a-z]{1,})\b")

def _proper_nouns(title: str) -> frozenset[str]:
    """Extract likely proper nouns (Capitalised mid-sentence words), lowercased."""
    words = title.split()
    # Skip the very first word (always capitalised). Look for capitalised words at position 1+.
    result = set()
    for w in words[1:]:
        clean = re.sub(r"[^\w]", "", w)
        if clean and clean[0].isupper() and len(clean) > 1:
            result.add(clean.lower())
    return frozenset(result)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DivergencePair:
    title: str                       # canonical title (Polymarket side)
    event_title: str | None
    category: str
    match_type: str                  # "exact" | "normalised" | "jaccard"
    match_score: float               # 1.0 for exact/normalised, Jaccard score otherwise
    poly_market_id: str
    poly_probability: float
    poly_url: str | None
    kals_market_id: str
    kals_probability: float
    kals_url: str | None
    delta: float                     # abs(poly - kals), always positive
    direction: str                   # "poly_higher" | "kals_higher"
    alert: bool                      # True when delta >= ALERT_THRESHOLD (10pp)


# ── Core query ────────────────────────────────────────────────────────────────

_LATEST_CTE = """
WITH latest AS (
    SELECT s.*
    FROM snapshots s
    JOIN (
        SELECT source, market_id, MAX(observed_at) AS observed_at
        FROM snapshots
        GROUP BY source, market_id
    ) n ON s.source = n.source
       AND s.market_id = n.market_id
       AND s.observed_at = n.observed_at
)
"""


def _fetch_all_with_probs(connection: sqlite3.Connection) -> list[dict]:
    """Return every market with its latest probability."""
    rows = connection.execute(
        _LATEST_CTE + """
        SELECT
            m.source,
            m.market_id,
            m.title,
            m.event_title,
            COALESCE(m.category, 'uncategorized') AS category,
            m.url,
            l.probability
        FROM markets m
        JOIN latest l ON m.source = l.source AND m.market_id = l.market_id
        WHERE l.probability IS NOT NULL
          AND COALESCE(m.category, 'uncategorized') IN
              ('macro','markets','geopolitics','ai_tech','crypto','politics_policy')
        """
    ).fetchall()
    return [dict(r) for r in rows]


def divergence_pairs(
    connection: sqlite3.Connection,
    min_delta: float = 0.05,
    limit: int = 50,
) -> list[DivergencePair]:
    """Find cross-platform pairs whose probabilities differ by >= min_delta.

    Three-tier matching (best match wins):
      Tier 1 — exact title (case-insensitive)
      Tier 2 — normalised title (dates + stop-prefixes stripped)
      Tier 3 — Jaccard token overlap ≥ JACCARD_THRESHOLD

    Pairs with delta ≥ ALERT_THRESHOLD are flagged alert=True.
    """
    rows = _fetch_all_with_probs(connection)

    poly_list = [r for r in rows if r["source"] == "polymarket"]
    kals_list = [r for r in rows if r["source"] == "kalshi"]

    # Build lookup indexes for tiers 1 and 2
    poly_by_exact: dict[str, list[dict]] = {}
    poly_by_norm: dict[str, list[dict]] = {}
    poly_tokens: dict[str, frozenset[str]] = {}

    for r in poly_list:
        exact_key = r["title"].lower().strip()
        poly_by_exact.setdefault(exact_key, []).append(r)
        norm_key = normalise(r["title"])
        poly_by_norm.setdefault(norm_key, []).append(r)
        poly_tokens[r["market_id"]] = _token_set(r["title"])

    pairs: list[DivergencePair] = []
    seen: set[tuple[str, str]] = set()

    def _add_pair(pr: dict, kr: dict, match_type: str, match_score: float) -> None:
        pair_key = (pr["market_id"], kr["market_id"])
        if pair_key in seen:
            return
        seen.add(pair_key)
        delta = abs(pr["probability"] - kr["probability"])
        if delta < min_delta:
            return
        pairs.append(
            DivergencePair(
                title=pr["title"],
                event_title=pr["event_title"] or kr["event_title"],
                category=pr["category"],
                match_type=match_type,
                match_score=round(match_score, 3),
                poly_market_id=pr["market_id"],
                poly_probability=pr["probability"],
                poly_url=pr["url"],
                kals_market_id=kr["market_id"],
                kals_probability=kr["probability"],
                kals_url=kr["url"],
                delta=round(delta, 4),
                direction="poly_higher" if pr["probability"] > kr["probability"] else "kals_higher",
                alert=delta >= ALERT_THRESHOLD,
            )
        )

    for kr in kals_list:
        kals_exact = kr["title"].lower().strip()
        kals_norm = normalise(kr["title"])
        kals_toks = _token_set(kr["title"])

        # Tier 1: exact
        tier1 = poly_by_exact.get(kals_exact, [])
        for pr in tier1:
            _add_pair(pr, kr, "exact", 1.0)

        # Tier 2: normalised (skip if tier 1 hit)
        if not tier1 and kals_norm and len(kals_norm) > 8:
            for pr in poly_by_norm.get(kals_norm, []):
                _add_pair(pr, kr, "normalised", 1.0)

        # Tier 3: Jaccard — only run if kalshi title has enough signal tokens
        if len(kals_toks) >= JACCARD_MIN_TOKENS:
            kals_proper = _proper_nouns(kr["title"])
            for pr in poly_list:
                pair_key = (pr["market_id"], kr["market_id"])
                if pair_key in seen:
                    continue
                p_toks = poly_tokens[pr["market_id"]]
                if len(p_toks) < JACCARD_MIN_TOKENS:
                    continue
                score = jaccard(kals_toks, p_toks)
                if score < JACCARD_THRESHOLD:
                    continue
                # Guard: skip if titles diverge on proper nouns (different candidates
                # within same event template, e.g. "Boris Johnson" vs "Keir Starmer")
                poly_proper = _proper_nouns(pr["title"])
                if kals_proper and poly_proper and not (kals_proper & poly_proper):
                    continue
                _add_pair(pr, kr, "jaccard", score)

    pairs.sort(key=lambda p: (-p.delta, -p.match_score))
    return pairs[:limit]

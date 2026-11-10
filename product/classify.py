from __future__ import annotations

import re
from collections.abc import Iterable


TAXONOMY: dict[str, tuple[str, ...]] = {
    "macro": (
        "fed",
        "federal reserve",
        "rate",
        "inflation",
        "cpi",
        "pce",
        "gdp",
        "recession",
        "unemployment",
        "jobs",
        "treasury",
        "yield",
        "tariff",
        "oil",
        "opec",
        "gold",
        "dollar",
        "yen",
        "yuan",
        "euro",
        "fed chair",
        "interest rate",
    ),
    "crypto": ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "stablecoin", "defi", "nft"),
    "ai_tech": (
        "artificial intelligence",
        "ai",
        "openai",
        "anthropic",
        "nvidia",
        "nvda",
        "semiconductor",
        "chips",
        "tsmc",
        "apple",
        "google",
        "microsoft",
        "meta",
        "tesla",
        "agi",
        "llm",
        "chatgpt",
        "deepseek",
        "fusion",
    ),
    "politics_policy": (
        "election",
        "president",
        "senate",
        "congress",
        "trump",
        "biden",
        "administration",
        "supreme court",
        "bill",
        "regulation",
        "ban",
        "prime minister",
        "parliament",
        "referendum",
        "nominee",
    ),
    "geopolitics": (
        "china",
        "taiwan",
        "russia",
        "ukraine",
        "israel",
        "iran",
        "war",
        "ceasefire",
        "sanction",
        "hong kong",
        "singapore",
        "north korea",
        "nato",
        "g7",
        "g20",
    ),
    "earnings": (
        "beat earnings",
        "miss earnings",
        "earnings estimate",
        "eps estimate",
        "beat eps",
        "miss eps",
        "quarterly earnings",
        "q1 earnings",
        "q2 earnings",
        "q3 earnings",
        "q4 earnings",
        "beat revenue",
        "revenue estimate",
        "analyst estimate",
        "beat analyst",
        "miss analyst",
    ),
    "markets": (
        "s&p",
        "spx",
        "nasdaq",
        "dow",
        "stock",
        "equity",
        "etf",
        "ipo",
        "earnings",
        "market cap",
        "hedge fund",
        "private equity",
        "venture capital",
        "valuation",
    ),
    "sports": ("nba", "nfl", "mlb", "nhl", "soccer", "world cup", "championship", "super bowl", "nba finals", "playoffs", "wimbledon", "olympics"),
    "culture": ("movie", "oscar", "grammy", "song", "album", "celebrity", "netflix", "eurovision", "emmy", "bafta", "cannes", "pope", "james bond"),
    "weather": ("temperature", "weather", "hurricane", "rain", "snow", "climate", "earthquake", "volcano", "celsius"),
}

# Explicit overrides: if any of these keywords appear, force the given category
# regardless of taxonomy scoring. Use for cases where a cultural/sports keyword
# is reliably more specific than a geopolitics or other keyword match.
OVERRIDES: dict[str, tuple[str, ...]] = {
    "culture": ("eurovision", "emmy award", "oscar ceremony", "grammy award", "bafta", "james bond theme", "next pope", "new pope"),
    "sports": ("nba finals", "super bowl winner", "world cup final", "wimbledon champion"),
}


INVESTMENT_RELEVANT = {
    "macro",
    "crypto",
    "ai_tech",
    "politics_policy",
    "geopolitics",
    "markets",
    "earnings",
}


def classify_market(*texts: str | None) -> str:
    haystack = " ".join(text for text in texts if text).lower()
    if not haystack:
        return "uncategorized"

    # Explicit overrides take priority over keyword scoring
    for category, phrases in OVERRIDES.items():
        if any(phrase in haystack for phrase in phrases):
            return category

    scores: dict[str, int] = {}
    for category, keywords in TAXONOMY.items():
        score = sum(1 for keyword in keywords if keyword_matches(haystack, keyword))
        if score:
            scores[category] = score

    if not scores:
        return "uncategorized"

    return max(scores.items(), key=lambda item: item[1])[0]


def keyword_matches(haystack: str, keyword: str) -> bool:
    keyword = keyword.lower()
    if not keyword:
        return False
    if re.search(r"[^a-z0-9\s]", keyword):
        return keyword in haystack
    if " " in keyword:
        return keyword in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", haystack) is not None


def relevance_note(category: str, title: str, event_title: str | None = None) -> str:
    subject = f"{event_title or ''} {title}".lower()
    if category == "macro":
        return "Potential read-through to rates, FX, duration, credit, and equity multiples."
    if category == "geopolitics":
        return "Potential read-through to risk premia, commodities, supply chains, and China/Asia exposure."
    if category == "ai_tech":
        return "Potential read-through to AI infrastructure, semis, software, and mega-cap narratives."
    if category == "crypto":
        return "Potential read-through to crypto liquidity, risk appetite, and retail speculation."
    if category == "earnings":
        return "Prediction-market implied beat/miss probability. Research shows PM earnings accuracy exceeds sell-side consensus day-before."
    if category == "markets":
        return "Directly market-facing; useful for sentiment and catalyst tracking."
    if "rate" in subject or "inflation" in subject:
        return "Potential read-through to discount rates and portfolio duration."
    return "Mostly useful as a forecasting/accountability signal unless linked to a portfolio thesis."


def is_investment_relevant(categories: Iterable[str]) -> bool:
    return any(category in INVESTMENT_RELEVANT for category in categories)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def cents_to_probability(value: Any) -> float | None:
    number = to_float(value)
    if number is None:
        return None
    if number > 1:
        return number / 100
    return number


@dataclass(frozen=True)
class MarketSnapshot:
    source: str
    market_id: str
    title: str
    observed_at: str
    probability: float | None
    event_title: str | None = None
    category: str | None = None
    url: str | None = None
    close_time: str | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    volume: float | None = None
    liquidity: float | None = None
    open_interest: float | None = None
    raw: dict[str, Any] | None = None


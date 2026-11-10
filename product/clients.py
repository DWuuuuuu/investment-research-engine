from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any

from .classify import classify_market
from .models import MarketSnapshot, cents_to_probability, to_float, utc_now_iso


class FetchError(RuntimeError):
    pass


def fetch_json(url: str, timeout: float = 20) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "InvestmentResearchEngine/0.1 (+prediction-market-research)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except Exception as exc:  # pragma: no cover - exact urllib exceptions vary by platform
        raise FetchError(f"Failed to fetch {url}: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FetchError(f"Invalid JSON from {url}: {exc}") from exc


def parse_jsonish_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def first_present(payload: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


class PolymarketClient:
    base_url = "https://gamma-api.polymarket.com"

    def __init__(self, timeout: float = 20):
        self.timeout = timeout

    def fetch_active_markets(self, limit: int = 200) -> list[MarketSnapshot]:
        params = urllib.parse.urlencode(
            {
                "limit": limit,
                "active": "true",
                "closed": "false",
                "order": "volume",
                "ascending": "false",
            }
        )
        events = fetch_json(f"{self.base_url}/events?{params}", timeout=self.timeout)
        if not isinstance(events, list):
            raise FetchError("Unexpected Polymarket events response shape")
        return list(self._normalize_events(events))

    def _normalize_events(self, events: list[dict[str, Any]]) -> Iterable[MarketSnapshot]:
        observed_at = utc_now_iso()
        for event in events:
            if not isinstance(event, dict):
                continue
            event_title = first_present(event, ("title", "question", "name"))
            event_slug = first_present(event, ("slug", "eventSlug"))
            event_category = first_present(event, ("category", "categoryName"))
            markets = event.get("markets") or []
            if not isinstance(markets, list):
                continue

            for market in markets:
                if not isinstance(market, dict):
                    continue
                if market.get("closed") is True or market.get("active") is False:
                    continue

                market_id = str(first_present(market, ("id", "conditionId", "questionID", "questionId")) or "")
                title = str(first_present(market, ("question", "title", "description")) or "")
                if not market_id or not title:
                    continue

                probability = self._extract_probability(market)
                slug = first_present(market, ("slug", "marketSlug")) or event_slug
                url = f"https://polymarket.com/event/{event_slug}" if event_slug else None
                if slug and not event_slug:
                    url = f"https://polymarket.com/market/{slug}"

                category = event_category or classify_market(title, event_title)
                yield MarketSnapshot(
                    source="polymarket",
                    market_id=market_id,
                    title=title,
                    event_title=str(event_title) if event_title else None,
                    category=str(category) if category else None,
                    url=url,
                    close_time=first_present(market, ("endDate", "end_date", "close_time")),
                    observed_at=observed_at,
                    probability=probability,
                    volume=to_float(first_present(market, ("volume", "volumeNum", "volume24hr"))),
                    liquidity=to_float(first_present(market, ("liquidity", "liquidityNum"))),
                    raw={"event": event, "market": market},
                )

    def _extract_probability(self, market: dict[str, Any]) -> float | None:
        """Polymarket's own displayed-price rule: midpoint(bestBid, bestAsk), falling
        back to last-trade price only when the spread exceeds $0.10 (verified against
        Polymarket's docs and live API — outcomePrices already implements exactly this
        server-side). Previously this prioritized bestAsk directly, which is what you
        pay to BUY, not the quoted/displayed probability — always biased high relative
        to the true midpoint (confirmed: 23/2,106 sampled markets differed by ≥5pp,
        one by 98pp). outcomePrices is Polymarket's own computed value, so prefer it
        over reconstructing the midpoint ourselves; only fall back to a hand-rolled
        bid/ask midpoint (with the same wide-spread rule) if outcomePrices is absent."""
        outcomes = parse_jsonish_list(market.get("outcomes"))
        prices = parse_jsonish_list(market.get("outcomePrices"))
        if prices:
            if outcomes:
                for outcome, price in zip(outcomes, prices, strict=False):
                    if str(outcome).lower() == "yes":
                        return cents_to_probability(price)
            return cents_to_probability(prices[0])

        bid = to_float(market.get("bestBid"))
        ask = to_float(market.get("bestAsk"))
        if bid is not None and ask is not None:
            if (ask - bid) <= 0.10:
                return cents_to_probability((bid + ask) / 2)
            last = to_float(market.get("lastTradePrice"))
            if last is not None:
                return cents_to_probability(last)
            return cents_to_probability((bid + ask) / 2)   # still better than ask alone

        direct = first_present(market, ("lastTradePrice", "bestAsk"))
        probability = cents_to_probability(direct)
        if probability is not None:
            return probability

        return cents_to_probability(first_present(market, ("yes_price", "price", "probability")))


class KalshiClient:
    base_url = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, timeout: float = 20):
        self.timeout = timeout

    def fetch_active_markets(self, limit: int = 200) -> list[MarketSnapshot]:
        """Two-pass strategy:
        1. Page through /events (metadata only, cheap) to find investment-relevant events.
        2. Fetch /events/{ticker} individually for each relevant event to get market data.
        This avoids the /markets endpoint which is dominated by KXMV sports prop bundles.
        """
        from .classify import INVESTMENT_RELEVANT, classify_market as _classify

        observed_at = utc_now_iso()
        relevant_tickers: list[tuple[str, str]] = []  # (event_ticker, event_title)
        cursor: str | None = None
        max_event_pages = 10

        for _ in range(max_event_pages):
            params: dict[str, str] = {"status": "open", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            payload = fetch_json(
                f"{self.base_url}/events?{urllib.parse.urlencode(params)}",
                timeout=self.timeout,
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
                raise FetchError("Unexpected Kalshi events response shape")

            for event in payload["events"]:
                if not isinstance(event, dict):
                    continue
                ticker = str(event.get("event_ticker") or "")
                title = str(event.get("title") or "")
                if ticker.startswith("KXMV"):
                    continue
                if _classify(title) in INVESTMENT_RELEVANT:
                    relevant_tickers.append((ticker, title))

            cursor = payload.get("cursor")
            if not cursor:
                break

        snapshots: list[MarketSnapshot] = []
        for event_ticker, event_title in relevant_tickers:
            if len(snapshots) >= limit:
                break
            try:
                event_payload = fetch_json(
                    f"{self.base_url}/events/{event_ticker}",
                    timeout=self.timeout,
                )
            except FetchError:
                continue
            if not isinstance(event_payload, dict):
                continue
            event_meta = event_payload.get("event") or {}
            markets = event_payload.get("markets") or []
            for market in markets:
                if not isinstance(market, dict):
                    continue
                if market.get("status") != "active":
                    continue
                snapshots.append(self._normalize_market(market, event_meta, event_title, observed_at))
                if len(snapshots) >= limit:
                    break

        return snapshots

    def _normalize_market(
        self,
        market: dict[str, Any],
        event_meta: dict[str, Any],
        event_title: str,
        observed_at: str,
    ) -> MarketSnapshot:
        ticker = str(market.get("ticker") or "")
        title = str(first_present(market, ("title",)) or ticker)
        event_ticker = str(event_meta.get("event_ticker") or market.get("event_ticker") or "")
        url = f"https://kalshi.com/markets/{event_ticker}" if event_ticker else None

        # Events API returns prices as dollar strings (e.g. "0.3500" = 35%)
        yes_bid = cents_to_probability(market.get("yes_bid_dollars") or market.get("yes_bid"))
        yes_ask = cents_to_probability(market.get("yes_ask_dollars") or market.get("yes_ask"))
        last_price = cents_to_probability(
            market.get("last_price_dollars") or market.get("yes_price") or market.get("last_trade_price")
        )

        # A market that has never traded reports its last price as "0.0000".
        # Kalshi's lowest tradable price is one cent, so a zero is "no trade
        # yet", not a 0% probability — and it used to be stored as one, beside
        # a live quote of 0.90 / 0.98.
        probability = last_price or None
        if probability is None and yes_bid is not None and yes_ask is not None:
            probability = (yes_bid + yes_ask) / 2
        elif probability is None:
            probability = yes_bid or yes_ask

        return MarketSnapshot(
            source="kalshi",
            market_id=ticker,
            title=title,
            event_title=event_title or None,
            category=classify_market(title, event_title),
            url=url,
            close_time=first_present(market, ("close_time", "expiration_time", "latest_expiration_time")),
            observed_at=observed_at,
            probability=probability,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            volume=to_float(market.get("volume")),
            liquidity=to_float(market.get("liquidity_dollars") or market.get("liquidity")),
            open_interest=to_float(market.get("open_interest_fp") or market.get("open_interest")),
            raw=market,
        )

"""US ticker search — thin proxy over Yahoo Finance's public search endpoint.

Used for autocomplete in the Stock Screen and ETF Screen ticker inputs.
"""

from __future__ import annotations

import requests

SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"


def search_us_tickers(query: str, limit: int = 8) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"q": q, "quotesCount": limit, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    results = []
    for item in data.get("quotes", [])[:limit]:
        symbol = item.get("symbol")
        if not symbol:
            continue
        results.append({
            "symbol":   symbol,
            "name":     item.get("shortname") or item.get("longname") or symbol,
            "type":     item.get("quoteType") or "",
            "exchange": item.get("exchDisp") or "",
        })
    return results

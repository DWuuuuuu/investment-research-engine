"""Tushare Pro HTTP client for A-share (mainland China) market data.

Uses the plain REST API (https://api.tushare.pro) rather than the Tushare MCP
server — the MCP server is for Claude's own tool-calling, while this module
powers the deployed dashboard backend directly.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

TUSHARE_URL = "https://api.tushare.pro"


class TushareError(Exception):
    pass


def _token() -> str:
    return os.environ.get("TUSHARE_TOKEN", "").strip()


def call(api_name: str, params: dict | None = None, fields: str = "") -> list[dict[str, Any]]:
    """Call a Tushare Pro interface, returning a list of row-dicts."""
    token = _token()
    if not token:
        raise TushareError("TUSHARE_TOKEN not set — add it to .env")
    resp = requests.post(
        TUSHARE_URL,
        json={"api_name": api_name, "token": token, "params": params or {}, "fields": fields},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise TushareError(data.get("msg") or f"Tushare error (code {data.get('code')})")
    payload = data.get("data") or {}
    cols = payload.get("fields") or []
    rows = payload.get("items") or []
    return [dict(zip(cols, row)) for row in rows]


# ── Ticker resolution ─────────────────────────────────────────────────────────

_STOCK_BASIC_CACHE: dict[str, Any] = {"ts": 0.0, "rows": []}
_STOCK_BASIC_TTL = 6 * 3600  # refresh every 6 hours


def _stock_basic_list() -> list[dict[str, Any]]:
    now = time.time()
    if now - _STOCK_BASIC_CACHE["ts"] > _STOCK_BASIC_TTL or not _STOCK_BASIC_CACHE["rows"]:
        rows = call(
            "stock_basic",
            {"list_status": "L", "exchange": ""},
            "ts_code,symbol,name,area,industry,market,list_date",
        )
        _STOCK_BASIC_CACHE["rows"] = rows
        _STOCK_BASIC_CACHE["ts"] = now
    return _STOCK_BASIC_CACHE["rows"]


def newest_filing(rows: list[dict]) -> dict[str, Any]:
    """One row out of several for the SAME period: the newest filing, with its
    blanks filled from its own duplicates.

    Tushare often returns a period twice with the same announcement date — one
    row complete, one with half its fields empty. China Merchants Bank's H1
    2026 comes back once with roe_waa 6.71 and once with it blank, and whichever
    happened to sort last used to win. Rows of an OLDER filing never fill a
    blank: that would mix two vintages into one row.
    """
    if not rows:
        return {}
    # Same day, two rows: the updated one (update_flag 1) is the one to keep.
    vs = sorted(rows, key=lambda x: (x.get("ann_date") or "", x.get("update_flag") or ""))
    newest = vs[-1].get("ann_date") or ""
    same = [x for x in vs if (x.get("ann_date") or "") == newest]
    out = dict(same[-1])
    for x in reversed(same[:-1]):
        for k, v in x.items():
            if out.get(k) is None and v is not None:
                out[k] = v
    return out


def names_for(codes: list[str]) -> dict[str, str]:
    """{code as given: company name} for a batch of A-share codes.

    A six-digit number is not a name a person can read. The watchlist shows
    them, so it needs to resolve a whole list at once rather than one search
    per pill — `stock_basic` is already held in memory, so this is a dict
    lookup, not a vendor call.

    Accepts either form the rest of the app uses, "605128" or "605128.SH", and
    keys the answer by exactly what was passed in so the caller can match it
    back to what it stored.
    """
    wanted = [c.strip() for c in (codes or []) if c and c.strip()]
    if not wanted:
        return {}
    rows = _stock_basic_list()
    by_symbol = {r["symbol"]: r.get("name") or "" for r in rows}
    by_code = {r["ts_code"].upper(): r.get("name") or "" for r in rows}
    out: dict[str, str] = {}
    for c in wanted:
        name = by_code.get(c.upper()) or by_symbol.get(c.split(".")[0])
        if name:
            out[c] = name
    return out


def resolve_ticker(query: str) -> dict[str, Any] | None:
    """Resolve a 6-digit code, ts_code (600519.SH), or company name to a stock_basic row."""
    q = (query or "").strip()
    if not q:
        return None

    rows = _stock_basic_list()

    # Exact ts_code match (already has exchange suffix)
    q_upper = q.upper()
    for r in rows:
        if r["ts_code"] == q_upper:
            return r

    # Bare 6-digit code — infer exchange by leading digit convention
    digits = "".join(ch for ch in q if ch.isdigit())
    if len(digits) == 6 and digits == q.strip():
        for r in rows:
            if r["symbol"] == digits:
                return r

    # Name substring match (Chinese or English)
    matches = [r for r in rows if q in (r.get("name") or "")]
    if matches:
        return matches[0]

    return None


def search_stocks(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return up to `limit` stock_basic rows matching a code or name query."""
    q = (query or "").strip()
    if not q:
        return []
    rows = _stock_basic_list()
    q_upper = q.upper()
    digits = "".join(ch for ch in q if ch.isdigit())

    results = []
    for r in rows:
        if r["ts_code"] == q_upper or r["symbol"] == digits or q in (r.get("name") or ""):
            results.append(r)
        if len(results) >= limit:
            break
    return results


# ── Latest-snapshot helpers (Tushare doesn't sort rows reliably) ─────────────

def latest_row(api_name: str, ts_code: str, fields: str, lookback_days: int = 12) -> dict[str, Any] | None:
    """Fetch rows over a short recent window and return the most recent by trade_date."""
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=lookback_days)
    rows = call(api_name, {
        "ts_code": ts_code,
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
    }, fields)
    if not rows:
        return None
    rows.sort(key=lambda r: r.get("trade_date", ""), reverse=True)
    return rows[0]

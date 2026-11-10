from __future__ import annotations

import argparse
import json
import mimetypes
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .clients import FetchError, KalshiClient, PolymarketClient
from .db import connect, init_db, reclassify_markets, upsert_snapshots
from .divergence import divergence_pairs
from .earnings import earnings_markets, market_intelligence
from .fedwatch import fedwatch_data
from .orderflow import orderflow_data
from .deployment import deployment_data
from .screener import screen_ticker
from .ashare_screener import screen_ashare
from .cn_data import search_stocks as cn_search_stocks
from .us_search import search_us_tickers
from .cn_macro import get_cn_cycle_diagnosis
from .filter_screener import (filter_stocks, meta as filter_meta, confirm_growth,
                              score_shortlist, compute_composite, backtest_basket,
                              competitive_position, momentum_concentration,
                              momentum_concentration_series)
import concurrent.futures as _cf
from .macro_layer import (get_cycle_diagnosis, get_valuation_snapshot, get_sentiment_snapshot,
                          get_us_fear_greed)
from .quality import quality_scores
from .radar import radar_items


DEFAULT_DB = "data/prediction_markets.sqlite"
STATIC_DIR = Path(__file__).with_name("static")


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error_response(handler: BaseHTTPRequestHandler, message: str, status: int = 400) -> None:
    json_response(handler, {"error": message}, status=status)


_FILTER_NUM_KEYS = (
    "mcap_min", "mcap_max", "pe_min", "pe_max", "fpe_max", "pb_max", "ps_max",
    "div_yield_min", "eps_min", "price_min", "price_max", "vol_min",
    "below_high_min", "growth_min", "growth_max", "peg_max",
    "mom_min", "mom_max", "roe_min", "roe5y_min", "revgrowth_min", "opprofit_min",
    "inst_min", "inst_max", "insider_min", "insider_max", "inst_holders_min", "top3_max",
    "strategic_max", "earngrowth3y_min", "earngrowth4y_min",
    "earngrowthq4_min", "earngrowthq8_min",
    "reportcov_min", "reportinst_min",
    "accel_top_pct", "garp_top_pct", "timing_top_pct", "h52_top_pct",
    "mansfield_min", "volratio_min", "price_vs_ma_min", "ma_slope_min",
    "sector_rs_min", "sector_rank_max", "limit",
    # stage-checklist scores/metrics
    "base_score_min", "adv_score_min", "top_score_min", "adv_vol_mult_min",
    "rs_pct_min",
)

# Stage-checklist toggles. Kept in a tuple rather than repeated if-blocks so a
# new checklist flag needs one entry, not a copy-pasted branch.
_FILTER_STAGE_BOOL_KEYS = (
    "base_textbook_only", "base_volume_dryup_only",
    "adv_textbook_only", "adv_volume_expanding_only",
    "adv_higher_lows_only", "adv_price_above_only",
    "top_textbook_only",
    "decl_confirmed_only", "decl_volume_rising_only",
)


def _parse_filter_criteria(query: dict[str, list[str]]) -> dict[str, Any]:
    """Turn ?pe_max=20&sectors=Technology,Finance&eps_positive=1 into a criteria dict."""
    def one(k: str) -> str | None:
        v = query.get(k)
        return v[0] if v else None

    c: dict[str, Any] = {}
    for k in _FILTER_NUM_KEYS:
        raw = one(k)
        if raw not in (None, ""):
            try:
                c[k] = float(raw)
            except ValueError:
                pass
    if one("eps_positive") in ("1", "true", "yes"):
        c["eps_positive"] = True
    if one("report_positive_only") in ("1", "true", "yes"):
        c["report_positive_only"] = True
    if one("report_initiated_only") in ("1", "true", "yes"):
        c["report_initiated_only"] = True
    if one("turnaround_only") in ("1", "true", "yes"):
        c["turnaround_only"] = True
    if one("turnaround_only_q") in ("1", "true", "yes"):
        c["turnaround_only_q"] = True
    if one("revenue_accel_only") in ("1", "true", "yes"):
        c["revenue_accel_only"] = True
    if one("revenue_accel_only_q") in ("1", "true", "yes"):
        c["revenue_accel_only_q"] = True
    if one("margin_turnaround_only") in ("1", "true", "yes"):
        c["margin_turnaround_only"] = True
    if one("margin_turnaround_only_q") in ("1", "true", "yes"):
        c["margin_turnaround_only_q"] = True
    if one("profit_accel_only") in ("1", "true", "yes"):
        c["profit_accel_only"] = True
    if one("profit_accel_only_q") in ("1", "true", "yes"):
        c["profit_accel_only_q"] = True
    if one("margin_accel_only") in ("1", "true", "yes"):
        c["margin_accel_only"] = True
    if one("margin_accel_only_q") in ("1", "true", "yes"):
        c["margin_accel_only_q"] = True
    if one("above_resistance_only") in ("1", "true", "yes"):
        c["above_resistance_only"] = True
    for k in _FILTER_STAGE_BOOL_KEYS:
        if one(k) in ("1", "true", "yes"):
            c[k] = True
    # Weinstein stage selection: "2" or "1,2". Parsed to ints so the matcher
    # compares against the numeric stage, never a string.
    raw_stages = one("stages")
    if raw_stages:
        picked = []
        for part in raw_stages.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= 4:
                picked.append(int(part))
        if picked:
            c["stages"] = picked
    if one("rs_basis") in ("ibd", "jt"):
        c["rs_basis"] = one("rs_basis")
    if one("vol_match") in ("all", "any"):
        c["vol_match"] = one("vol_match")
    if one("stage_basis") in ("sma", "ema"):
        c["stage_basis"] = one("stage_basis")
    raw_sec = one("sector_stages")
    if raw_sec:
        picked = [int(x) for x in (p.strip() for p in raw_sec.split(",")) if x.isdigit() and 1 <= int(x) <= 4]
        if picked:
            c["sector_stages"] = picked
    for k in ("sectors", "industries"):
        raw = one(k)
        if raw:
            c[k] = [s.strip() for s in raw.split(",") if s.strip()]
    for k in ("sort", "sort_dir"):
        raw = one(k)
        if raw:
            c[k] = raw
    return c


def bool_param(query: dict[str, list[str]], key: str, default: bool = False) -> bool:
    value = first_param(query, key)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def first_param(query: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def int_param(query: dict[str, list[str]], key: str, default: int) -> int:
    value = first_param(query, key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def float_param(query: dict[str, list[str]], key: str, default: float | None = None) -> float | None:
    value = first_param(query, key)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def make_handler(db_path: str, fred_key: str = "") -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "PredictionMarketDashboard/0.1"

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self.handle_api_get(parsed.path, parse_qs(parsed.query))
                return
            self.serve_static(parsed.path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/ingest":
                self.handle_ingest()
                return
            error_response(self, "Not found", status=HTTPStatus.NOT_FOUND)

        def handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
            try:
                if path == "/api/summary":
                    json_response(self, summary_payload(db_path))
                elif path == "/api/radar":
                    json_response(self, radar_payload(db_path, query))
                elif path == "/api/markets":
                    json_response(self, markets_payload(db_path, query))
                elif path == "/api/divergence":
                    json_response(self, divergence_payload(db_path, query))
                elif path == "/api/fedwatch":
                    json_response(self, fedwatch_payload(db_path, fred_key))
                elif path == "/api/earnings":
                    json_response(self, earnings_payload(db_path, query))
                elif path == "/api/orderflow":
                    json_response(self, orderflow_payload(db_path))
                elif path == "/api/deployment":
                    json_response(self, deployment_payload(db_path, fred_key))
                elif path == "/api/quality":
                    json_response(self, quality_payload(db_path, query))
                elif path == "/api/fomc_path":
                    from .fomc_path import fomc_path
                    try:
                        json_response(self, fomc_path(fred_key))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/macro":
                    json_response(self, macro_payload(db_path, fred_key))
                elif path == "/api/valuation":
                    json_response(self, valuation_payload(db_path, fred_key))
                elif path == "/api/sentiment":
                    json_response(self, sentiment_payload(db_path, fred_key))
                elif path == "/api/us_fear":
                    try:
                        json_response(self, us_fear_payload(db_path, fred_key))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/history":
                    json_response(self, history_payload(db_path, query))
                elif path == "/api/screener":
                    ticker = (query.get("ticker") or [""])[0]
                    json_response(self, screen_ticker(ticker))
                elif path == "/api/ashare_screener":
                    ticker = (query.get("ticker") or [""])[0]
                    json_response(self, screen_ashare(ticker))
                elif path == "/api/cn_fear":
                    from .cn_fear import get_fear
                    try:
                        json_response(self, get_fear())
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/cn_vix":
                    from .cn_vix import get_vix
                    try:
                        json_response(self, get_vix())
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/cn_flow":
                    from .cn_flow import get_institutional_flow
                    try:
                        json_response(self, get_institutional_flow())
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/cn_breadth":
                    from .cn_breadth import get_momentum_index
                    try:
                        days = int((query.get("days") or ["400"])[0])
                        json_response(self, get_momentum_index(history_days=days))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/earnings_line":
                    from .earnings_line import get_ttm_eps
                    try:
                        sym = (query.get("symbol") or [""])[0].strip()
                        mkt = (query.get("market") or ["us"])[0]
                        if not sym:
                            json_response(self, {"status": "error", "error": "symbol required"})
                        else:
                            json_response(self, get_ttm_eps(mkt, sym))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/index_list":
                    from .market_context import INDEX_INSTRUMENTS
                    json_response(self, {"status": "ready", "instruments": INDEX_INSTRUMENTS})
                elif path == "/api/index_history":
                    from .market_context import index_history
                    try:
                        sym = (query.get("symbol") or ["SPY"])[0]
                        json_response(self, index_history(sym))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/cp3":
                    from .cp3_distribution import cp3_all
                    try:
                        vb = (query.get("vol_bar") or ["prior_day"])[0]
                        json_response(self, cp3_all(vol_bar=vb))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/market_context":
                    from .market_context import get_market_context
                    try:
                        json_response(self, get_market_context())
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/us_breadth":
                    from .us_breadth import get_momentum_index as _us_mi
                    try:
                        days = int((query.get("days") or ["400"])[0])
                        scope = (query.get("scope") or ["all"])[0]
                        json_response(self, _us_mi(history_days=days, scope=scope))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/cn_limit":
                    from .cn_limit import get_limit_breadth
                    try:
                        json_response(self, get_limit_breadth())
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/cn_reports":
                    from .cn_reports import get_recent_reports
                    try:
                        days = int((query.get("days") or ["3"])[0])
                        ts_code = (query.get("ts_code") or [None])[0]
                        json_response(self, get_recent_reports(days=days, ts_code=ts_code))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/cn_macro":
                    try:
                        payload = get_cn_cycle_diagnosis()
                    except Exception as exc:
                        payload = {"error": str(exc)}
                    json_response(self, payload)
                elif path == "/api/filter_meta":
                    market = (query.get("market") or ["us"])[0]
                    try:
                        json_response(self, filter_meta(market))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/filter":
                    market = (query.get("market") or ["us"])[0]
                    criteria = _parse_filter_criteria(query)
                    try:
                        json_response(self, filter_stocks(market, criteria))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/momentum_concentration":
                    market = (query.get("market") or ["us"])[0]
                    horizon = (query.get("horizon") or [None])[0]
                    try:
                        json_response(self, momentum_concentration(market, horizon))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/momentum_concentration_series":
                    market = (query.get("market") or ["us"])[0]
                    try:
                        json_response(self, momentum_concentration_series(market))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/filter_confirm":
                    market = (query.get("market") or ["us"])[0]
                    tickers = (query.get("tickers") or [""])[0].split(",")
                    try:
                        json_response(self, confirm_growth(market, tickers))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/filter_score":
                    market = (query.get("market") or ["us"])[0]
                    tickers = (query.get("tickers") or [""])[0].split(",")
                    try:
                        json_response(self, score_shortlist(market, tickers))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/quant_rank":
                    market = (query.get("market") or ["us"])[0]
                    def _wf(k):
                        v = (query.get(k) or [None])[0]
                        try: return float(v) if v is not None else None
                        except ValueError: return None
                    weights = {"value": _wf("value"), "growth": _wf("growth"),
                               "momentum": _wf("momentum"), "size": _wf("size")}
                    limit = (query.get("limit") or ["100"])[0]
                    try:
                        json_response(self, compute_composite(market, weights, limit))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/backtest":
                    market = (query.get("market") or ["us"])[0]
                    tickers = (query.get("tickers") or [""])[0].split(",")
                    start = (query.get("start") or ["2019-01-01"])[0]
                    bench = (query.get("benchmark") or [None])[0]
                    try:
                        json_response(self, backtest_basket(market, tickers, start, bench))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/positions":
                    from .positions import list_positions
                    market = (query.get("market") or [None])[0]
                    json_response(self, list_positions(market))
                elif path == "/api/positions/add":
                    import json as _json
                    from .positions import add_position
                    try:
                        snap = _json.loads((query.get("snapshot") or ["null"])[0])
                        json_response(self, add_position(
                            (query.get("market") or ["us"])[0],
                            (query.get("ticker") or [""])[0],
                            name=(query.get("name") or [None])[0],
                            source=(query.get("source") or [None])[0],
                            lynch_type=(query.get("lynch_type") or [None])[0],
                            snapshot=snap))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/positions/update":
                    import json as _json
                    from .positions import update_position
                    try:
                        pid = int((query.get("id") or ["0"])[0])
                        fields = _json.loads((query.get("fields") or ["{}"])[0])
                        json_response(self, update_position(pid, fields))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/positions/delete":
                    from .positions import delete_position
                    try:
                        json_response(self, delete_position(int((query.get("id") or ["0"])[0])))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/portfolio/quote":
                    from .portfolio import quote_batch
                    market = (query.get("market") or ["us"])[0]
                    tickers = [t for t in (query.get("tickers") or [""])[0].split(",") if t]
                    try:
                        json_response(self, quote_batch(market, tickers))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/portfolio/overlap":
                    from .portfolio import asset_overlap
                    market = (query.get("market") or ["us"])[0]
                    tickers = [t for t in (query.get("tickers") or [""])[0].split(",") if t]
                    try:
                        json_response(self, asset_overlap(market, tickers))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/portfolio/backtest":
                    import json as _json
                    from .portfolio import backtest_portfolio
                    market = (query.get("market") or ["us"])[0]
                    try:
                        holds = _json.loads((query.get("holdings") or ["[]"])[0])
                        years = int((query.get("years") or ["5"])[0])
                        json_response(self, backtest_portfolio(market, holds, years))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/valuation_pctile":
                    from .valuation import valuation_percentile
                    market = (query.get("market") or ["us"])[0]
                    ticker = (query.get("ticker") or [""])[0]
                    years = int((query.get("years") or ["5"])[0])
                    try:
                        json_response(self, valuation_percentile(market, ticker, years))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/siglab":
                    import json as _json
                    from .signal_lab import run_signal_test
                    market = (query.get("market") or ["us"])[0]
                    ticker = (query.get("ticker") or [""])[0]
                    try:
                        conds = _json.loads((query.get("conds") or ["[]"])[0])
                        lookback = int((query.get("lookback") or ["10"])[0])
                        horizons = [h for h in (query.get("horizons") or [""])[0].split(",") if h] or None
                        json_response(self, run_signal_test(market, ticker, conds,
                                                            lookback_years=lookback, horizons=horizons))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/peers":
                    from .peers import peer_comparison
                    try:
                        json_response(self, peer_comparison(
                            (query.get("market") or ["us"])[0],
                            (query.get("ticker") or [""])[0],
                            (query.get("add") or [""])[0],
                            (query.get("drop") or [""])[0]))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/competitive":
                    market = (query.get("market") or ["us"])[0]
                    ticker = (query.get("ticker") or [""])[0]
                    try:
                        json_response(self, competitive_position(market, ticker))
                    except Exception as exc:
                        json_response(self, {"status": "error", "error": str(exc)})
                elif path == "/api/ashare_search":
                    q = (query.get("q") or [""])[0]
                    try:
                        rows = cn_search_stocks(q, limit=8)
                        results = [{
                            "symbol":   r.get("symbol") or r.get("ts_code"),
                            "name":     r.get("name") or "",
                            "type":     "EQUITY",
                            "exchange": r.get("market") or "",
                        } for r in rows]
                    except Exception:
                        results = []
                    json_response(self, {"results": results})
                elif path == "/api/ashare_names":
                    codes = [c for c in (query.get("codes") or [""])[0].split(",") if c][:60]
                    try:
                        from .cn_data import names_for
                        json_response(self, {"names": names_for(codes)})
                    except Exception as exc:                   # noqa: BLE001
                        json_response(self, {"names": {}, "error": str(exc)})
                elif path == "/api/us_search":
                    q = (query.get("q") or [""])[0]
                    try:
                        results = search_us_tickers(q, limit=8)
                    except Exception:
                        results = []
                    json_response(self, {"results": results})
                elif path == "/api/etf_batch":
                    raw = (query.get("tickers") or [""])[0]
                    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()][:12]
                    with _cf.ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as ex:
                        results = list(ex.map(screen_ticker, tickers))
                    json_response(self, {"etfs": results})
                elif path == "/api/vix_history":
                    try:
                        import yfinance as yf
                        hist = yf.Ticker("^VIX").history(period="5y")
                        vix = [
                            {"d": str(idx.date()), "v": round(float(row["Close"]), 2)}
                            for idx, row in hist.iterrows()
                        ] if not hist.empty else []
                    except Exception:
                        vix = []
                    json_response(self, {"vix": vix})
                elif path.startswith("/api/live_price"):
                    from urllib.parse import parse_qs, urlparse
                    qs = parse_qs(urlparse(self.path).query)
                    sym = qs.get("ticker", [""])[0].upper().strip()
                    if not sym:
                        json_response(self, {"error": "missing ticker"})
                    else:
                        try:
                            import yfinance as yf
                            fi = yf.Ticker(sym).fast_info
                            price = float(fi.last_price) if fi.last_price is not None else None
                            prev  = float(fi.previous_close) if fi.previous_close is not None else None
                            json_response(self, {"price": price, "prev_close": prev})
                        except Exception as exc:
                            json_response(self, {"error": str(exc)})
                else:
                    error_response(self, "Not found", status=HTTPStatus.NOT_FOUND)
            except Exception as exc:
                error_response(self, str(exc), status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def handle_ingest(self) -> None:
            content_length = int(self.headers.get("Content-Length") or "0")
            raw_body = self.rfile.read(content_length) if content_length else b"{}"
            try:
                body = json.loads(raw_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                error_response(self, "Invalid JSON body")
                return

            source = body.get("source") or "all"
            limit = int(body.get("limit") or 200)
            timeout = float(body.get("timeout") or 20)
            if source not in {"all", "polymarket", "kalshi"}:
                error_response(self, "source must be all, polymarket, or kalshi")
                return

            snapshots = []
            warnings: list[str] = []
            clients = []
            if source in {"all", "polymarket"}:
                clients.append(PolymarketClient(timeout=timeout))
            if source in {"all", "kalshi"}:
                clients.append(KalshiClient(timeout=timeout))

            for client in clients:
                try:
                    snapshots.extend(client.fetch_active_markets(limit=limit))
                except FetchError as exc:
                    warnings.append(str(exc))

            connection = connect(db_path)
            count = upsert_snapshots(connection, snapshots)
            reclassified = reclassify_markets(connection)
            json_response(
                self,
                {
                    "stored": count,
                    "reclassified": reclassified,
                    "warnings": warnings,
                    "summary": summary_payload(db_path),
                },
                status=200 if count else 207,
            )

        def serve_static(self, path: str) -> None:
            if path in {"", "/"}:
                path = "/index.html"
            relative = Path(path.lstrip("/"))
            if ".." in relative.parts:
                error_response(self, "Invalid path", status=HTTPStatus.BAD_REQUEST)
                return

            file_path = STATIC_DIR / relative
            if not file_path.exists() or not file_path.is_file():
                file_path = STATIC_DIR / "index.html"

            content = file_path.read_bytes()
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            if file_path.suffix == ".js":
                content_type = "application/javascript"
            elif file_path.suffix == ".css":
                content_type = "text/css"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return DashboardHandler


def summary_payload(db_path: str) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    totals = connection.execute(
        """
        SELECT
            COUNT(*) AS markets,
            (SELECT COUNT(*) FROM snapshots) AS snapshots,
            MIN(first_seen_at) AS first_seen_at,
            MAX(last_seen_at) AS last_seen_at
        FROM markets
        """
    ).fetchone()
    categories = connection.execute(
        """
        SELECT COALESCE(category, 'uncategorized') AS category, COUNT(*) AS count
        FROM markets
        GROUP BY 1
        ORDER BY count DESC, category
        """
    ).fetchall()
    sources = connection.execute(
        """
        SELECT source, COUNT(*) AS count
        FROM markets
        GROUP BY source
        ORDER BY source
        """
    ).fetchall()
    return {
        "markets": totals["markets"],
        "snapshots": totals["snapshots"],
        "first_seen_at": totals["first_seen_at"],
        "last_seen_at": totals["last_seen_at"],
        "categories": [dict(row) for row in categories],
        "sources": [dict(row) for row in sources],
    }


def radar_payload(db_path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    lookback_hours = int_param(query, "lookbackHours", 24)
    limit = int_param(query, "limit", 25)
    max_per_event = int_param(query, "maxPerEvent", 2)
    min_probability = float_param(query, "minProbability", 0.02)
    min_volume = float_param(query, "minVolume")
    category = first_param(query, "category")
    investment_only = not bool_param(query, "allCategories")
    items = radar_items(
        connection,
        lookback_hours=lookback_hours,
        limit=limit,
        min_volume=min_volume,
        min_probability=min_probability,
        category=category or None,
        investment_only=investment_only,
        max_per_event=max_per_event or None,
    )
    return {"items": [asdict(item) for item in items]}


def markets_payload(db_path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    limit = min(int_param(query, "limit", 100), 500)
    source = first_param(query, "source")
    category = first_param(query, "category")
    search = first_param(query, "q")
    sort = first_param(query, "sort", "volume") or "volume"

    filters = []
    params: list[Any] = []
    if source:
        filters.append("m.source = ?")
        params.append(source)
    if category:
        filters.append("COALESCE(m.category, 'uncategorized') = ?")
        params.append(category)
    if search:
        filters.append("(m.title LIKE ? OR COALESCE(m.event_title, '') LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    order_by = {
        "probability": "latest.probability DESC",
        "volume": "COALESCE(latest.volume, 0) DESC",
        "liquidity": "COALESCE(latest.liquidity, 0) DESC",
        "updated": "latest.observed_at DESC",
    }.get(sort, "COALESCE(latest.volume, 0) DESC")

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(limit)
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
        )
        SELECT
            m.source,
            m.market_id,
            m.title,
            m.event_title,
            COALESCE(m.category, 'uncategorized') AS category,
            m.url,
            m.close_time,
            latest.observed_at,
            latest.probability,
            latest.yes_bid,
            latest.yes_ask,
            latest.volume,
            latest.liquidity,
            latest.open_interest
        FROM markets m
        LEFT JOIN latest
          ON m.source = latest.source AND m.market_id = latest.market_id
        {where_clause}
        ORDER BY {order_by}
        LIMIT ?
        """,
        params,
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


def history_payload(db_path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    source = first_param(query, "source")
    market_id = first_param(query, "marketId")
    if not source or not market_id:
        raise ValueError("source and marketId are required")

    connection = connect(db_path)
    init_db(connection)
    market = connection.execute(
        """
        SELECT source, market_id, title, event_title, category, url
        FROM markets
        WHERE source = ? AND market_id = ?
        """,
        (source, market_id),
    ).fetchone()
    rows = connection.execute(
        """
        SELECT observed_at, probability, yes_bid, yes_ask, volume, liquidity, open_interest
        FROM snapshots
        WHERE source = ? AND market_id = ?
        ORDER BY observed_at
        LIMIT 1000
        """,
        (source, market_id),
    ).fetchall()
    return {"market": dict(market) if market else None, "snapshots": [dict(row) for row in rows]}


def quality_payload(db_path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    limit = min(int_param(query, "limit", 50), 200)
    category = first_param(query, "category")
    min_score = int_param(query, "minScore", 0)
    from dataclasses import asdict
    results = quality_scores(connection, limit=limit, category=category or None, min_score=min_score)
    return {"items": [asdict(r) for r in results], "total": len(results)}


def earnings_payload(db_path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    limit = min(int_param(query, "limit", 50), 200)
    cards = earnings_markets(connection, limit=limit)
    intel_cards = market_intelligence(connection, limit=30)

    earnings_out = []
    for card in cards:
        earnings_out.append({
            "company": card.company,
            "quarter": card.quarter,
            "beat_prob": card.beat_prob,
            "miss_prob": card.miss_prob,
            "markets": [
                {
                    "source": m.source,
                    "market_id": m.market_id,
                    "title": m.title,
                    "url": m.url,
                    "probability": m.probability,
                    "volume": m.volume,
                    "metric": m.metric,
                    "outcome_type": m.outcome_type,
                    "consensus": m.consensus,
                }
                for m in card.markets
            ],
        })

    intel_out = []
    for ic in intel_cards:
        intel_out.append({
            "company": ic.company,
            "summary_kind": ic.summary_kind,
            "top_probability": round(ic.top_probability, 4),
            "markets": [
                {
                    "source": m.source,
                    "market_id": m.market_id,
                    "title": m.title,
                    "url": m.url,
                    "probability": m.probability,
                    "volume": m.volume,
                    "kind": m.kind,
                }
                for m in ic.markets[:5]
            ],
        })

    return {
        "cards": earnings_out,
        "total": len(earnings_out),
        "intel": intel_out,
        "intel_total": len(intel_out),
        "earnings_season_note": (
            "Earnings prediction markets are typically active during earnings season: "
            "Q1 (Apr–May), Q2 (Jul–Aug), Q3 (Oct–Nov), Q4 (Jan–Feb). "
            "Research shows prediction-market implied probabilities outperform sell-side consensus."
        ),
    }


def orderflow_payload(db_path: str) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    data = orderflow_data(connection)
    if data.error:
        return {"error": data.error, "markets": [], "top_wallets": [], "global_large_trades": []}
    return {
        "markets": [
            {
                "market_id": m.market_id,
                "title": m.title,
                "category": m.category,
                "probability": m.probability,
                "volume": m.volume,
                "hhi": m.hhi,
                "concentration_label": m.concentration_label,
                "top_wallets": m.top_wallets,
                "large_trades": [
                    {
                        "wallet": t.wallet,
                        "name": t.name or t.wallet[:10] + "…",
                        "side": t.side,
                        "outcome": t.outcome,
                        "usd": round(t.usd, 0),
                        "shares": round(t.size, 1),
                        "price": round(t.price, 3),
                        "timestamp": t.timestamp,
                    }
                    for t in m.large_trades
                ],
                "url": m.url,
            }
            for m in data.markets
        ],
        "top_wallets": [
            {
                "wallet": w.wallet,
                "name": w.name,
                "total_buy_usd": w.total_buy_usd,
                "total_sell_usd": w.total_sell_usd,
                "markets_active": w.markets_active,
                "recent_markets": w.recent_markets,
            }
            for w in data.top_wallets
        ],
        "global_large_trades": data.global_large_trades,
        "note": (
            "Order-flow data sourced from Polymarket public trades API. "
            "HHI (Herfindahl-Hirschman Index) measures concentration: "
            "< 0.10 = distributed, 0.10–0.25 = moderate, > 0.25 = whale-dominated."
        ),
    }


def fedwatch_payload(db_path: str, fred_key: str = "") -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    from dataclasses import asdict
    data = fedwatch_data(connection, fred_key=fred_key)
    return asdict(data)


def macro_payload(db_path: str, fred_key: str) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    return get_cycle_diagnosis(connection, fred_key)


def valuation_payload(db_path: str, fred_key: str) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    return get_valuation_snapshot(connection, fred_key)


def sentiment_payload(db_path: str, fred_key: str) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    return get_sentiment_snapshot(connection, fred_key)


def us_fear_payload(db_path: str, fred_key: str) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    return get_us_fear_greed(connection, fred_key)


def deployment_payload(db_path: str, fred_key: str) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    return deployment_data(connection, fred_key)


def divergence_payload(db_path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    connection = connect(db_path)
    init_db(connection)
    min_delta = float_param(query, "minDelta", 0.05) or 0.05
    limit = min(int_param(query, "limit", 50), 200)
    from dataclasses import asdict
    pairs = divergence_pairs(connection, min_delta=min_delta, limit=limit)
    return {"pairs": [asdict(p) for p in pairs], "min_delta": min_delta}


def _load_dotenv() -> None:
    """Load key=value pairs from .env in the project root (two levels up from this file)."""
    import os
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:   # don't override real env vars
                os.environ[key] = value


def build_parser() -> argparse.ArgumentParser:
    import os
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Local prediction market dashboard")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--fred-key",
        default=os.environ.get("FRED_API_KEY", ""),
        help="FRED API key (or set FRED_API_KEY env var, or add to .env). Get a free key at fred.stlouisfed.org/docs/api/api_key.html",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    connection = connect(args.db)
    init_db(connection)
    fred_key = args.fred_key
    if fred_key:
        print(f"FRED API: connected (live macro data enabled)")
    else:
        print("FRED API: no key — macro views will show cached/static data only")
        print("  Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.db, fred_key))
    print(f"Prediction market dashboard: http://{args.host}:{args.port}")
    print(f"Database: {args.db}")
    try:
        # The sell-side research cache fills over hours under a vendor quota and
        # must not depend on someone leaving its page open.
        from .cn_reports import resume_on_startup
        resume_on_startup()
    except Exception as exc:                           # noqa: BLE001 — never block the server on it
        print(f"[cn_reports] not resumed at startup: {type(exc).__name__}: {exc}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

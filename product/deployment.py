"""
Cash Deployment Playbook — backend payload.

Assembles VIX signal + valuation state + Dalio season into a single
structured payload consumed by the /api/deployment endpoint.

All data comes from FRED via the existing macro_layer helpers.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .fred_client import get_latest, get_recent_values, TTL_DAILY, TTL_MONTHLY
from .macro_layer import (
    S, STATIC_VALUATIONS,
    _classify_vix, _vix_deployment_signal,
    _compute_its, _compute_cfnai_trend, _classify_regime,
)


# ── Valuation state ───────────────────────────────────────────────────────────

def _valuation_state(cape: float, fwd_pe: float, erp: float | None) -> dict:
    """Map the 3-signal system to a named band."""
    expensive_count = 0
    if cape > 38:
        expensive_count += 1
    if fwd_pe > 26:
        expensive_count += 1
    if erp is not None and erp < 0:
        expensive_count += 1

    normal_count = 0
    if 25 <= cape <= 38:
        normal_count += 1
    if 17 <= fwd_pe <= 26:
        normal_count += 1
    if erp is not None and 1 <= erp <= 3:
        normal_count += 1

    cheap_count = 0
    if cape < 25:
        cheap_count += 1
    if fwd_pe < 17:
        cheap_count += 1
    if erp is not None and erp > 3:
        cheap_count += 1

    if expensive_count >= 2 and (erp is None or erp < 0):
        return {"state": "extreme", "label": "Extreme Overvaluation",
                "reserve_min": 15, "reserve_max": 20, "color": "danger"}
    if expensive_count >= 2:
        return {"state": "expensive", "label": "Expensive",
                "reserve_min": 10, "reserve_max": 15, "color": "warning"}
    if cheap_count >= 2:
        return {"state": "cheap", "label": "Cheap",
                "reserve_min": 5, "reserve_max": 8, "color": "positive"}
    return {"state": "normal", "label": "Normal",
            "reserve_min": 8, "reserve_max": 10, "color": "neutral"}


# ── VIX next trigger ──────────────────────────────────────────────────────────

_VIX_THRESHOLDS = [
    {"vix": 20, "level": "elevated", "label": "Elevated Anxiety", "deploy_pct": 25},
    {"vix": 30, "level": "high",     "label": "Fear",             "deploy_pct": 50},
    {"vix": 40, "level": "panic",    "label": "Panic / Crisis",   "deploy_pct": 75},
]

def _next_trigger(current_vix: float | None) -> dict | None:
    if current_vix is None:
        return None
    for t in _VIX_THRESHOLDS:
        if current_vix < t["vix"]:
            return {**t, "distance": round(t["vix"] - current_vix, 1)}
    return None  # already at panic


# ── Season → favoured assets ─────────────────────────────────────────────────

_SEASON_ASSETS = {
    "growing":     {"favor": ["Equities (VOO, QQQ, SMH)", "Growth stocks"],
                    "avoid": ["Long bonds", "Commodities"]},
    "heating":     {"favor": ["Gold (GDE)", "Commodities", "Real assets", "TIPS"],
                    "avoid": ["Long-duration growth stocks", "Long bonds"]},
    "slowing":     {"favor": ["Bonds / VGSH", "Cash", "Defensives (healthcare, staples)"],
                    "avoid": ["Cyclicals", "High-beta growth", "Crypto"]},
    "stagflation": {"favor": ["Gold (GDE)", "TIPS", "Short-duration bonds"],
                    "avoid": ["Equities broadly", "Long bonds", "Crypto"]},
    "unknown":     {"favor": ["Diversified index (VOO)", "Cash"],
                    "avoid": []},
}


# ── Overall posture ───────────────────────────────────────────────────────────

def _overall_posture(vix_level: str, val_state: str) -> dict:
    if vix_level in ("panic", "high"):
        return {
            "posture": "deploy",
            "label": "Deploy Opportunity Reserve",
            "color": "warning",
            "headline": "VIX elevated — your pre-committed rules say deploy a tranche now.",
        }
    if vix_level == "elevated":
        return {
            "posture": "deploy_partial",
            "label": "Deploy 25% of Reserve",
            "color": "caution",
            "headline": "Elevated VIX. Rules say deploy 25% of opportunity cash reserve.",
        }
    if val_state in ("cheap",):
        return {
            "posture": "deploy_base",
            "label": "Consider Lump Sum",
            "color": "positive",
            "headline": "Valuations cheap. Consider accelerating deployment beyond DCA.",
        }
    return {
        "posture": "hold_dca",
        "label": "Hold & DCA",
        "color": "neutral",
        "headline": "VIX normal, valuations expensive. Continue DCA — no extra lump-sum deployment.",
    }


# ── Layer status cards ────────────────────────────────────────────────────────

def _layer_cards(vix_signal: dict, val: dict, season: dict) -> list[dict]:
    return [
        {
            "num": 1, "title": "Base DCA",
            "status": "active", "status_label": "Always On",
            "color": "positive",
            "detail": "Fixed monthly DCA into VOO/VTI regardless of conditions.",
            "action": "Continue your regular DCA schedule. Never pause this layer.",
        },
        {
            "num": 2, "title": "Reserve Size",
            "status": "check", "status_label": "Check vs Target",
            "color": val["color"],
            "detail": f"At current valuations ({val['label']}), target reserve is {val['reserve_min']}–{val['reserve_max']}% of NW.",
            "action": f"Enter your portfolio value and cash below to see if you're on target.",
        },
        {
            "num": 3, "title": "VIX Trigger",
            "status": "monitoring", "status_label": "Monitoring",
            "color": vix_signal.get("color", "neutral"),
            "detail": f"Current VIX: {vix_signal['value']:.1f} ({vix_signal['label']}). {vix_signal['note']}",
            "action": vix_signal["action"],
        },
        {
            "num": 4, "title": "Stock Patrol",
            "status": "patient", "status_label": "Patient",
            "color": "neutral",
            "detail": "Individual stocks only deploy when a specific business passes all 3 gates + fly checklist at the right price.",
            "action": "No forced deployment. Run the 3-gate screen when a candidate appears.",
        },
        {
            "num": 5, "title": "Economic Season",
            "status": "calibrated", "status_label": season.get("label", "—"),
            "color": season.get("color", "neutral"),
            "detail": season.get("description", ""),
            "action": f"Favour: {', '.join(_SEASON_ASSETS.get(season.get('id','unknown'), {}).get('favor', [])[:2])}.",
        },
    ]


# ── Public API ────────────────────────────────────────────────────────────────

def deployment_data(connection: sqlite3.Connection, fred_key: str) -> dict:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # VIX
    vix_val, vix_date = get_latest(connection, S["VIX"], fred_key)
    vix_signal = _vix_deployment_signal(vix_val, vix_date)
    next_trigger = _next_trigger(vix_val)

    # Valuation — CAPE (static), Fwd P/E (static), ERP (live)
    cape = next((s["value"] for s in STATIC_VALUATIONS if s["id"] == "cape"), 41.83)
    cape_date = next((s["as_of"] for s in STATIC_VALUATIONS if s["id"] == "cape"), None)
    fwd_pe = next((s["value"] for s in STATIC_VALUATIONS if s["id"] == "forward_pe"), 20.9)
    fwd_pe_date = next((s["as_of"] for s in STATIC_VALUATIONS if s["id"] == "forward_pe"), None)
    t10_val, t10_date = get_latest(connection, S["T10Y"], fred_key)
    cape_ey = round(1 / cape * 100, 2) if cape else None
    erp = round(cape_ey - t10_val, 2) if cape_ey and t10_val else None

    val = _valuation_state(cape, fwd_pe, erp)

    # Dalio season
    its, _, _ = _compute_its(connection, fred_key)
    cfnai_val, _ = get_latest(connection, S["CFNAI"], fred_key, ttl=TTL_MONTHLY)
    season = _classify_regime(cfnai_val, its)
    season_assets = _SEASON_ASSETS.get(season["id"], _SEASON_ASSETS["unknown"])

    overall = _overall_posture(vix_signal["level"], val["state"])
    layers = _layer_cards(vix_signal, val, season)

    return {
        "overall": overall,
        "valuation": {
            **val,
            "cape": cape, "cape_date": cape_date,
            "fwd_pe": fwd_pe, "fwd_pe_date": fwd_pe_date,
            "erp": erp, "erp_date": t10_date,
            "t10y": t10_val,
        },
        "vix_signal": {**vix_signal, "next_trigger": next_trigger},
        "season": {**season, **season_assets},
        "layers": layers,
        "as_of": now_str,
        "fred_live": bool(fred_key),
    }

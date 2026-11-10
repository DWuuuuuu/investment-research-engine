"""
Macro Layer — Cycle Diagnosis and S&P 500 Valuation Snapshot.

Provides three top-level functions consumed by web.py — a MECE split (2026-08-02) of
three non-overlapping lenses: what phase the economy is in (Cycle), whether the market
is priced cheap or expensive (Valuation), and how fearful/greedy positioning is (Sentiment):

  get_cycle_diagnosis(connection, fred_key)    →  dict
  get_valuation_snapshot(connection, fred_key) →  dict
  get_sentiment_snapshot(connection, fred_key) →  dict

All live data comes from FRED via fred_client.  Slow-moving valuation
indicators (CAPE, forward P/E, Buffett Indicator, Tobin's Q) are stored as
static constants updated manually from Shiller/Yale, FactSet, and the
Federal Reserve Z.1 release.  Each static value carries an explicit
``as_of`` date so stale data is visible on the UI.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .fred_client import get_latest, get_recent_values, get_series, TTL_DAILY, TTL_MONTHLY

# ---------------------------------------------------------------------------
# FRED series IDs
# ---------------------------------------------------------------------------
S = {
    # Yield / rates
    "YIELD_CURVE":   "T10Y2Y",        # 10Y − 2Y Treasury spread (daily, %)
    "REAL_YIELD":    "DFII10",        # 10Y TIPS real yield (daily, %)
    "T10Y":          "DGS10",         # 10Y nominal Treasury (daily, %)
    "T2Y":           "DGS2",          # 2Y nominal Treasury (daily, %)
    "FED_RATE":      "DFF",           # Effective Fed Funds Rate (daily, %)
    # Volatility / credit / financial conditions
    "VIX":           "VIXCLS",        # CBOE VIX (daily)
    "HY_SPREAD":     "BAMLH0A0HYM2",  # ICE BofA HY OAS (daily, % — ×100 → bps)
    "SP500":         "SP500",         # S&P 500 daily close (for the Fear & Greed price-strength/momentum legs)
    "IG_SPREAD":     "BAMLC0A0CM",    # ICE BofA IG OAS (daily, % — ×100 → bps)
    "BAA_SPREAD":    "BAA10Y",        # Moody's BAA − 10Y Treasury (daily, % — ×100 → bps)
    "STLFSI":        "STLFSI4",       # St. Louis Fed Financial Stress Index (weekly, z-score)
    "NFCI":          "NFCI",          # Chicago Fed National Financial Conditions (weekly, z-score)
    # Macro / cycle
    "CORE_PCE":      "PCEPILFE",      # Core PCE Price Index (monthly, index) — Fed's actual target
    "CORE_CPI":      "CPILFESL",      # Core CPI (monthly, index) — for display alongside PCE
    "SAHM":          "SAHMREALTIME",  # Sahm Rule real-time (monthly)
    "UNEMPLOYMENT":  "UNRATE",        # Unemployment rate (monthly, %)
    "CFNAI":         "CFNAI",         # Chicago Fed National Activity Index (monthly, diffusion)
    # Inflation expectations
    "BREAKEVEN_10Y": "T10YIE",        # 10Y inflation breakeven (daily, %)
    "BREAKEVEN_5Y":  "T5YIE",         # 5Y inflation breakeven (daily, %)
    "FWD_INFLATION": "T5YIFR",        # 5Y5Y forward inflation expectation (daily, %)
}

# ---------------------------------------------------------------------------
# Static valuation constants  (update monthly from primary sources)
# ---------------------------------------------------------------------------
STATIC_VALUATIONS: list[dict] = [
    {
        # Verified against multpl.com 2026-08-10 (Codex audit flagged this table as
        # 101-131 days stale — was 41.83 as_of 2026-05-01; multpl showed 42.39 as of
        # 2026-08-07 close). Still a manually-updated static value — refresh periodically.
        "id": "cape",
        "label": "Shiller CAPE",
        "value": 42.39,
        "unit": "x",
        "as_of": "2026-08-07",
        "percentile": "98–99th",
        "long_run_avg": "17.4",
        "source": "Shiller / Yale (econ.yale.edu/~shiller) via multpl.com",
        "description": (
            "10-year cyclically adjusted P/E. The most reliable long-horizon "
            "return predictor — current reading rivals the 1929 and 2000 peaks."
        ),
    },
    {
        # Verified against FactSet's Aug 7, 2026 Earnings Season Update 2026-08-10
        # (Codex audit: product showed 20.9x as_of 2026-05-01 vs FactSet's actual
        # 20.1x on Jul 24 — confirmed real staleness/drift). Still manually-updated.
        "id": "forward_pe",
        "label": "Forward P/E (NTM)",
        "value": 20.0,
        "unit": "x",
        "as_of": "2026-08-07",
        "percentile": "80–85th",
        "long_run_avg": "19.0",
        "source": "FactSet Earnings Insight (manual update)",
        "description": (
            "Price / next-12-months consensus EPS. Most widely tracked near-term "
            "multiple. Currently above both the 5Y (19.9x) and 10Y (19.0x) averages, "
            "though below the 20.4x recorded at the June 30, 2026 quarter-end."
        ),
    },
    {
        "id": "buffett_indicator",
        "label": "Buffett Indicator",
        "value": 200.0,
        "unit": "%",
        "as_of": "2026-05-01",
        "percentile": "All-time high range",
        "long_run_avg": "~80",
        "source": "GuruFocus / AdvisorPerspectives (manual update)",
        "description": (
            "Total US equity market cap / GDP. Buffett's preferred single-number "
            "market measure. Current ~195–228% — at or near the all-time record."
        ),
    },
    {
        "id": "tobins_q",
        "label": "Tobin's Q",
        "value": 2.07,
        "unit": "x",
        "as_of": "2026-04-01",
        "percentile": "~99th (near record)",
        "long_run_avg": "~0.75",
        "source": "Federal Reserve Z.1 / AdvisorPerspectives (manual update)",
        "description": (
            "Market value / replacement cost of corporate assets. Theoretical "
            "equilibrium = 1.0. Current 2.07 is 173% above the geometric average."
        ),
    },
    {
        "id": "price_sales",
        "label": "Price / Sales",
        "value": 3.4,
        "unit": "x",
        "as_of": "2026-05-01",
        "percentile": "+2.5 std dev",
        "long_run_avg": "~1.6",
        "source": "Multpl.com / CurrentMarketValuation.com (manual update)",
        "description": (
            "S&P 500 market cap / aggregate revenues. Immune to earnings manipulation. "
            "Current 3.2–3.6x exceeds the prior 2000 peak of ~2.3x."
        ),
    },
]

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _classify_yield_curve(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < -0.5:
        return "warning", f"{val:+.2f}% — deeply inverted (strong recession signal)"
    if val < 0:
        return "caution", f"{val:+.2f}% — inverted (post-inversion phase)"
    if val < 0.5:
        return "neutral", f"{val:+.2f}% — flat (post-inversion normalising)"
    if val < 1.5:
        return "positive", f"{val:+.2f}% — normal"
    return "positive", f"{val:+.2f}% — steep (early-cycle signal)"


def _classify_vix(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < 13:
        return "caution", f"{val:.1f} — extreme complacency (elevated future vol risk)"
    if val < 20:
        return "neutral", f"{val:.1f} — normal range"
    if val < 30:
        return "caution", f"{val:.1f} — elevated concern"
    if val < 40:
        return "warning", f"{val:.1f} — fear"
    return "warning", f"{val:.1f} — panic / crisis level"


def _vix_deployment_signal(val: float | None, date: str | None) -> dict:
    """Return a structured deployment signal based on pre-committed VIX thresholds."""
    if val is None:
        return {
            "value": None, "date": date,
            "level": "unknown", "label": "No Data",
            "color": "neutral", "deploy_pct": 0,
            "action": "No VIX data available",
            "note": "",
        }
    if val < 15:
        level, label, color, deploy_pct = "low", "Complacency", "caution", 0
        action = "Normal DCA only — no extra lump-sum deployment"
        note = "Low VIX is a warning sign, not a buy signal. Markets are priced for perfection."
    elif val < 20:
        level, label, color, deploy_pct = "normal", "Normal", "neutral", 0
        action = "Normal DCA only"
        note = "No special action. Continue regular investment schedule."
    elif val < 30:
        level, label, color, deploy_pct = "elevated", "Elevated Anxiety", "caution", 37
        action = "Deploy 25–50% of opportunity cash reserve"
        note = "Elevated fear. Historically a reasonable entry window for patient capital."
    elif val < 40:
        level, label, color, deploy_pct = "high", "Fear", "warning", 62
        action = "Deploy 50–75% of opportunity cash reserve"
        note = "Significant fear. VIX spikes at this level show statistically positive 3M/1Y returns."
    else:
        level, label, color, deploy_pct = "panic", "Panic / Crisis", "danger", 100
        action = "Deploy all remaining opportunity cash reserve"
        note = "Rare panic zone (2008, 2020, 2022). Maximum historical opportunity for patient capital."
    return {
        "value": val, "date": date,
        "level": level, "label": label,
        "color": color, "deploy_pct": deploy_pct,
        "action": action, "note": note,
    }


def _classify_hy_spread(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < 300:
        return "caution", f"{val:.0f} bps — near 25-year tights; late-cycle risk-on"
    if val < 450:
        return "neutral", f"{val:.0f} bps — below long-run avg (~490 bps)"
    if val < 600:
        return "neutral", f"{val:.0f} bps — near historical average"
    if val < 900:
        return "caution", f"{val:.0f} bps — elevated; growth concerns priced"
    return "warning", f"{val:.0f} bps — stress / recession fears"


def _classify_real_yield(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < -1.0:
        return "caution", f"{val:.2f}% — deeply negative (distorted; unsustainable)"
    if val < 0:
        return "positive", f"{val:.2f}% — slightly negative (accommodative)"
    if val < 1.0:
        return "neutral", f"{val:.2f}% — low positive"
    if val < 2.0:
        return "caution", f"{val:.2f}% — restrictive; headwind for high multiples"
    return "warning", f"{val:.2f}% — significantly restrictive"


def _classify_sahm(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val >= 0.5:
        return "warning", f"{val:.2f} — TRIGGERED (recession signal)"
    if val >= 0.3:
        return "caution", f"{val:.2f} — rising; monitor closely"
    return "positive", f"{val:.2f} — below trigger threshold (no recession signal)"


def _classify_pmi(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val >= 55:
        return "positive", f"{val:.1f} — strong expansion"
    if val >= 50:
        return "positive", f"{val:.1f} — expanding"
    if val >= 45:
        return "caution", f"{val:.1f} — contracting (mild)"
    return "warning", f"{val:.1f} — contracting (significant)"


def _classify_core_cpi_yoy(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < 2.0:
        return "positive", f"{val:.1f}% YoY — at/below target"
    if val < 2.5:
        return "neutral", f"{val:.1f}% YoY — near target"
    if val < 4.0:
        return "caution", f"{val:.1f}% YoY — above target"
    return "warning", f"{val:.1f}% YoY — significantly above target"


def _classify_stlfsi(val: float | None) -> tuple[str, str]:
    """St. Louis Fed Financial Stress Index: 0 = normal, >1 = stress, <-1 = very easy."""
    if val is None:
        return "neutral", "No data"
    if val > 1.5:
        return "warning", f"{val:+.2f} — high financial stress"
    if val > 0.5:
        return "caution", f"{val:+.2f} — elevated financial stress"
    if val > -0.5:
        return "neutral", f"{val:+.2f} — normal conditions"
    if val > -1.5:
        return "positive", f"{val:+.2f} — easy financial conditions (low stress)"
    return "caution", f"{val:+.2f} — very easy conditions (complacency risk)"


def _classify_nfci(val: float | None) -> tuple[str, str]:
    """Chicago Fed National Financial Conditions Index: 0 = historical avg, negative = loose."""
    if val is None:
        return "neutral", "No data"
    if val > 0.5:
        return "warning", f"{val:+.2f} — tight financial conditions"
    if val > 0.1:
        return "caution", f"{val:+.2f} — slightly tight"
    if val > -0.3:
        return "neutral", f"{val:+.2f} — near-average conditions"
    return "positive", f"{val:+.2f} — loose financial conditions"


def _classify_baa_spread(val_bps: float | None) -> tuple[str, str]:
    """Moody's BAA − 10Y Treasury spread (in bps). Long-run avg ~170 bps."""
    if val_bps is None:
        return "neutral", "No data"
    if val_bps < 120:
        return "caution", f"{val_bps:.0f} bps — below long-run avg; late-cycle tight"
    if val_bps < 200:
        return "neutral", f"{val_bps:.0f} bps — near long-run average (~170 bps)"
    if val_bps < 300:
        return "caution", f"{val_bps:.0f} bps — moderately elevated"
    return "warning", f"{val_bps:.0f} bps — elevated; credit stress signals"


def _classify_fwd_inflation(val: float | None) -> tuple[str, str]:
    """5Y5Y forward inflation expectation: 2.0–2.5% = anchored near target."""
    if val is None:
        return "neutral", "No data"
    if val > 3.0:
        return "warning", f"{val:.2f}% — unanchored; well above Fed target"
    if val > 2.5:
        return "caution", f"{val:.2f}% — slightly above target; watch for de-anchoring"
    if val > 1.8:
        return "positive", f"{val:.2f}% — anchored near 2% target"
    return "caution", f"{val:.2f}% — below target; disinflation / deflation risk"


def _classify_cfnai(val: float | None) -> tuple[str, str]:
    """Chicago Fed National Activity Index: 0 = trend, >0 = above trend, <0 = below trend."""
    if val is None:
        return "neutral", "No data"
    if val >= 0.70:
        return "positive", f"{val:+.2f} — strong above-trend growth (inflationary pressure possible)"
    if val >= 0.10:
        return "positive", f"{val:+.2f} — above-trend growth"
    if val >= -0.10:
        return "neutral", f"{val:+.2f} — near-trend growth"
    if val >= -0.70:
        return "caution", f"{val:+.2f} — below-trend growth; slow but not recessionary"
    return "warning", f"{val:+.2f} — recessionary conditions signalled"


def _classify_static_valuation(ind: dict) -> str:
    """Return status string for static valuation indicators."""
    vid = ind["id"]
    val = ind["value"]
    if vid == "cape":
        if val > 38:
            return "danger"
        if val > 30:
            return "warning"
        if val > 22:
            return "caution"
        return "positive"
    if vid == "forward_pe":
        if val > 22:
            return "warning"
        if val > 19:
            return "caution"
        return "neutral"
    if vid == "buffett_indicator":
        if val > 175:
            return "danger"
        if val > 135:
            return "warning"
        if val > 100:
            return "caution"
        return "positive"
    if vid == "tobins_q":
        if val > 1.8:
            return "danger"
        if val > 1.3:
            return "warning"
        if val > 1.0:
            return "caution"
        return "positive"
    if vid == "price_sales":
        if val > 3.0:
            return "danger"
        if val > 2.2:
            return "warning"
        if val > 1.8:
            return "caution"
        return "positive"
    return "neutral"


# ---------------------------------------------------------------------------
# ITS (Inflation Trend Signal) and PMI trend helpers
# ---------------------------------------------------------------------------

def _compute_its(connection: sqlite3.Connection, api_key: str) -> tuple[str, float | None, float | None]:
    """
    Inflation Trend Signal using Core PCE (Fed's actual target).
    Compares 3-month annualised Core PCE with 12-month Core PCE trend.
    Returns (signal, ann_3m, ann_12m) where signal is:
    'accelerating' | 'decelerating' | 'stable' | 'unknown'
    """
    vals = get_recent_values(connection, S["CORE_PCE"], api_key, n=15, ttl=TTL_MONTHLY)
    if len(vals) < 13:
        return "unknown", None, None
    pce_now = vals[0]
    pce_3m_ago = vals[3]
    pce_12m_ago = vals[12]
    if pce_3m_ago <= 0 or pce_12m_ago <= 0:
        return "unknown", None, None
    ann_3m = round(((pce_now / pce_3m_ago) ** 4 - 1) * 100, 2)
    ann_12m = round((pce_now / pce_12m_ago - 1) * 100, 2)
    diff = ann_3m - ann_12m
    if diff > 0.3:
        return "accelerating", ann_3m, ann_12m
    if diff < -0.3:
        return "decelerating", ann_3m, ann_12m
    return "stable", ann_3m, ann_12m


def _compute_cfnai_trend(connection: sqlite3.Connection, api_key: str) -> str:
    """Returns 'rising' | 'falling' | 'stable' based on CFNAI momentum."""
    vals = get_recent_values(connection, S["CFNAI"], api_key, n=6, ttl=TTL_MONTHLY)
    if len(vals) < 4:
        return "stable"
    latest = vals[0]
    avg_3m = sum(vals[1:4]) / 3
    if latest - avg_3m > 0.15:
        return "rising"
    if avg_3m - latest > 0.15:
        return "falling"
    return "stable"


def _cfnai_ma3(connection: sqlite3.Connection, api_key: str) -> float | None:
    """Current CFNAI-MA3 — the Chicago Fed's own smoothed statistic (average of
    the 3 most recent monthly readings), used for the recession threshold and
    regime split instead of the noisy raw monthly index."""
    vals = get_recent_values(connection, S["CFNAI"], api_key, n=3, ttl=TTL_MONTHLY)
    if len(vals) < 3:
        return None
    return sum(vals) / 3


def _classify_regime(cfnai_ma3: float | None, its: str) -> dict:
    """Map CFNAI + ITS to one of the four FactSet regimes.

    Takes the CFNAI 3-MONTH AVERAGE, not the raw monthly index — the Chicago Fed's
    own convention (their -0.70 recession threshold is explicitly defined for the
    MA3, not the noisy single-month reading). Confirmed bug (fixed 2026-08-10): a
    raw monthly reading of -0.02 — essentially zero, "near-trend growth" by the
    Fed's own classification — was flipping `expanding` to False purely because
    it was a hair below zero, mislabeling trend growth as "the economy contracting"
    in the regime description. The MA3 smooths out exactly this single-month noise."""
    if cfnai_ma3 is None or its == "unknown":
        return {
            "id": "unknown",
            "label": "Unknown",
            "color": "neutral",
            "description": "Insufficient data to classify the current cycle phase.",
            "implication": "—",
        }
    expanding = cfnai_ma3 > 0
    if expanding and its == "decelerating":
        return {
            "id": "growing",
            "label": "Growing",
            "color": "positive",
            "description": "Economy expanding with inflation decelerating.",
            "implication": (
                "Historically the most favourable regime for equities broadly. "
                "Cyclicals and growth tend to lead. Monitor for a transition to Heating."
            ),
        }
    if expanding and its in ("accelerating", "stable"):
        return {
            "id": "heating",
            "label": "Heating",
            "color": "caution",
            "description": "Economy expanding but inflation accelerating.",
            "implication": (
                "Late-cycle dynamics. Real assets (energy, materials, real estate) "
                "tend to outperform. Watch for policy tightening that could tip into Slowing."
            ),
        }
    if not expanding and its in ("decelerating", "stable"):
        return {
            "id": "slowing",
            "label": "Slowing",
            "color": "warning",
            "description": "Economy contracting with inflation declining.",
            "implication": (
                "Typical recession / slowdown environment. Defensives (healthcare, "
                "staples, utilities) historically hold better. Credit spreads "
                "typically widen. Watch for stabilisation as the leading indicator."
            ),
        }
    # PMI < 50 and ITS accelerating → Stagflation
    return {
        "id": "stagflation",
        "label": "Stagflation",
        "color": "danger",
        "description": "Economy contracting while inflation rises.",
        "implication": (
            "Most difficult regime. Both equities and bonds struggle simultaneously. "
            "Hard assets and short-duration instruments historically provide the "
            "most relative protection."
        ),
    }


def _recession_risk(
    yield_curve: float | None,
    sahm: float | None,
    hy_spread_bps: float | None,
    cfnai_ma3: float | None,
) -> dict:
    score = 0
    factors = []
    if yield_curve is not None:
        if yield_curve < 0:
            score += 2
            factors.append("Yield curve inverted (post-inversion phase)")
        elif yield_curve < 0.5:
            score += 1
            factors.append("Yield curve flat")
    if sahm is not None:
        if sahm >= 0.5:
            score += 3
            factors.append("Sahm Rule triggered")
        elif sahm >= 0.3:
            score += 1
            factors.append("Sahm Rule rising")
    if hy_spread_bps is not None and hy_spread_bps > 600:
        score += 2
        factors.append("HY spreads elevated")
    # CFNAI-MA3, per the Chicago Fed's own -0.70 threshold definition — NOT the
    # raw monthly reading, which is noisy enough to false-trigger on ordinary
    # single-month wobble. Fixed 2026-08-10.
    if cfnai_ma3 is not None and cfnai_ma3 < -0.70:
        score += 2
        factors.append("CFNAI-MA3 below -0.70 (recessionary signal)")
    elif cfnai_ma3 is not None and cfnai_ma3 < -0.30:
        score += 1
        factors.append("CFNAI-MA3 below -0.30 (below-trend growth)")

    if score >= 5:
        level, label = "high", "High"
    elif score >= 3:
        level, label = "elevated", "Elevated"
    elif score >= 1:
        level, label = "moderate", "Moderate"
    else:
        level, label = "low", "Low"

    return {"level": level, "label": label, "score": score, "factors": factors}


# ---------------------------------------------------------------------------
# Top-level public functions
# ---------------------------------------------------------------------------

def get_cycle_diagnosis(connection: sqlite3.Connection, fred_key: str) -> dict:
    """Return the full cycle diagnosis payload for the /api/macro endpoint."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # --- Fetch all live indicators ---
    yc_val, yc_date       = get_latest(connection, S["YIELD_CURVE"],   fred_key)
    rv_val, rv_date       = get_latest(connection, S["REAL_YIELD"],    fred_key)
    vix_val, vix_date     = get_latest(connection, S["VIX"],           fred_key)
    # FRED credit spread series are in % — multiply ×100 to convert to bps
    _hy_raw, hy_date      = get_latest(connection, S["HY_SPREAD"],     fred_key)
    hy_val                = round(_hy_raw * 100, 1) if _hy_raw is not None else None
    _ig_raw, ig_date      = get_latest(connection, S["IG_SPREAD"],     fred_key)
    ig_val                = round(_ig_raw * 100, 1) if _ig_raw is not None else None
    _baa_raw, baa_date    = get_latest(connection, S["BAA_SPREAD"],    fred_key)
    baa_val               = round(_baa_raw * 100, 1) if _baa_raw is not None else None
    stlfsi_val, stlfsi_date = get_latest(connection, S["STLFSI"],      fred_key)
    nfci_val, nfci_date   = get_latest(connection, S["NFCI"],          fred_key)
    sahm_val, sahm_date   = get_latest(connection, S["SAHM"],          fred_key, ttl=TTL_MONTHLY)
    cfnai_val, cfnai_date = get_latest(connection, S["CFNAI"],         fred_key, ttl=TTL_MONTHLY)
    t10_val, t10_date     = get_latest(connection, S["T10Y"],          fred_key)
    t2_val, t2_date       = get_latest(connection, S["T2Y"],           fred_key)
    fed_val, fed_date     = get_latest(connection, S["FED_RATE"],      fred_key)
    unemp_val, unemp_date = get_latest(connection, S["UNEMPLOYMENT"],  fred_key, ttl=TTL_MONTHLY)
    bkeven_10y_val, bkeven_10y_date = get_latest(connection, S["BREAKEVEN_10Y"], fred_key)
    fwd_inf_val, fwd_inf_date = get_latest(connection, S["FWD_INFLATION"], fred_key)

    # Core PCE YoY (Fed's actual inflation target)
    from .fred_client import get_series as _get_series
    pce_vals = get_recent_values(connection, S["CORE_PCE"], fred_key, n=15, ttl=TTL_MONTHLY)
    core_pce_yoy: float | None = None
    core_pce_3m: float | None = None
    core_pce_date: str | None = None
    if len(pce_vals) >= 13:
        core_pce_yoy = round((pce_vals[0] / pce_vals[12] - 1) * 100, 2)
        core_pce_3m  = round(((pce_vals[0] / pce_vals[3]) ** 4 - 1) * 100, 2)
        pce_obs = _get_series(connection, S["CORE_PCE"], fred_key, limit=2, ttl=TTL_MONTHLY)
        core_pce_date = pce_obs[0]["date"] if pce_obs else None

    # Core CPI YoY (for display)
    cpi_vals = get_recent_values(connection, S["CORE_CPI"], fred_key, n=14, ttl=TTL_MONTHLY)
    core_cpi_yoy: float | None = None
    core_cpi_date: str | None = None
    if len(cpi_vals) >= 13:
        core_cpi_yoy = round((cpi_vals[0] / cpi_vals[12] - 1) * 100, 2)
        cpi_obs = _get_series(connection, S["CORE_CPI"], fred_key, limit=2, ttl=TTL_MONTHLY)
        core_cpi_date = cpi_obs[0]["date"] if cpi_obs else None

    # Cycle regime inputs — ITS now uses Core PCE
    its, its_3m, its_12m  = _compute_its(connection, fred_key)
    cfnai_trend           = _compute_cfnai_trend(connection, fred_key)
    cfnai_ma3             = _cfnai_ma3(connection, fred_key)
    regime                = _classify_regime(cfnai_ma3, its)
    recession             = _recession_risk(yc_val, sahm_val, hy_val, cfnai_ma3)

    # --- Build indicator list ---
    def ind(id, label, tier, value, date, unit, status, reading, description, source="FRED"):
        return {
            "id": id, "label": label, "tier": tier, "value": value,
            "date": date, "unit": unit, "status": status,
            "reading": reading, "description": description,
            "source": source, "is_live": True,
        }

    yc_status, yc_reading         = _classify_yield_curve(yc_val)
    vix_status, vix_reading       = _classify_vix(vix_val)
    hy_status, hy_reading         = _classify_hy_spread(hy_val)
    rv_status, rv_reading         = _classify_real_yield(rv_val)
    sahm_status, sahm_reading     = _classify_sahm(sahm_val)
    cfnai_status, cfnai_reading   = _classify_cfnai(cfnai_val)
    stlfsi_status, stlfsi_reading = _classify_stlfsi(stlfsi_val)
    nfci_status, nfci_reading     = _classify_nfci(nfci_val)
    baa_status, baa_reading       = _classify_baa_spread(baa_val)
    fwdinf_status, fwdinf_reading = _classify_fwd_inflation(fwd_inf_val)

    # Core PCE classification (same thresholds as CPI but Fed's actual target)
    pce_status, pce_reading = _classify_core_cpi_yoy(core_pce_yoy)
    cpi_status, cpi_reading = _classify_core_cpi_yoy(core_cpi_yoy)

    # IG spread (already in bps)
    ig_status, ig_reading = "neutral", (f"{ig_val:.0f} bps" if ig_val is not None else "No data")
    if ig_val is not None:
        if ig_val < 80:
            ig_status, ig_reading = "caution", f"{ig_val:.0f} bps — near historic tights"
        elif ig_val < 130:
            ig_status, ig_reading = "neutral", f"{ig_val:.0f} bps — normal range"
        else:
            ig_status, ig_reading = "caution", f"{ig_val:.0f} bps — elevated"

    # 10Y breakeven
    bk_status, bk_reading = "neutral", (f"{bkeven_10y_val:.2f}% — 10Y market-implied inflation" if bkeven_10y_val else "No data")
    if bkeven_10y_val is not None:
        if bkeven_10y_val > 3.0:
            bk_status, bk_reading = "warning", f"{bkeven_10y_val:.2f}% — well above Fed target"
        elif bkeven_10y_val > 2.5:
            bk_status, bk_reading = "caution", f"{bkeven_10y_val:.2f}% — above-target expectations"
        elif bkeven_10y_val < 1.5:
            bk_status, bk_reading = "caution", f"{bkeven_10y_val:.2f}% — deflation concern"

    its_label = f"ITS: {its.title()} (Core PCE 3M ann. {its_3m:.1f}% vs 12M {its_12m:.1f}%)" if its_3m is not None else f"ITS: {its.title()}"

    # MECE split (2026-08-02 session): Cycle Diagnosis = growth/inflation/policy regime
    # only. Real Yield -> Valuation (competing-asset yield); VIX/STLFSI/NFCI -> Sentiment
    # (stress/fear composites, not regime-classification inputs). Credit spreads (HY/IG/
    # BAA) stay here because recession_risk already uses HY spread as a regime factor.
    short_cycle = [
        ind("yield_curve",  "Yield Curve (10Y−2Y)",          "signal",  yc_val,        yc_date,        "%",   yc_status,     yc_reading,     "10Y minus 2Y Treasury spread. Inversion has preceded every US recession since the 1950s."),
        ind("hy_spread",    "HY Credit Spread (OAS)",         "signal",  hy_val,        hy_date,        "bps", hy_status,     hy_reading,     "ICE BofA high-yield OAS. Tight spreads signal late-cycle risk-on; widening signals incoming stress."),
        ind("sahm_rule",    "Sahm Rule Indicator",            "signal",  sahm_val,      sahm_date,      "",    sahm_status,   sahm_reading,   "Rise of 0.5pp in 3M avg unemployment above 12M low. Has triggered at start of every recent US recession."),
        ind("cfnai",        "CFNAI (Activity Index)",         "signal",  cfnai_val,     cfnai_date,     "",    cfnai_status,  cfnai_reading + (f" — trend: {cfnai_trend}" if cfnai_val is not None else ""), "Chicago Fed composite of 85 monthly indicators. Above 0 = above-trend; below −0.70 = recessionary."),
        ind("core_pce",     "Core PCE (YoY)",                 "signal",  core_pce_yoy,  core_pce_date,  "%",   pce_status,    f"{pce_reading} | 3M ann. {core_pce_3m:.1f}%" if core_pce_3m else pce_reading, "Core PCE is the Fed's actual 2% inflation target. 3M annualised shows momentum; ITS is based on this series."),
        ind("baa_spread",   "Moody's BAA Spread",             "context", baa_val,       baa_date,       "bps", baa_status,    baa_reading,    "BAA corporate bond yield minus 10Y Treasury. Long history back to 1919. Long-run average ~170 bps."),
        ind("core_cpi",     "Core CPI (YoY)",                 "context", core_cpi_yoy,  core_cpi_date,  "%",   cpi_status,    cpi_reading,    "CPI ex-food and energy. Runs ~0.3–0.5pp above Core PCE structurally. Widely reported in media."),
        ind("ig_spread",    "IG Credit Spread (OAS)",         "context", ig_val,        ig_date,        "bps", ig_status,     ig_reading,     "ICE BofA investment-grade OAS. Reflects systemic funding conditions and corporate credit quality."),
        ind("fed_rate",     "Fed Funds Rate",                 "context", fed_val,       fed_date,       "%",   "neutral",     f"{fed_val:.2f}%" if fed_val else "No data",     "Effective fed funds rate (daily). Above ~2.5% neutral rate = restrictive policy stance."),
        ind("unemployment", "Unemployment Rate",              "context", unemp_val,     unemp_date,     "%",   "neutral",     f"{unemp_val:.1f}%" if unemp_val else "No data",  "Lagging indicator. Sahm Rule uses rate-of-change rather than absolute level."),
        ind("t10y",         "10Y Treasury Yield",             "context", t10_val,       t10_date,       "%",   "neutral",     f"{t10_val:.2f}%" if t10_val else "No data",     "Benchmark long-term rate. Rising rates compress P/E multiples and compete with equity yields."),
    ]

    long_cycle = [
        {
            "id": "its_signal",
            "label": "Inflation Trend Signal (ITS)",
            "tier": "signal",
            "value": its_3m,
            "date": core_pce_date,
            "unit": "%",
            "status": "caution" if its == "accelerating" else ("positive" if its == "decelerating" else "neutral"),
            "reading": its_label,
            "description": "FactSet regime signal: compares 3M annualised Core PCE to 12M trend. Drives Growing/Heating/Slowing/Stagflation regime classification.",
            "source": "FRED PCEPILFE (calculated)",
            "is_live": True,
        },
        {
            "id": "fwd_inflation",
            "label": "5Y5Y Forward Inflation",
            "tier": "signal",
            "value": fwd_inf_val,
            "date": fwd_inf_date,
            "unit": "%",
            "status": fwdinf_status,
            "reading": fwdinf_reading,
            "description": "Market-implied inflation rate for years 5–10. The Fed's anchor: if this drifts above 2.5% persistently, long-run expectations are at risk of de-anchoring.",
            "source": "FRED T5YIFR",
            "is_live": True,
        },
        {
            "id": "breakeven_10y",
            "label": "10Y Breakeven Inflation",
            "tier": "context",
            "value": bkeven_10y_val,
            "date": bkeven_10y_date,
            "unit": "%",
            "status": bk_status,
            "reading": bk_reading,
            "description": "10Y TIPS breakeven = nominal 10Y minus real 10Y yield. Reflects bond market's aggregate 10-year inflation forecast.",
            "source": "FRED T10YIE",
            "is_live": True,
        },
        {
            "id": "cfnai_trend",
            "label": "CFNAI Growth Trend",
            "tier": "context",
            "value": cfnai_val,
            "date": cfnai_date,
            "unit": "",
            "status": "positive" if cfnai_trend == "rising" else ("warning" if cfnai_trend == "falling" else "neutral"),
            "reading": f"CFNAI {cfnai_val:+.2f} — trend {cfnai_trend}" if cfnai_val is not None else "No data",
            "description": "Momentum of CFNAI over 3 months vs prior 3 months. Trend is more predictive than level for regime turning points.",
            "source": "FRED CFNAI (calculated)",
            "is_live": True,
        },
    ]
    # NOTE (2026-08-02 MECE split): static valuation multiples (CAPE, Fwd P/E, Buffett
    # Indicator, Tobin's Q, P/S) used to be duplicated here AND in get_valuation_snapshot.
    # They now live solely in the Market Valuation tab (/api/valuation) — not appended here.

    return {
        "regime": regime,
        "recession_risk": recession,
        "its": its,
        "cfnai_trend": cfnai_trend,
        "short_cycle": short_cycle,
        "long_cycle": long_cycle,
        "vix_signal": _vix_deployment_signal(vix_val, vix_date),
        "as_of": now_str,
        "fred_live": bool(fred_key),
    }


def get_valuation_snapshot(connection: sqlite3.Connection, fred_key: str) -> dict:
    """Return the S&P 500 valuation dashboard payload for /api/valuation."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Live indicators from FRED
    yc_val, yc_date   = get_latest(connection, S["YIELD_CURVE"], fred_key)
    rv_val, rv_date   = get_latest(connection, S["REAL_YIELD"],  fred_key)
    vix_val, vix_date = get_latest(connection, S["VIX"],         fred_key)
    _hy_raw, hy_date  = get_latest(connection, S["HY_SPREAD"],   fred_key)
    hy_val            = round(_hy_raw * 100, 1) if _hy_raw is not None else None
    stlfsi_val, stlfsi_date = get_latest(connection, S["STLFSI"], fred_key)
    fwd_inf_val, fwd_inf_date = get_latest(connection, S["FWD_INFLATION"], fred_key)
    t10_val, t10_date = get_latest(connection, S["T10Y"], fred_key)

    # Core PCE YoY
    from .fred_client import get_series as _get_series
    pce_vals = get_recent_values(connection, S["CORE_PCE"], fred_key, n=15, ttl=TTL_MONTHLY)
    core_pce_yoy: float | None = None
    core_pce_3m: float | None = None
    core_pce_date: str | None = None
    if len(pce_vals) >= 13:
        core_pce_yoy = round((pce_vals[0] / pce_vals[12] - 1) * 100, 2)
        core_pce_3m  = round(((pce_vals[0] / pce_vals[3]) ** 4 - 1) * 100, 2)
        pce_obs = _get_series(connection, S["CORE_PCE"], fred_key, limit=2, ttl=TTL_MONTHLY)
        core_pce_date = pce_obs[0]["date"] if pce_obs else None

    # Build live valuation indicators. MECE split (2026-08-02): yield curve -> Cycle
    # (recession lead, feeds recession_risk); HY spread/STLFSI/core PCE/5Y5Y inflation ->
    # Cycle/Sentiment; VIX -> Sentiment. Only the competing-asset-yield / risk-premium
    # measures and the static multiples stay here.
    live_indicators = []

    rv_s, rv_r = _classify_real_yield(rv_val)
    live_indicators.append({
        "id": "real_yield", "label": "Real Yield (10Y TIPS)",
        "value": rv_val, "unit": "%", "date": rv_date,
        "status": rv_s, "reading": rv_r, "tier": "signal", "is_live": True, "source": "FRED",
        "description": "Inflation-adjusted risk-free rate. The key competing asset to equities — high real yields compress equity risk premiums.",
        "percentile": None,
    })

    # ERP proxy: CAPE earnings yield minus 10Y nominal. CAPE is a manually-updated
    # static value (see STATIC_VALUATIONS) while the 10Y yield is fetched live —
    # this mixes two different vintages into one number. Previously the "date"
    # field showed only the live yield's date, implying the whole figure was
    # current when the CAPE half could be months old (Codex audit finding).
    # Fixed 2026-08-10: show both dates explicitly.
    cape_entry = next((s for s in STATIC_VALUATIONS if s["id"] == "cape"), None)
    cape_static = cape_entry["value"] if cape_entry else 42.39
    cape_as_of = cape_entry["as_of"] if cape_entry else None
    cape_ey = round(1 / cape_static * 100, 2)
    erp_proxy = round(cape_ey - (t10_val or 4.25), 2) if t10_val else None
    erp_status = "warning" if erp_proxy is not None and erp_proxy < 0.5 else (
        "caution" if erp_proxy is not None and erp_proxy < 1.5 else "neutral"
    )
    live_indicators.append({
        "id": "erp_proxy", "label": "CAPE Earnings Yield − 10Y",
        "value": erp_proxy, "unit": "%",
        "date": f"CAPE {cape_as_of or '—'} · 10Y {t10_date or '—'}",
        "status": erp_status,
        "reading": f"{erp_proxy:.2f}% (1/CAPE {cape_ey:.2f}% as of {cape_as_of} minus 10Y {t10_val:.2f}% as of {t10_date})" if erp_proxy is not None and t10_val else "No data",
        "tier": "signal", "is_live": True, "source": "Calculated",
        "description": "Equity risk premium proxy: CAPE earnings yield minus the risk-free rate. Negative or near-zero ERP means bonds are competitive with stocks.",
        "percentile": None,
    })

    # Add static valuation indicators
    static_indicators = []
    for sv in STATIC_VALUATIONS:
        status = _classify_static_valuation(sv)
        static_indicators.append({
            "id": sv["id"], "label": sv["label"],
            "value": sv["value"], "unit": sv["unit"], "date": sv["as_of"],
            "status": status,
            "reading": f"{sv['value']}{sv['unit']}",
            "tier": "signal", "is_live": False, "source": sv["source"],
            "description": sv["description"],
            "percentile": sv["percentile"],
            "long_run_avg": sv["long_run_avg"],
        })

    # Overall signal: driven by static valuations
    danger_count = sum(1 for i in static_indicators if i["status"] in ("danger", "warning"))
    if danger_count >= 3:
        overall = "extreme"
        overall_label = "Extreme Overvaluation"
        overall_description = (
            "Multiple long-horizon indicators (CAPE, Tobin's Q, Buffett Indicator) "
            "are simultaneously at or near historical extremes. Institutional forecasters "
            "project below-average returns over the next 7–10 years."
        )
    elif danger_count >= 1:
        overall = "expensive"
        overall_label = "Expensive"
        overall_description = (
            "Price-based valuation metrics are elevated relative to history. "
            "Rate-adjusted metrics (ERP) appear closer to fair value. "
            "The market requires strong earnings delivery to justify current multiples."
        )
    else:
        overall = "fair"
        overall_label = "Fairly Valued"
        overall_description = "Valuation indicators are broadly within historical normal ranges."

    return {
        "overall": overall,
        "overall_label": overall_label,
        "overall_description": overall_description,
        "live_indicators": live_indicators,
        "static_indicators": static_indicators,
        "as_of": now_str,
        "fred_live": bool(fred_key),
    }


def get_sentiment_snapshot(connection: sqlite3.Connection, fred_key: str) -> dict:
    """Market Sentiment tab: how fearful/greedy is positioning right now — the third
    MECE lens, distinct from Cycle (regime) and Valuation (price level). VIX/STLFSI/NFCI
    are stress-and-fear composites, not regime-classification inputs, so they live here
    exclusively (moved out of Cycle Diagnosis and Market Valuation, 2026-08-02)."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    vix_val, vix_date = get_latest(connection, S["VIX"], fred_key)
    stlfsi_val, stlfsi_date = get_latest(connection, S["STLFSI"], fred_key)
    nfci_val, nfci_date = get_latest(connection, S["NFCI"], fred_key)

    vix_s, vix_r = _classify_vix(vix_val)
    stlfsi_s, stlfsi_r = _classify_stlfsi(stlfsi_val)
    nfci_s, nfci_r = _classify_nfci(nfci_val)

    indicators = [
        {"id": "vix", "label": "VIX (Implied Volatility)", "value": vix_val, "unit": "",
         "date": vix_date, "status": vix_s, "reading": vix_r, "tier": "signal", "is_live": True, "source": "FRED",
         "description": "30-day S&P 500 implied volatility. Low = complacency; high = fear. The fastest-moving sentiment gauge."},
        {"id": "stlfsi", "label": "Financial Stress (STLFSI)", "value": stlfsi_val, "unit": "",
         "date": stlfsi_date, "status": stlfsi_s, "reading": stlfsi_r, "tier": "signal", "is_live": True, "source": "FRED STLFSI4",
         "description": "St. Louis Fed weekly composite of 18 financial stress indicators. Zero = historical average stress; very negative = complacency."},
        {"id": "nfci", "label": "Natl Financial Conditions (NFCI)", "value": nfci_val, "unit": "",
         "date": nfci_date, "status": nfci_s, "reading": nfci_r, "tier": "context", "is_live": True, "source": "FRED",
         "description": "Chicago Fed weekly index of 105 indicators covering money markets, debt, equity, and traditional/shadow banking conditions."},
    ]

    # overall read: reuse the VIX ladder's zone since VIX is the highest-frequency,
    # most-understood single sentiment gauge; STLFSI/NFCI are corroborating context.
    danger_map = {"danger": "extreme_fear", "warning": "fear", "caution": "complacency", "neutral": "normal"}
    overall = danger_map.get(vix_s, "normal")
    overall_label = {"extreme_fear": "Extreme Fear", "fear": "Fear", "complacency": "Complacency", "normal": "Normal"}[overall]

    return {
        "overall": overall, "overall_label": overall_label,
        "indicators": indicators,
        "as_of": now_str,
        "fred_live": bool(fred_key),
    }


_FG_ZONES = [
    (20, "Extreme Fear", "#16a34a"),
    (40, "Fear", "#65a30d"),
    (60, "Neutral", "#a16207"),
    (80, "Greed", "#ea580c"),
    (101, "Extreme Greed", "#dc2626"),
]


def _fg_zone(v: float) -> tuple[str, str]:
    for hi, name, color in _FG_ZONES:
        if v < hi:
            return name, color
    return "Extreme Greed", "#dc2626"


def _fg_bucket(metric: float, thresholds: tuple[float, float, float, float]) -> tuple[str, int]:
    """thresholds = (t1,t2,t3,t4) ascending; metric >= t4 is the greediest bucket."""
    t1, t2, t3, t4 = thresholds
    if metric >= t4:
        return "extreme_greed", 90
    if metric >= t3:
        return "greed", 70
    if metric >= t2:
        return "neutral", 50
    if metric >= t1:
        return "fear", 30
    return "extreme_fear", 10


def get_us_fear_greed(connection: sqlite3.Connection, fred_key: str) -> dict:
    """A US Fear & Greed composite, patterned after CNN's well-known 7-leg index and
    after this product's own A-share Fear & Greed (cn_fear.py) — but NOT the same
    methodology as either, and that difference matters:

    - Only 5 of CNN's 7 legs are built. Options put/call ratio and NYSE advance/
      decline VOLUME breadth both need data this product doesn't have (CBOE options
      data, NYSE breadth feeds) — they're skipped, not faked with a weak proxy.
    - Unlike cn_fear.py, where every leg is a rolling 5-YEAR PERCENTILE (needing years
      of stored history), this is a single-day read: each leg is bucketed against a
      fixed, documented threshold on today's value only. No time series is stored yet,
      so there is no percentile, no trend, and no history chart — a coarser read than
      the CN gauge, on purpose rather than by accident.
    """
    if not fred_key:
        return {"status": "error", "error": "No FRED API key — set FRED_API_KEY to compute this."}

    vix_obs = get_series(connection, S["VIX"], fred_key, limit=60, ttl=TTL_DAILY)
    hy_obs  = get_series(connection, S["HY_SPREAD"], fred_key, limit=30, ttl=TTL_DAILY)
    sp_obs  = get_series(connection, S["SP500"], fred_key, limit=260, ttl=TTL_DAILY)
    t10_obs = get_series(connection, S["T10Y"], fred_key, limit=30, ttl=TTL_DAILY)
    if not (vix_obs and hy_obs and sp_obs and t10_obs):
        return {"status": "error", "error": "Insufficient FRED history to compute the composite yet — try again shortly."}

    vix = [float(o["value"]) for o in vix_obs]
    hy  = [float(o["value"]) for o in hy_obs]
    sp  = [float(o["value"]) for o in sp_obs]
    t10 = [float(o["value"]) for o in t10_obs]

    legs = []

    # 1. Volatility — VIX vs its own trailing 50-day average. Below average = calmer
    #    than usual = greed; well above = fear. Mirrors CNN's Market Volatility leg.
    vix_window = vix[:50]
    vix_ma = sum(vix_window) / len(vix_window)
    pct_dev = (vix_ma - vix[0]) / vix_ma * 100 if vix_ma else 0.0
    zone, score = _fg_bucket(pct_dev, (-25, -10, 10, 25))
    legs.append({"key": "volatility", "label": "Volatility (VIX vs 50d avg)", "value": score, "zone": zone,
                 "raw": f"VIX {vix[0]:.1f} vs 50d avg {vix_ma:.1f} ({pct_dev:+.0f}%)",
                 "desc": "CBOE VIX vs its own trailing 50-day average. Below average = calmer than usual = greed; well above = fear."})

    # 2. Credit stress — 21-session change in the HY OAS spread, in bps. Widening
    #    (investors demanding more compensation for junk-bond risk) = fear. Mirrors
    #    CNN's Junk Bond Demand leg.
    hy_21 = hy[min(20, len(hy) - 1)]
    delta_bps = (hy[0] - hy_21) * 100
    zone, score = _fg_bucket(-delta_bps, (-40, -10, 10, 40))
    legs.append({"key": "credit", "label": "Credit stress (HY spread, 21d Δ)", "value": score, "zone": zone,
                 "raw": f"{delta_bps:+.0f}bps over 21 sessions",
                 "desc": "21-session change in the ICE BofA high-yield OAS spread. Widening = fear; narrowing = greed."})

    # 3. Price strength — S&P 500 vs its own 252-day (52-week) high. Same formula
    #    as cn_fear.py's price_strength leg, applied to SP500 instead of CSI300.
    sp_window = sp[:252]
    sp_high = max(sp_window)
    pct_from_high = (sp[0] - sp_high) / sp_high * 100 if sp_high else 0.0
    zone, score = _fg_bucket(pct_from_high, (-20, -10, -5, -2))
    legs.append({"key": "price_strength", "label": "Price strength (vs 52w high)", "value": score, "zone": zone,
                 "raw": f"{pct_from_high:+.1f}% from the 52-week high",
                 "desc": "S&P 500 close vs its own 252-day high. Near the high = confidence/greed; well below = fear."})

    # 4. Momentum — S&P 500 vs its own 125-day mean. Same formula as cn_fear.py's
    #    momentum leg.
    sp_ma_window = sp[:125]
    sp_ma = sum(sp_ma_window) / len(sp_ma_window)
    pct_above = (sp[0] - sp_ma) / sp_ma * 100 if sp_ma else 0.0
    zone, score = _fg_bucket(pct_above, (-10, -3, 3, 10))
    legs.append({"key": "momentum", "label": "Momentum (vs 125d avg)", "value": score, "zone": zone,
                 "raw": f"{pct_above:+.1f}% above the 125-day average",
                 "desc": "S&P 500 close vs its own 125-day (~6-month) average. Above & rising = greed; below = fear."})

    # 5. Safe-haven demand — stocks vs Treasury yields over 20 sessions. Stocks up
    #    while yields also rise (no rush to safety) = greed; stocks down while yields
    #    fall too (flight to Treasuries) = fear. The bond-yield term is a rough,
    #    explicitly-scaled proxy for bond price moves, not an actual bond return.
    sp_20 = sp[min(20, len(sp) - 1)]
    stock_20d_ret = (sp[0] / sp_20 - 1) * 100 if sp_20 else 0.0
    t10_20 = t10[min(20, len(t10) - 1)]
    yield_chg_bps = (t10[0] - t10_20) * 100
    combined = stock_20d_ret - yield_chg_bps / 10       # crude common-scale proxy, not a real bond return
    zone, score = _fg_bucket(combined, (-6, -2, 2, 6))
    legs.append({"key": "safe_haven", "label": "Safe-haven demand (stocks vs yields, 20d)", "value": score, "zone": zone,
                 "raw": f"stocks {stock_20d_ret:+.1f}%, 10Y yield {yield_chg_bps:+.0f}bps over 20 sessions",
                 "desc": "S&P 500's 20-session return vs the 10-year Treasury yield's 20-session change. Stocks falling while yields also fall (flight to Treasuries) = fear; both rising together = greed."})

    composite = round(sum(l["value"] for l in legs) / len(legs))
    zone_label, color = _fg_zone(composite)   # same thresholds drive both the zone label AND the read text below

    if zone_label == "Extreme Fear":
        read = "Extreme fear — by CNN's original framework this leans toward opportunity, but this is a coarse single-day read with no historical validation on THIS index yet (unlike the validated CN gauge). Treat as a rough cross-check, not a standalone signal."
    elif zone_label == "Fear":
        read = "Fear — several legs are reading stressed. Same caveat: no historical backtest of this specific 5-leg composite exists yet."
    elif zone_label == "Greed":
        read = "Greed — positioning looks stretched across most legs. Same caveat: no historical backtest of this specific 5-leg composite exists yet."
    elif zone_label == "Extreme Greed":
        read = "Extreme greed — positioning looks very stretched across most legs. Same caveat: no historical backtest of this specific 5-leg composite exists yet."
    else:
        read = "Neutral — no strong read from either direction across the 5 legs."

    return {
        "status": "ready",
        "date": vix_obs[0]["date"],
        "value": composite, "zone": zone_label, "color": color,
        "legs": legs, "read": read,
        "methodology_note": "5-leg composite (of CNN's standard 7 — options put/call ratio and NYSE advance/decline volume breadth aren't available from this product's data sources) using fixed thresholds on today's value, not a rolling percentile like the A-share gauge. No history is stored yet.",
    }

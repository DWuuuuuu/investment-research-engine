"""
Industry Cycle Assessment Module.

Assesses:
  1. Industry Lifecycle Phase (Introduction / Growth / Maturity / Decline)
     using quarterly yfinance data: revenue growth, gross margin trend,
     CapEx/Depreciation ratio, and Rule of 40 for tech.

  2. Business Cycle Context — Fidelity sector rotation framework
     (Early / Mid / Late / Recession) per GICS sector.

  3. Sector-Specific Leading Indicators — the key external signals
     to monitor for each sector (FRED series, SEMI.org, etc.).

Called by screener.py; result is attached as `cycle` key to the screener
response and consumed by the frontend renderCycleSection() function.
"""
from __future__ import annotations

import math
from typing import Any


def _s(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return val


# ─────────────────────────────────────────────────────────────────────────────
# GICS sector → Fidelity business cycle performance map
# Sources: Fidelity Business Cycle research (1962–2024)
# ─────────────────────────────────────────────────────────────────────────────

SECTOR_CYCLE_MAP: dict[str, dict] = {
    "Technology": {
        "best_phases":  ["Early Cycle", "Mid Cycle"],
        "avoid_phases": ["Late Cycle", "Recession"],
        "cycle_note": (
            "IT and Semiconductors historically lead in Early/Mid-cycle expansion. "
            "Sensitive to rate rises (real yield) and earnings deceleration in Late cycle."
        ),
        "phase_rank": {"early": "strong", "mid": "strong", "late": "below", "recession": "avoid"},
    },
    "Consumer Discretionary": {
        "best_phases":  ["Early Cycle"],
        "avoid_phases": ["Recession"],
        "cycle_note": (
            "Consumer Discretionary has outperformed in every Early-cycle since 1962 — "
            "the most consistent sector rotation signal in the Fidelity dataset."
        ),
        "phase_rank": {"early": "strong", "mid": "neutral", "late": "below", "recession": "avoid"},
    },
    "Financials": {
        "best_phases":  ["Early Cycle"],
        "avoid_phases": ["Recession"],
        "cycle_note": (
            "Financials benefit from steepening yield curve (NIM expansion) and "
            "recovering credit in Early cycle. Avoid as credit deteriorates in recession."
        ),
        "phase_rank": {"early": "strong", "mid": "neutral", "late": "below", "recession": "avoid"},
    },
    "Industrials": {
        "best_phases":  ["Early Cycle", "Mid Cycle"],
        "avoid_phases": ["Recession"],
        "cycle_note": (
            "Industrials align with capex recovery in Early/Mid cycle. "
            "Monitor ISM PMI (>55 = aggressive buy) and capacity utilization (FRED: TCU)."
        ),
        "phase_rank": {"early": "above", "mid": "strong", "late": "neutral", "recession": "avoid"},
    },
    "Real Estate": {
        "best_phases":  ["Early Cycle"],
        "avoid_phases": ["Late Cycle", "Recession"],
        "cycle_note": (
            "REITs benefit from rate cuts and transaction volume recovery in Early cycle. "
            "Late-cycle rising rates compress cap rate spreads and REIT multiples."
        ),
        "phase_rank": {"early": "strong", "mid": "neutral", "late": "below", "recession": "avoid"},
    },
    "Energy": {
        "best_phases":  ["Late Cycle"],
        "avoid_phases": ["Recession"],
        "cycle_note": (
            "Energy historically outperforms in Late-cycle inflation phases. "
            "Monitor Baker Hughes rig count and EIA refinery utilization."
        ),
        "phase_rank": {"early": "below", "mid": "neutral", "late": "strong", "recession": "avoid"},
    },
    "Materials": {
        "best_phases":  ["Mid Cycle", "Late Cycle"],
        "avoid_phases": ["Recession"],
        "cycle_note": (
            "Materials benefit from commodity price inflation in Mid/Late cycle. "
            "China credit impulse leads global commodity demand by 6–12 months."
        ),
        "phase_rank": {"early": "neutral", "mid": "above", "late": "strong", "recession": "avoid"},
    },
    "Utilities": {
        "best_phases":  ["Late Cycle", "Recession"],
        "avoid_phases": ["Early Cycle"],
        "cycle_note": (
            "Utilities are defensive — inelastic demand and bond-proxy characteristics. "
            "Outperform during slowdowns and recessions but lag in early-cycle recoveries. "
            "New secular driver: AI data center electricity demand (monitor EIA load data)."
        ),
        "phase_rank": {"early": "avoid", "mid": "below", "late": "above", "recession": "strong"},
    },
    "Consumer Staples": {
        "best_phases":  ["Late Cycle", "Recession"],
        "avoid_phases": ["Early Cycle"],
        "cycle_note": (
            "Staples are defensive — pricing power and inelastic demand. "
            "Classic recession outperformer. Trade-down thesis activates when consumer "
            "sentiment (UMCSENT) falls to historical lows."
        ),
        "phase_rank": {"early": "below", "mid": "neutral", "late": "above", "recession": "strong"},
    },
    "Health Care": {
        "best_phases":  ["Late Cycle", "Recession"],
        "avoid_phases": ["Early Cycle"],
        "cycle_note": (
            "Healthcare is defensive with demand inelasticity. "
            "Monitor FDA PDUFA calendar for binary catalysts and patent cliff exposure "
            "($173.9B in branded drug revenue at risk 2026–2032)."
        ),
        "phase_rank": {"early": "below", "mid": "neutral", "late": "above", "recession": "strong"},
    },
    "Communication Services": {
        "best_phases":  ["Early Cycle", "Mid Cycle"],
        "avoid_phases": ["Recession"],
        "cycle_note": (
            "Communication Services (Meta, Alphabet, Netflix) behave more like Technology "
            "than traditional telecom. Digital ad spend growth is the key leading indicator."
        ),
        "phase_rank": {"early": "above", "mid": "strong", "late": "neutral", "recession": "below"},
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Sector-specific leading indicators
# ─────────────────────────────────────────────────────────────────────────────

SECTOR_INDICATORS: dict[str, list[dict]] = {
    "Technology": [
        {
            "name": "SEMI Book-to-Bill Ratio",
            "description": (
                "Monthly ratio of orders to billings for North American semiconductor equipment. "
                "12–18 month leading indicator for semiconductor equipment revenue."
            ),
            "source": "SEMI.org (monthly, free)",
            "thresholds": "> 1.10 = upcycle  |  0.90–1.10 = balanced  |  < 0.90 = downcycle",
        },
        {
            "name": "Hyperscaler CapEx (MSFT / GOOG / AMZN / META)",
            "description": (
                "AI infrastructure capex by the four largest hyperscalers. "
                "Rising YoY signals semiconductor and infrastructure revenue 12–18 months forward. "
                "The most actionable forward indicator for the current AI cycle."
            ),
            "source": "Company quarterly earnings releases",
            "thresholds": "Rising YoY = semiconductor upcycle incoming  |  Decelerating = risk signal",
        },
        {
            "name": "Wafer Fab Utilization",
            "description": (
                "Percentage of capacity in use at semiconductor fabs. "
                "High utilization = tight supply and pricing power; low = overcapacity and margin pressure."
            ),
            "source": "SEMI.org / company reports (quarterly)",
            "thresholds": "> 90% = pricing power  |  75–90% = balanced  |  < 60% = overcapacity",
        },
        {
            "name": "Semiconductor Inventory Days",
            "description": "Inventory held at distributors and OEMs. Rising inventory = revenue headwind 1–2 quarters out.",
            "source": "Company earnings / distributor channel checks",
            "thresholds": "< 10 weeks = tight  |  10–15 weeks = normal  |  > 15 weeks = oversupply risk",
        },
    ],
    "Industrials": [
        {
            "name": "ISM Manufacturing PMI",
            "description": (
                "Purchasing Managers' Index. >50 = expanding orders; <50 = contracting. "
                "Most widely tracked leading indicator for industrial activity."
            ),
            "source": "ISM (ismworld.org, monthly, free)  ·  FRED: MNFCTRMPCIMSA",
            "thresholds": "> 55 = strong expansion  |  50–55 = moderate  |  < 45 = significant contraction",
        },
        {
            "name": "Capacity Utilization (FRED: TCU)",
            "description": (
                "% of industrial capacity in use. "
                "Above ~82% = inflationary pressure and capex incentive. "
                "Below 75% = overcapacity (no rational capex incentive)."
            ),
            "source": "FRED series TCU (monthly)",
            "thresholds": "> 82% = tight / capex incentive  |  75–82% = normal  |  < 75% = overcapacity",
        },
        {
            "name": "CapEx / Depreciation Ratio",
            "description": (
                "Ratio above 1.5× = companies investing for growth; "
                "below 1.0× = harvesting existing assets. "
                "Computed from the company's own cash flow statement."
            ),
            "source": "Company cash flow statement (computed above)",
            "thresholds": "> 1.5× = growth cycle  |  1.0–1.5× = maintenance  |  < 1.0× = disinvestment",
        },
    ],
    "Financials": [
        {
            "name": "Yield Curve (10Y−2Y)  ·  FRED: T10Y2Y",
            "description": (
                "Steepening yield curve = NIM (net interest margin) expansion = buy regional banks. "
                "Flat or inverted = margin compression for deposit-funded lenders."
            ),
            "source": "FRED series T10Y2Y (daily, free)",
            "thresholds": "> +1.5% = strong NIM tailwind  |  0–+1.5% = neutral  |  Inverted = compression",
        },
        {
            "name": "Credit Card Delinquency Rate  ·  FRED: DRCCLACBS",
            "description": (
                "Rising delinquencies = late credit cycle; consumers are stretched. "
                "Watch for turn — stabilization after a spike precedes credit recovery."
            ),
            "source": "FRED series DRCCLACBS (quarterly)",
            "thresholds": "Rising trend = late credit cycle  |  Stable / falling = early-mid cycle",
        },
        {
            "name": "Commercial & Industrial Loan Growth  ·  FRED: TOTLL",
            "description": (
                "Accelerating C&I loan growth = companies borrowing to invest and expand. "
                "Early-cycle signal for bank earnings and credit formation."
            ),
            "source": "FRED series TOTLL (weekly Federal Reserve H.8 release)",
            "thresholds": "Accelerating YoY = early credit cycle  |  Contracting = late / post-cycle",
        },
    ],
    "Energy": [
        {
            "name": "Baker Hughes Rig Count",
            "description": (
                "US active oil/gas drilling rigs. Leads production by 6–12 months. "
                "Post-2020 efficiency gains have weakened the relationship with total production "
                "but the rig count still leads upstream services revenue."
            ),
            "source": "Baker Hughes (bakerhughes.com, weekly, free)",
            "thresholds": "Rising = upstream expansion  |  Declining = capex contraction for drillers",
        },
        {
            "name": "EIA Refinery Utilization",
            "description": (
                "% of US refinery capacity in use. "
                ">90% = strong demand and wide crack spreads (bullish for refiners)."
            ),
            "source": "EIA.gov (weekly, free)",
            "thresholds": "> 90% = strong demand + wide cracks  |  80–90% = normal  |  < 80% = weakness",
        },
        {
            "name": "WTI Crude — Spot vs Forward Curve",
            "description": (
                "Backwardation (spot > futures) = tight physical market = bullish for producers. "
                "Contango = oversupply and storage builds."
            ),
            "source": "EIA.gov / FRED: DCOILWTICO",
            "thresholds": "Backwardation = tight supply (bullish)  |  Contango = oversupply (bearish)",
        },
    ],
    "Real Estate": [
        {
            "name": "Cap Rate vs 10Y Treasury Spread",
            "description": (
                "Core real estate return premium over the risk-free rate. "
                "Key intrinsic value signal for REITs. "
                "Narrow spread = REITs expensive relative to bonds."
            ),
            "source": "NCREIF / Green Street Advisors (quarterly)",
            "thresholds": "> 3.5% = undervalued  |  1.5–3.5% = fair  |  < 1% = expensive vs bonds",
        },
        {
            "name": "30Y Mortgage Rate  ·  FRED: MORTGAGE30US",
            "description": (
                "Falling rates = transaction volume recovery and REIT multiple expansion. "
                "Rising rates rapidly = transaction freeze (2022–2024 pattern confirmed this)."
            ),
            "source": "FRED series MORTGAGE30US (weekly)",
            "thresholds": "Falling = REIT recovery signal  |  Rising rapidly = transaction freeze",
        },
        {
            "name": "Commercial / Residential Vacancy Rate Trend",
            "description": "Falling vacancy = expansion phase. Rising = over-supply or demand slowdown.",
            "source": "CBRE / CoStar (commercial)  ·  US Census (residential, quarterly)",
            "thresholds": "Falling vacancy = expansion  |  Flat = balance  |  Rising = hyper-supply risk",
        },
    ],
    "Consumer Discretionary": [
        {
            "name": "Michigan Consumer Sentiment  ·  FRED: UMCSENT",
            "description": (
                "Near historical lows as of May 2026 — potential contrarian signal. "
                "Consumer Discretionary leads in Early-cycle recoveries historically. "
                "Watch for a sentiment floor and turn."
            ),
            "source": "FRED series UMCSENT (monthly)",
            "thresholds": "Near lows & stabilizing = early-cycle buy signal  |  Still falling = caution",
        },
        {
            "name": "Retail Sales Ex-Auto  ·  FRED: RSXFS",
            "description": "Core consumer spending proxy. Accelerating = consumer cycle expansion.",
            "source": "FRED series RSXFS (monthly)",
            "thresholds": "Accelerating MoM = expansion  |  Decelerating two months in a row = caution",
        },
    ],
    "Consumer Staples": [
        {
            "name": "Michigan Consumer Sentiment  ·  FRED: UMCSENT",
            "description": (
                "Low sentiment triggers trade-down behavior (branded → private label, "
                "restaurant → grocery). Favors Staples vs. Discretionary."
            ),
            "source": "FRED series UMCSENT (monthly)",
            "thresholds": "Low & falling = trade-down thesis active  |  Rising from lows = rotate to Discretionary",
        },
        {
            "name": "PPI Food & Beverages  ·  FRED: WPU0222",
            "description": (
                "Input cost pressure for food/beverage staples. "
                "Rising PPI = margin headwind unless company has pricing power."
            ),
            "source": "FRED series WPU0222 (monthly)",
            "thresholds": "Rising = margin pressure (unless pricing power)  |  Falling = margin tailwind",
        },
    ],
    "Health Care": [
        {
            "name": "FDA PDUFA Calendar",
            "description": (
                "Prescription Drug User Fee Act approval dates. "
                "Binary risk/reward events for pharma and biotech companies. "
                "Check company-specific PDUFA dates before holding into approval."
            ),
            "source": "FDA.gov (free, continuously updated)",
            "thresholds": "Track company-specific dates; binary catalyst = concentrated position risk",
        },
        {
            "name": "Patent Cliff Exposure (2026–2032)",
            "description": (
                "$173.9B in branded drug revenue loses exclusivity 2026–2032. "
                "Keytruda (~$25B/yr) is the single largest cliff. "
                "Assess pipeline coverage ratio before holding pharma beyond 2026."
            ),
            "source": "IQVIA reports  ·  Company 10-K filings",
            "thresholds": "High cliff + thin pipeline = near-term revenue risk  |  Pipeline coverage = mitigant",
        },
        {
            "name": "Phase 2/3 Clinical Pipeline Density",
            "description": (
                "Count of Phase 2/3 trials per therapeutic area indicates innovation momentum "
                "and future revenue optionality. "
                "High density = pipeline coverage for cliff; low = risk unmitigated."
            ),
            "source": "ClinicalTrials.gov (free, searchable by sponsor)",
            "thresholds": "High Phase 2/3 density = strong pipeline  |  Low = patent cliff unmitigated",
        },
    ],
    "Communication Services": [
        {
            "name": "Digital Ad Spend Growth",
            "description": (
                "Leads revenue for Meta and Alphabet by 1–2 quarters. "
                "Highly correlated with GDP growth and consumer confidence. "
                "IAB annual forecast is the most widely cited industry benchmark."
            ),
            "source": "IAB annual reports  ·  Company quarterly earnings",
            "thresholds": "Accelerating YoY = tailwind  |  Decelerating = caution for ad-dependent names",
        },
        {
            "name": "Platform Subscriber / User Growth (MAU / DAU)",
            "description": (
                "Net adds and engagement metrics for streaming and platform businesses. "
                "Decelerating subscriber growth at Netflix / Spotify / Disney+ signals lifecycle maturity."
            ),
            "source": "Company earnings releases (quarterly)",
            "thresholds": "Accelerating = growth phase  |  Decelerating = lifecycle maturity signal",
        },
    ],
    "Materials": [
        {
            "name": "Global Manufacturing PMI (J.P. Morgan)",
            "description": "Composite global PMI. Leads materials demand across mining, chemicals, and specialty materials.",
            "source": "S&P Global / ISM (monthly)",
            "thresholds": "> 53 = strong demand  |  50–53 = moderate  |  < 48 = downcycle for materials",
        },
        {
            "name": "China Credit Impulse",
            "description": (
                "China's credit growth leads global commodity demand by 6–12 months. "
                "The single most reliable leading indicator for broad materials cycles globally."
            ),
            "source": "PBoC data  ·  Bloomberg  ·  MacroMicro.me",
            "thresholds": "Rising = commodity demand impulse incoming  |  Falling = demand headwind",
        },
    ],
    "Utilities": [
        {
            "name": "10Y Treasury Yield  ·  FRED: DGS10",
            "description": (
                "Utilities are bond proxies — their dividends compete with Treasury yields. "
                "Rising rates = multiple compression headwind; falling rates = relative outperformance."
            ),
            "source": "FRED series DGS10 (daily, free)",
            "thresholds": "Falling = relative outperformance  |  Rising rapidly = multiple compression",
        },
        {
            "name": "US Electricity Load / Demand Growth (EIA)",
            "description": (
                "AI data center electricity demand is a new secular driver for the utility sector — "
                "potentially the largest demand increase since industrial electrification. "
                "Monitor EIA Short-Term Energy Outlook monthly."
            ),
            "source": "EIA Short-Term Energy Outlook (monthly, free at eia.gov)",
            "thresholds": "Secular upswing in AI-driven load = structural positive for regulated utilities",
        },
    ],
}

DEFAULT_INDICATORS: list[dict] = [
    {
        "name": "CFNAI (Broad Economic Activity)  ·  FRED: CFNAI",
        "description": (
            "Chicago Fed composite of 85 monthly indicators across production, "
            "employment, consumption, and housing. Zero = trend; below −0.70 = recessionary."
        ),
        "source": "FRED series CFNAI (monthly)",
        "thresholds": "> +0.10 = above-trend  |  −0.10 to +0.10 = near-trend  |  < −0.70 = recessionary",
    },
    {
        "name": "ISM Services PMI",
        "description": "Composite services sector activity. >50 = expanding. Covers ~75% of US GDP.",
        "source": "ISM (ismworld.org, monthly)",
        "thresholds": "> 55 = strong expansion  |  50–55 = moderate  |  < 50 = contraction",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Quarterly financial data helpers
# ─────────────────────────────────────────────────────────────────────────────

def _quarterly_vals(t: Any, *names: str) -> list[float]:
    """Return up to 4 most-recent quarterly values for a financial line item."""
    try:
        qf = t.quarterly_financials
        if qf is None or qf.empty:
            return []
        row_name = next((n for n in names if n in qf.index), None)
        if row_name is None:
            return []
        out: list[float] = []
        for v in list(qf.loc[row_name])[:4]:
            try:
                f = float(v)
                if not math.isnan(f):
                    out.append(f)
            except (TypeError, ValueError):
                pass
        return out
    except Exception:
        return []


def _quarterly_cf_vals(t: Any, *names: str) -> list[float]:
    """Return up to 4 most-recent quarterly cash-flow values (absolute)."""
    try:
        qcf = t.quarterly_cashflow
        if qcf is None or qcf.empty:
            return []
        row_name = next((n for n in names if n in qcf.index), None)
        if row_name is None:
            return []
        out: list[float] = []
        for v in list(qcf.loc[row_name])[:4]:
            try:
                f = abs(float(v))
                if not math.isnan(f):
                    out.append(f)
            except (TypeError, ValueError):
                pass
        return out
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle phase assessment
# ─────────────────────────────────────────────────────────────────────────────

def _assess_lifecycle(t: Any, info: dict, sector: str, industry: str) -> dict:
    """Compute lifecycle phase and quantitative signals from yfinance data."""

    rev_growth_ttm: float | None = _s(info.get("revenueGrowth"))
    if rev_growth_ttm is not None:
        rev_growth_ttm = round(rev_growth_ttm * 100, 1)

    gross_margin_now: float | None = _s(info.get("grossMargins"))
    if gross_margin_now is not None:
        gross_margin_now = round(gross_margin_now * 100, 1)

    # ── Revenue trend (quarterly) ────────────────────────────────────────────
    q_rev = _quarterly_vals(t, "Total Revenue")
    rev_trend, rev_trend_note = "unknown", ""
    if len(q_rev) >= 4:
        recent = (q_rev[0] + q_rev[1]) / 2
        prior  = (q_rev[2] + q_rev[3]) / 2
        if prior > 0:
            delta = (recent - prior) / prior * 100
            if delta > 3:
                rev_trend      = "accelerating"
                rev_trend_note = f"+{delta:.1f}% vs prior 2Q avg"
            elif delta < -3:
                rev_trend      = "decelerating"
                rev_trend_note = f"{delta:.1f}% vs prior 2Q avg"
            else:
                rev_trend      = "stable"
                rev_trend_note = f"{delta:+.1f}% vs prior 2Q avg"

    # ── Gross margin trend (quarterly) ──────────────────────────────────────
    q_gp = _quarterly_vals(t, "Gross Profit")
    q_rv = _quarterly_vals(t, "Total Revenue")
    margin_trend, margin_trend_note = "unknown", ""
    if len(q_gp) >= 4 and len(q_rv) >= 4:
        margins = [gp / rv * 100 for gp, rv in zip(q_gp[:4], q_rv[:4]) if rv > 0]
        if len(margins) >= 3:
            recent_m = sum(margins[:2]) / 2
            prior_m  = sum(margins[2:]) / 2
            delta    = recent_m - prior_m
            if delta > 1.0:
                margin_trend      = "expanding"
                margin_trend_note = f"+{delta:.1f}pp vs prior 2Q"
            elif delta < -1.0:
                margin_trend      = "compressing"
                margin_trend_note = f"{delta:.1f}pp vs prior 2Q"
            else:
                margin_trend      = "stable"
                margin_trend_note = f"{delta:+.1f}pp vs prior 2Q"

    # ── CapEx / Depreciation ─────────────────────────────────────────────────
    q_capex = _quarterly_cf_vals(t, "Capital Expenditure")
    q_dep   = _quarterly_cf_vals(
        t,
        "Depreciation And Amortization",
        "Depreciation Depletion And Amortization",
        "Reconciled Depreciation",
    )
    capex_dep_ratio: float | None = None
    capex_dep_note = ""
    if q_capex and q_dep:
        ttm_capex = sum(q_capex[:4])
        ttm_dep   = sum(q_dep[:4])
        if ttm_dep > 0:
            capex_dep_ratio = round(ttm_capex / ttm_dep, 2)
            if capex_dep_ratio > 1.5:
                capex_dep_note = f"{capex_dep_ratio}× — investing for growth"
            elif capex_dep_ratio > 1.0:
                capex_dep_note = f"{capex_dep_ratio}× — maintenance + modest growth"
            else:
                capex_dep_note = f"{capex_dep_ratio}× — harvesting (below replacement)"

    # ── Rule of 40 for Tech/SaaS ─────────────────────────────────────────────
    rule40: float | None = None
    rule40_note = ""
    is_tech = any(
        x in (sector + " " + industry).lower()
        for x in ["technology", "software", "internet", "semiconductor", "communication services"]
    )
    if is_tech and rev_growth_ttm is not None:
        fcf_raw = _s(info.get("freeCashflow"))
        rev_ttm = _s(info.get("totalRevenue"))
        if fcf_raw is not None and rev_ttm and rev_ttm > 0:
            fcf_m  = fcf_raw / rev_ttm * 100
            rule40 = round(rev_growth_ttm + fcf_m, 1)
            if rule40 > 60:
                rule40_note = "Early / high-growth"
            elif rule40 > 40:
                rule40_note = "Healthy mid-growth"
            elif rule40 > 20:
                rule40_note = "Late growth / early maturity"
            else:
                rule40_note = "Maturity or structural issue (median SaaS ~12)"

    # ── Phase determination ──────────────────────────────────────────────────
    rg = rev_growth_ttm if rev_growth_ttm is not None else 0.0
    if rev_growth_ttm is None:
        phase, phase_color = "Unknown", "neutral"
        phase_note  = "Revenue growth data not available — assess manually."
        max_pos     = "—"
        entry_note  = "Assess manually using alternative data sources"
    elif rg >= 15:
        phase, phase_color = "Growth", "positive"
        phase_note  = f"Revenue growth {rg:.0f}% — classic growth-phase territory."
        max_pos     = "5–8%"
        entry_note  = "Full position at mid-growth confirmation; monitor for deceleration"
    elif rg >= 8:
        phase, phase_color = "Growth", "positive"
        phase_note  = f"Revenue growth {rg:.0f}% — mid-growth phase."
        max_pos     = "5–8%"
        entry_note  = "Full position; set review trigger if growth dips below 5%"
    elif rg >= 3:
        if margin_trend == "compressing":
            phase, phase_color = "Late Growth / Early Maturity", "caution"
            phase_note  = "Revenue slowing with margin compression — transitioning to maturity."
            max_pos     = "3–5%"
            entry_note  = "Reduce unless deep-value catalyst or durable moat confirmed"
        else:
            phase, phase_color = "Maturity", "caution"
            phase_note  = f"Revenue growth {rg:.0f}% (GDP-level) — mature industry."
            max_pos     = "8–10% if ROIC >2× WACC and CAP credibly 10+ years"
            entry_note  = "Full position only for proven compounders with durable moat"
    elif rg >= 0:
        phase, phase_color = "Late Maturity", "warning"
        phase_note  = "Near-zero revenue growth — approaching potential structural decline."
        max_pos     = "2–3% (reduce if margins compressing)"
        entry_note  = "Only on deep value, catalyst, or special situation thesis"
    else:
        phase, phase_color = "Decline", "warning"
        phase_note  = f"Negative revenue growth ({rg:.0f}%) — potential structural decline."
        max_pos     = "0% (avoid)"
        entry_note  = "Avoid unless compelling recovery thesis with specific catalyst"

    # ── Build signals list ───────────────────────────────────────────────────
    def _sig_status_margin(trend: str) -> str:
        return ("positive" if trend == "expanding"
                else "warning" if trend == "compressing"
                else "caution" if trend not in ("unknown",)
                else "neutral")

    signals: list[dict] = [
        {
            "name":       "Revenue Growth (TTM)",
            "value":      f"{rev_growth_ttm:.1f}%" if rev_growth_ttm is not None else "N/A",
            "trend":      rev_trend,
            "trend_note": rev_trend_note,
            "status":     ("positive" if rev_growth_ttm is not None and rev_growth_ttm >= 10
                           else "caution" if rev_growth_ttm is not None and rev_growth_ttm >= 0
                           else "warning" if rev_growth_ttm is not None
                           else "neutral"),
        },
        {
            "name":       "Gross Margin Trend",
            "value":      f"{gross_margin_now:.1f}%" if gross_margin_now is not None else "N/A",
            "trend":      margin_trend,
            "trend_note": margin_trend_note,
            "status":     _sig_status_margin(margin_trend),
        },
        {
            "name":       "CapEx / Depreciation",
            "value":      (f"{capex_dep_ratio}×" if capex_dep_ratio is not None else "N/A"),
            "trend":      ("growth"    if capex_dep_ratio is not None and capex_dep_ratio > 1.5
                           else "maintenance" if capex_dep_ratio is not None and capex_dep_ratio >= 1.0
                           else "harvesting"  if capex_dep_ratio is not None
                           else "unknown"),
            "trend_note": capex_dep_note,
            "status":     ("positive" if capex_dep_ratio is not None and capex_dep_ratio > 1.5
                           else "caution" if capex_dep_ratio is not None and capex_dep_ratio >= 1.0
                           else "warning" if capex_dep_ratio is not None and capex_dep_ratio < 1.0
                           else "neutral"),
        },
    ]

    if rule40 is not None:
        signals.append({
            "name":       "Rule of 40 (Tech/SaaS)",
            "value":      str(rule40),
            "trend":      ("strong"      if rule40 > 60
                           else "healthy"    if rule40 > 40
                           else "late_growth" if rule40 > 20
                           else "weak"),
            "trend_note": rule40_note,
            "status":     ("positive" if rule40 > 40
                           else "caution" if rule40 > 20
                           else "warning"),
        })

    return {
        "phase":        phase,
        "phase_color":  phase_color,
        "phase_note":   phase_note,
        "max_position": max_pos,
        "entry_note":   entry_note,
        "signals":      signals,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Business cycle context
# ─────────────────────────────────────────────────────────────────────────────

def _business_cycle_context(sector: str) -> dict:
    ctx = SECTOR_CYCLE_MAP.get(sector)
    caveat = (
        "Fidelity Business Cycle research (1962–present). Academic caveat: sector rotation "
        "produces no systematic alpha net of transaction costs (Molchanov & Stangl 2024). "
        "Use for position sizing and entry framing only — not for short-term trading."
    )
    if not ctx:
        return {
            "sector":       sector or "Unknown",
            "best_phases":  [],
            "avoid_phases": [],
            "cycle_note":   "No specific business cycle data for this sector.",
            "phase_rank":   {},
            "caveat":       caveat,
        }
    return {
        "sector":       sector,
        "best_phases":  ctx["best_phases"],
        "avoid_phases": ctx["avoid_phases"],
        "cycle_note":   ctx["cycle_note"],
        "phase_rank":   ctx["phase_rank"],
        "caveat":       caveat,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ETF category → GICS sector mapping
# ─────────────────────────────────────────────────────────────────────────────

def _etf_sector_from_category(category: str) -> str:
    cat = (category or "").lower()
    if any(x in cat for x in ["technology", "semiconductor", "computer"]):
        return "Technology"
    if any(x in cat for x in ["health", "biotech", "pharma", "medical"]):
        return "Health Care"
    if any(x in cat for x in ["energy", "oil", "gas", "mlp"]):
        return "Energy"
    if any(x in cat for x in ["financial", "bank", "insurance", "brokerage"]):
        return "Financials"
    if any(x in cat for x in ["real estate", "reit"]):
        return "Real Estate"
    if any(x in cat for x in ["consumer discretionary", "retail", "leisure"]):
        return "Consumer Discretionary"
    if any(x in cat for x in ["consumer staples", "food", "staple"]):
        return "Consumer Staples"
    if any(x in cat for x in ["industrial", "defense", "aerospace", "transport"]):
        return "Industrials"
    if any(x in cat for x in ["material", "mining", "commodity", "gold", "silver", "precious"]):
        return "Materials"
    if any(x in cat for x in ["utility", "utilities", "power", "electric", "water"]):
        return "Utilities"
    if any(x in cat for x in ["communication", "telecom", "media", "entertainment"]):
        return "Communication Services"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

# yfinance sector names → canonical GICS names used in SECTOR_CYCLE_MAP
_SECTOR_ALIASES: dict[str, str] = {
    "financial services": "Financials",
    "basic materials":    "Materials",
    "consumer cyclical":  "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "healthcare":         "Health Care",
    "real estate":        "Real Estate",
    "communication services": "Communication Services",
    # already canonical
    "technology":         "Technology",
    "industrials":        "Industrials",
    "energy":             "Energy",
    "utilities":          "Utilities",
    "financials":         "Financials",
}


def _normalize_sector(sector: str) -> str:
    """Normalise yfinance sector names to canonical GICS names."""
    return _SECTOR_ALIASES.get((sector or "").lower(), sector)


def assess_cycle(ticker_str: str, t: Any, info: dict) -> dict:
    """
    Return industry cycle assessment for any ticker (stock or ETF).

    Returns:
        sector, industry, is_etf,
        lifecycle  (None for ETFs — no single income statement),
        business_cycle (Fidelity framework per sector),
        sector_indicators (leading indicators list for the sector).
    """
    sector     = _s(info.get("sector"), "")
    industry   = _s(info.get("industry"), "")
    quote_type = (info.get("quoteType") or "").upper()
    is_etf     = quote_type in ("ETF", "MUTUALFUND")

    if is_etf:
        category = _s(info.get("category"), "")
        sector   = _etf_sector_from_category(category)
        industry = category
        lifecycle = None
    else:
        sector    = _normalize_sector(sector)
        lifecycle = _assess_lifecycle(t, info, sector, industry)

    return {
        "sector":            sector,
        "industry":          industry,
        "is_etf":            is_etf,
        "lifecycle":         lifecycle,
        "business_cycle":    _business_cycle_context(sector),
        "sector_indicators": SECTOR_INDICATORS.get(sector, DEFAULT_INDICATORS),
    }

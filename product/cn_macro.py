"""China macro / cycle diagnosis — mirrors macro_layer.py's get_cycle_diagnosis()
output shape (regime, recession_risk, short_cycle, long_cycle) so the existing
renderIndicatorCard() frontend component can render it without modification.

Data source: Tushare Pro macro-economy endpoints (CPI/PPI/PMI/GDP/M2/social
financing/SHIBOR/LPR) plus CSI 300 index valuation. No China VIX is published
under the available data tier, so there is no deployment-signal panel here.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from . import cn_data

CSI300 = "000300.SH"
_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_TTL = 3600  # macro series move slowly — cache for an hour


def _latest(rows: list[dict], date_key: str) -> dict | None:
    if not rows:
        return None
    return sorted(rows, key=lambda r: r.get(date_key, ""), reverse=True)[0]


def _yoy_series(rows: list[dict], date_key: str, val_key: str, n: int = 13) -> list[tuple[str, float]]:
    """Return up to n (date, value) pairs, newest first, with non-null values."""
    clean = [(r[date_key], r[val_key]) for r in rows if r.get(date_key) and r.get(val_key) is not None]
    clean.sort(key=lambda x: x[0], reverse=True)
    return clean[:n]


# ── Classification helpers (thresholds calibrated for China, not US) ────────

def _classify_pmi(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val >= 52:
        return "positive", f"{val:.1f} — solid expansion"
    if val >= 50:
        return "positive", f"{val:.1f} — expansion, just above 50"
    if val >= 49:
        return "caution", f"{val:.1f} — contraction, near the 50 line"
    if val >= 47:
        return "warning", f"{val:.1f} — contraction"
    return "danger", f"{val:.1f} — sharp contraction"


def _classify_cpi(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < -0.5:
        return "warning", f"{val:+.1f}% YoY — deflation, demand weakness"
    if val < 0.5:
        return "caution", f"{val:+.1f}% YoY — near-zero, deflation risk"
    if val <= 3.0:
        return "positive", f"{val:+.1f}% YoY — within PBoC's ~3% comfort zone"
    return "warning", f"{val:+.1f}% YoY — above target"


def _classify_ppi(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < -2.0:
        return "warning", f"{val:+.1f}% YoY — factory-gate deflation, margin pressure upstream"
    if val < 0:
        return "caution", f"{val:+.1f}% YoY — mild factory-gate deflation"
    return "positive", f"{val:+.1f}% YoY — producer prices rising"


def _classify_m2(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val >= 11:
        return "positive", f"{val:.1f}% YoY — loose liquidity, supportive of risk assets"
    if val >= 8:
        return "neutral", f"{val:.1f}% YoY — normal liquidity growth"
    return "caution", f"{val:.1f}% YoY — tightening liquidity"


def _classify_sf(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val >= 9:
        return "positive", f"{val:.1f}% YoY — credit growth accelerating"
    if val >= 7:
        return "neutral", f"{val:.1f}% YoY — steady credit growth"
    return "caution", f"{val:.1f}% YoY — credit growth slowing"


def _classify_lpr_real(lpr: float | None, cpi: float | None) -> tuple[str, str, float | None]:
    if lpr is None or cpi is None:
        return "neutral", "No data", None
    real = round(lpr - cpi, 2)
    if real > 3.5:
        return "warning", f"{real:+.2f}% — restrictive real policy rate", real
    if real > 1.5:
        return "caution", f"{real:+.2f}% — moderately restrictive", real
    return "positive", f"{real:+.2f}% — accommodative real policy rate", real


def _classify_shibor(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < 1.5:
        return "positive", f"{val:.2f}% — very loose interbank liquidity"
    if val < 2.5:
        return "neutral", f"{val:.2f}% — normal interbank liquidity"
    return "caution", f"{val:.2f}% — tightening interbank liquidity"


def _classify_csi300_pe(val: float | None) -> tuple[str, str]:
    if val is None:
        return "neutral", "No data"
    if val < 11:
        return "positive", f"{val:.1f}× — cheap vs CSI 300's own history"
    if val < 16:
        return "neutral", f"{val:.1f}× — fair value range"
    if val < 20:
        return "caution", f"{val:.1f}× — getting rich"
    return "warning", f"{val:.1f}× — expensive vs history"


# ── Regime + risk classification (PMI for growth, CPI/PPI for inflation) ────

def _classify_regime(pmi: float | None, cpi: float | None, ppi: float | None) -> dict:
    if pmi is None or cpi is None:
        return {
            "id": "unknown", "label": "Unknown", "color": "neutral",
            "description": "Insufficient data to classify the current cycle phase.",
            "implication": "—",
        }
    expanding = pmi >= 50
    # Use PPI as the inflation-trend proxy (China's headline CPI is too close to
    # zero to reliably signal direction quarter to quarter)
    heating = (ppi is not None and ppi > 0) or cpi > 1.5

    if expanding and not heating:
        return {
            "id": "growing", "label": "Growing", "color": "positive",
            "description": "PMI in expansion with inflation still subdued.",
            "implication": "The most favourable regime for A-share risk assets. Watch for PPI turning positive as the next signal of overheating.",
        }
    if expanding and heating:
        return {
            "id": "heating", "label": "Heating", "color": "caution",
            "description": "PMI in expansion with producer prices turning up.",
            "implication": "Late-cycle dynamics. Upstream cyclicals (materials, energy) tend to lead. Watch for policy tightening (LPR hikes, SF deceleration).",
        }
    if not expanding and not heating:
        return {
            "id": "slowing", "label": "Slowing", "color": "warning",
            "description": "PMI in contraction with deflationary or near-zero inflation.",
            "implication": "China's most persistent recent regime — demand weakness without pricing power. Defensives and high-dividend SOEs historically hold up better. Watch social financing for stimulus response.",
        }
    return {
        "id": "stagflation", "label": "Stagflation", "color": "danger",
        "description": "PMI in contraction while producer prices rise.",
        "implication": "Rare for China but the most difficult regime when it occurs. Hard assets and pricing-power names provide the most relative protection.",
    }


def _growth_risk(pmi: float | None, sf_yoy: float | None, ppi: float | None) -> dict:
    score = 0
    factors = []
    if pmi is not None:
        if pmi < 49:
            score += 2
            factors.append("Manufacturing PMI below 49")
        elif pmi < 50:
            score += 1
            factors.append("Manufacturing PMI below the 50 expansion line")
    if sf_yoy is not None:
        if sf_yoy < 7:
            score += 2
            factors.append("Social financing growth below 7% YoY")
        elif sf_yoy < 9:
            score += 1
            factors.append("Social financing growth decelerating")
    if ppi is not None and ppi < -2:
        score += 2
        factors.append("PPI deflation deeper than -2% YoY")
    elif ppi is not None and ppi < 0:
        score += 1
        factors.append("PPI in deflation")

    if score >= 5:
        level, label = "high", "High"
    elif score >= 3:
        level, label = "elevated", "Elevated"
    elif score >= 1:
        level, label = "moderate", "Moderate"
    else:
        level, label = "low", "Low"
    return {"level": level, "label": label, "score": score, "factors": factors, "max_score": 6}


def _ind(id_, label, tier, value, date, unit, status, reading, description, source="Tushare") -> dict:
    return {
        "id": id_, "label": label, "tier": tier, "value": value,
        "date": date, "unit": unit, "status": status,
        "reading": reading, "description": description,
        "source": source, "is_live": True,
    }


def get_cn_cycle_diagnosis(force: bool = False) -> dict:
    now = time.time()
    if not force and _CACHE["data"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["data"]

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    live = bool(cn_data._token())

    pmi_val = pmi_date = cpi_val = cpi_date = ppi_val = ppi_date = None
    m2_val = m2_date = sf_val = sf_date = lpr1y_val = lpr_date = shibor_val = shibor_date = None
    pe_val = pb_val = dv_val = idx_date = None

    try:
        pmi_rows = cn_data.call("cn_pmi", {}, "month,pmi010000")
        latest_pmi = _latest([r for r in pmi_rows if r.get("pmi010000") is not None], "month")
        if latest_pmi:
            pmi_val, pmi_date = latest_pmi["pmi010000"], latest_pmi["month"]
    except Exception:
        pass

    try:
        cpi_rows = cn_data.call("cn_cpi", {}, "month,nt_yoy")
        latest_cpi = _latest([r for r in cpi_rows if r.get("nt_yoy") is not None], "month")
        if latest_cpi:
            cpi_val, cpi_date = latest_cpi["nt_yoy"], latest_cpi["month"]
    except Exception:
        pass

    try:
        ppi_rows = cn_data.call("cn_ppi", {}, "month,ppi_yoy")
        latest_ppi = _latest([r for r in ppi_rows if r.get("ppi_yoy") is not None], "month")
        if latest_ppi:
            ppi_val, ppi_date = latest_ppi["ppi_yoy"], latest_ppi["month"]
    except Exception:
        pass

    try:
        m_rows = cn_data.call("cn_m", {}, "month,m2_yoy")
        latest_m = _latest([r for r in m_rows if r.get("m2_yoy") is not None], "month")
        if latest_m:
            m2_val, m2_date = latest_m["m2_yoy"], latest_m["month"]
    except Exception:
        pass

    try:
        # stk_endval = period-end stock of total social financing (亿元).
        # inc_month (monthly incremental flow) is too volatile/seasonal for YoY growth.
        sf_rows = cn_data.call("sf_month", {}, "month,stk_endval")
        sf_clean = [r for r in sf_rows if r.get("stk_endval") is not None]
        sf_clean.sort(key=lambda r: r["month"], reverse=True)
        if len(sf_clean) >= 13:
            latest = sf_clean[0]["stk_endval"]
            year_ago = sf_clean[12]["stk_endval"]
            if year_ago:
                sf_val = round((latest / year_ago - 1) * 100, 1)
            sf_date = sf_clean[0]["month"]
    except Exception:
        pass

    try:
        lpr_rows = cn_data.call("shibor_lpr", {}, "date,1y")
        latest_lpr = _latest(lpr_rows, "date")
        if latest_lpr:
            lpr1y_val, lpr_date = latest_lpr.get("1y"), latest_lpr["date"]
    except Exception:
        pass

    try:
        sh_rows = cn_data.call("shibor", {}, "date,on")
        latest_sh = _latest(sh_rows, "date")
        if latest_sh:
            shibor_val, shibor_date = latest_sh.get("on"), latest_sh["date"]
    except Exception:
        pass

    try:
        idx = cn_data.latest_row(
            "index_dailybasic", CSI300,
            "ts_code,trade_date,pe,pb,turnover_rate,total_mv",
            lookback_days=15,
        )
        if idx:
            pe_val, pb_val, idx_date = idx.get("pe"), idx.get("pb"), idx.get("trade_date")
            idx_date = f"{idx_date[:4]}-{idx_date[4:6]}-{idx_date[6:]}" if idx_date else None
    except Exception:
        pass

    pmi_status, pmi_reading = _classify_pmi(pmi_val)
    cpi_status, cpi_reading = _classify_cpi(cpi_val)
    ppi_status, ppi_reading = _classify_ppi(ppi_val)
    m2_status, m2_reading   = _classify_m2(m2_val)
    sf_status, sf_reading   = _classify_sf(sf_val)
    lpr_status, lpr_reading, real_rate = _classify_lpr_real(lpr1y_val, cpi_val)
    shibor_status, shibor_reading = _classify_shibor(shibor_val)
    pe_status, pe_reading = _classify_csi300_pe(pe_val)

    regime   = _classify_regime(pmi_val, cpi_val, ppi_val)
    growth_risk = _growth_risk(pmi_val, sf_val, ppi_val)

    cpi_date_fmt = f"{cpi_date[:4]}-{cpi_date[4:]}" if cpi_date else None
    ppi_date_fmt = f"{ppi_date[:4]}-{ppi_date[4:]}" if ppi_date else None
    pmi_date_fmt = f"{pmi_date[:4]}-{pmi_date[4:]}" if pmi_date else None
    m2_date_fmt  = f"{m2_date[:4]}-{m2_date[4:]}" if m2_date else None
    sf_date_fmt  = f"{sf_date[:4]}-{sf_date[4:]}" if sf_date else None
    lpr_date_fmt = f"{lpr_date[:4]}-{lpr_date[4:6]}-{lpr_date[6:]}" if lpr_date else None
    shibor_date_fmt = f"{shibor_date[:4]}-{shibor_date[4:6]}-{shibor_date[6:]}" if shibor_date else None

    short_cycle = [
        _ind("pmi", "Manufacturing PMI", "signal", pmi_val, pmi_date_fmt, "", pmi_status, pmi_reading,
             "China (CFLP) manufacturing PMI — a survey diffusion index that turns before GDP. "
             "How to read: >50 = expansion, <50 = contraction; the 50 line is the pivot. "
             "For A-shares: rising/above-50 favours cyclicals & industrials; sub-50 or falling pressures earnings and argues for defensives. It's the single most-watched real-activity gauge."),
        _ind("sf_yoy", "Social Financing Growth", "signal", sf_val, sf_date_fmt, "%", sf_status, sf_reading,
             "YoY growth in the STOCK (outstanding balance) of Total Social Financing — China's broadest credit measure and the most policy-responsive cycle signal. "
             "Note: this is credit stock growth, not the technical 'credit impulse' (which is the change in new credit FLOW as a % of GDP — a different, second-derivative measure this product doesn't compute). "
             "How to read: accelerating TSF leads equity rallies by ~1–2 quarters; decelerating credit is an early risk-off flag. Watch the direction more than the level."),
        _ind("cpi", "CPI (YoY)", "signal", cpi_val, cpi_date_fmt, "%", cpi_status, cpi_reading,
             "National consumer inflation, YoY. China's problem has been too-LOW inflation, not too-high. "
             "How to read: near-zero/negative = weak demand and deflation risk (bad for corporate pricing power); a gentle rise toward ~2% is healthy reflation and supportive for equities. Below zero is a warning."),
        _ind("ppi", "PPI (YoY)", "signal", ppi_val, ppi_date_fmt, "%", ppi_status, ppi_reading,
             "Factory-gate producer inflation, YoY. Directly drives industrial margins. "
             "How to read: persistent PPI deflation (negative) squeezes upstream/cyclical earnings; PPI turning UP from negative is a strong positive for materials, energy & industrials. A leading margin indicator."),
        _ind("lpr_real", "Real Policy Rate (1Y LPR − CPI)", "signal", real_rate, lpr_date_fmt, "%", lpr_status, lpr_reading,
             "The 1-Year Loan Prime Rate minus trailing CPI — an EX-POST real policy-rate proxy (nominal short-term policy rate minus backward-looking inflation). "
             "This is NOT analogous to the US 10Y TIPS yield: TIPS is a market-priced, forward-looking real yield over a 10-year horizon, while this is a simple arithmetic proxy off a 1-year policy rate and trailing inflation — different duration, different construction, not a comparable instrument. "
             "How to read: HIGH real rates are restrictive — and because China's CPI is so low, real rates can be tight even after nominal cuts (a hidden tightening). FALLING real rates ease conditions and lift valuations. This is the true policy-stance gauge, not the headline rate."),
        _ind("m2_yoy", "M2 Money Supply Growth", "context", m2_val, m2_date_fmt, "%", m2_status, m2_reading,
             "Broad money-supply growth, YoY. "
             "How to read: >~11% = loose liquidity supportive of asset prices; sub-8% = tight. Most telling relative to nominal GDP growth — M2 running well above it signals excess liquidity that can rotate into equities."),
        _ind("shibor_on", "SHIBOR Overnight Rate", "context", shibor_val, shibor_date_fmt, "%", shibor_status, shibor_reading,
             "Overnight interbank funding cost in Shanghai — China's SOFR. "
             "How to read: low & stable = ample liquidity (PBoC accommodative); a sudden spike = funding stress or the PBoC draining, and often precedes equity wobbles. It's the highest-frequency liquidity tell here."),
        _ind("lpr_1y", "1Y Loan Prime Rate", "context", lpr1y_val, lpr_date_fmt, "%", "neutral",
             f"{lpr1y_val:.2f}%" if lpr1y_val is not None else "No data",
             "The PBoC's benchmark lending rate — the closest analogue to the Fed funds rate. "
             "How to read: cuts lower borrowing costs and are a deliberate easing signal, historically a tailwind for equities with a lag. Changes are chunky and infrequent, so each move is a real policy statement."),
    ]

    long_cycle = [
        _ind("csi300_pe", "CSI 300 P/E (TTM)", "signal", pe_val, idx_date, "×", pe_status, pe_reading,
             "Trailing P/E of the CSI 300 (China large-cap, the rough S&P 500 analogue). "
             "How to read AGAINST ITS OWN HISTORY, not vs the US: a low historical percentile = cheap / margin of safety; a high percentile = stretched. A-share valuations mean-revert hard, so the extremes are what matter — this is a long-horizon buy/sell context, not a timing trigger."),
        _ind("csi300_pb", "CSI 300 P/B", "context", pb_val, idx_date, "×",
             "neutral", f"{pb_val:.2f}×" if pb_val is not None else "No data",
             "Price-to-book of the CSI 300. China large-caps (heavy in banks/SOEs) structurally trade at lower P/B than US peers, so judge it on its own scale. "
             "How to read: P/B near or below ~1.3–1.4 has historically marked major index bottoms — a useful long-run floor gauge and margin-of-safety check."),
    ]

    payload = {
        "regime": regime,
        "recession_risk": growth_risk,
        "short_cycle": short_cycle,
        "long_cycle": long_cycle,
        "as_of": now_str,
        "data_live": live,
    }
    _CACHE["data"] = payload
    _CACHE["ts"] = now
    return payload

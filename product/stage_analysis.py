"""Stan Weinstein Stage Analysis — indicators + stage classification (Phase 2).

Computes every Weinstein primitive from the weekly cache built in Phase 2's
prerequisite, product/price_history.py. Nothing here fetches: it is pure
computation over the cached 70-week close/volume/benchmark arrays, so it is
cheap enough to run at request time (like the percentile ranks in
filter_screener.py) and needs no cache of its own.

MOVING AVERAGE VARIANTS. Weinstein's book specifies a 30-week SIMPLE moving
average (verified against multiple sources 2026-08-15 — it is SMA, not EMA).
Per user request this module ALSO computes the EMA30 variant and a full
parallel set of derived fields, so both can be compared side by side:
    sma30 / ema30, price_vs_*, *_slope, *_slope_dir, and stage / stage_ema.
`stage` is the canonical (SMA) reading; `stage_ema` is the faster-responding
alternative and is NOT what the book describes. A stock can legitimately read
Stage 2 on one and Stage 3 on the other — that divergence is information, not
a bug, but the canonical label is the SMA one.

PARTIAL-WEEK HANDLING. yfinance labels US weekly bars by the week's START
(Monday), so the trailing bar is the CURRENT, INCOMPLETE week whenever the
screen runs mid-week. Tushare publishes only completed weeks, so CN normally
has no partial bar — the check is applied to both markets defensively anyway.
This matters because VOLUME ACCUMULATES over the week while PRICE DOES NOT:
  - price / MA / RS  -> USE the trailing bar (its close is simply the latest
    price, which is exactly what "price vs 30-week MA" should reflect).
  - volume ratio     -> EXCLUDE it. Measured live 2026-08-19, the partial bar
    carried ~half a normal week's volume (market-median 1.25M vs 2.37M), which
    made 4,682 stocks read as "volume drying up" versus 2,530 genuinely doing
    so. Left uncorrected, the volume signal would swing with the DAY OF WEEK
    the screen is run rather than with the market, and would systematically
    suppress the volume-surge confirmation that is Weinstein's core breakout
    filter. Pro-rating the partial week was rejected: inventing values to fill
    a known gap is the failure mode behind earlier bugs in this codebase.

RESISTANCE IS CLOSE-BASED, NOT HIGH-BASED. Phase 1 stored close+volume only,
so the base ceiling here is the max weekly CLOSE over the lookback rather than
the max intraweek HIGH. This is defensible on its own terms — Weinstein
confirms breakouts on a WEEKLY CLOSE above resistance — but the conventional
resistance line is drawn at the highs, so this reads slightly conservative
(max close <= max high). Storing high/low is a cheap Phase-1 extension if a
true high-based level is wanted.

GAPS ARE SKIPPED, NEVER INTERPOLATED. All indicators use the last N *valid*
observations, so a long-suspended stock's "30 weeks" may span more than 30
calendar weeks. Consistent with the cumulative-YTD differencing discipline
elsewhere in this codebase.

UNPINNED CHOICES (documented rather than hidden — sources do not specify these):
  - volume-average window (10 completed weeks)
  - the +/-5% "near the average" band separating a true base from a stock
    merely trading far from a stalled MA
  - slope measurement horizon (13 weeks) and the flat/rising band (+/-1.0%)
  - the Stage 1 vs Stage 3 disambiguation heuristic (see _classify)
Every underlying raw value is exposed as its own field so these thresholds can
be overridden downstream rather than being baked into a single opaque verdict.

NOT VALIDATED. No backtest has been run. This is a faithful implementation of
a documented method, not evidence that the method works. Stage is reported as
a DESCRIPTIVE STATE, never as a buy/sell instruction — consistent with
cn_limit.py's state-only doctrine and this product's inform-not-advise line.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

_MA = 30            # Weinstein's 30-week moving average
_RS = 52            # Mansfield RS lookback (52-week SMA of relative performance)

# ── Percentile relative strength ─────────────────────────────────────────────
# Mansfield RS is a TIME-SERIES measure — "is this stock stronger than its own
# 52-week norm?" — and its scale is unbounded and volatility-dependent. Measured
# across this universe it ran from -100 to +938 with a stdev of 42, so a "+5"
# means something entirely different for a utility than for a biotech, and
# stocks that crashed then bounced get PINNED at the floor and stop
# discriminating (INHD: top 3% by recent momentum, yet Mansfield -98).
#
# A percentile rank answers the CROSS-SECTIONAL question instead — "how does
# this stock rank against every other stock?" — and is bounded 1-99 and uniform
# by construction, which is what "show me the top 20%" actually needs.
#
# Two accepted constructions, and they genuinely disagree (rank corr 0.83):
#   IBD/O'Neil : 0.4*ROC(3m) + 0.2*ROC(6m) + 0.2*ROC(9m) + 0.2*ROC(12m), with
#                the most recent quarter DOUBLE-weighted to catch new leaders.
#   Academic   : Jegadeesh-Titman 12-1 — the trailing 12-month return EXCLUDING
#                the most recent period, because very short-term returns
#                mean-revert. Note this is the opposite emphasis to IBD's.
# Both are offered; neither is treated as the settled answer.
_RS_W3, _RS_W6, _RS_W9, _RS_W12 = 13, 26, 38, 52   # weeks ~ 63/126/189/252 days
_RS_MIN_WEEKS = _RS_W12 + 2                        # 12-1 needs one extra bar
_SLOPE_W = 13       # weeks over which MA slope is measured
_PRIOR_W = 26       # weeks back for the prior-trend leg (Stage 1 vs 3)
_VOL_W = 13         # completed weeks in the breakout-volume baseline.
                    # Aligned to 13 so it shares a denominator with the
                    # SUSTAINED test (13wk vs prior 13wk) — the two answer
                    # different questions but are now directly comparable.
                    # Moving 10 -> 13 shifted readings a median 6.4% and
                    # moved 155 of 5,490 stocks (2.8%) across the 1.5x line.
_RES_W = 30         # lookback for base resistance (max weekly close)
_FLAT = 1.0         # +/- % band on the slope that counts as "flat"
_NEAR = 5.0         # +/- % of the MA within which price counts as "oscillating around" it

# Enough history to classify: MA at t-_PRIOR_W needs _MA + _PRIOR_W valid weeks.
_MIN_WEEKS = _MA + _PRIOR_W

# ── Stage 1 base-quality test ────────────────────────────────────────────────
# Weinstein's Stage 1 is not just "flat average". A real base is a SEQUENCE:
# a meaningful decline, then price going range-bound, volume drying up, and the
# average finally levelling off. The stage LABEL below still comes from the
# canonical position+slope rule; these four tests report whether the label is
# backed by a textbook base, and are surfaced separately rather than silently
# reclassifying stocks.
#
# The MA here is 60 TRADING DAYS (~12 weeks), deliberately shorter than the
# 30-week stage MA. With a 30-week MA the full test needs 30+26+26 = 82 weeks,
# more than the 70-week cache holds, so it could not be computed at all. At 12
# weeks the requirement drops to 12+13+26 = 51 weeks and fits with headroom.
# NOTE the consequence, stated rather than hidden: the base test and the stage
# label are computed off DIFFERENT averages, so a stock can show a clean base
# while its 30-week line is still falling.
_BASE_MA = 12          # 60 trading days
_BASE_W = 13           # the base itself
_BASE_PRIOR_W = 26     # window for the decline that precedes the base
_BASE_FLAT = 5.0       # max |net MA drift| across the base, %
_BASE_BAND = 25.0      # max price range across the base, % of mean
_BASE_DECLINE = -20.0  # required drop from the pre-base peak, %
_BASE_VOL = 0.0        # base-window volume vs the decline leg, % (<=0 = dry-up)
_BASE_MIN_WEEKS = _BASE_MA + _BASE_W + _BASE_PRIOR_W


# ── Stage 2 advance-quality test ─────────────────────────────────────────────
# Weinstein's Stage 2 is the phase that matters: the advance you actually want
# to own. A rising average alone is not enough — the move should be CONFIRMED
# by demand (volume expanding), by structure (each pullback bottoming higher),
# and by price holding above the average THROUGH its pullbacks, not just at the
# close of the latest week.
#
# Unlike the Stage 1 base test, this fits the canonical 30-week average
# (30 + 26 = 56 weeks, inside the 70-week cache), so it is measured off the
# SAME line as the stage label — no shorter-MA compromise is needed here.
_ADV_W = 26            # window over which the advance is judged
_ADV_VOL_MULT = 1.5    # volume expansion vs the preceding equal window
_ADV_ABOVE_PCT = 80.0  # % of weeks price must hold above the average
_ADV_SWING_K = 2       # bars each side that define a swing low


def _swing_lows(seq: list[float], k: int = _ADV_SWING_K) -> list[tuple[int, float]]:
    """Local minima with k lower bars on each side — the 'wave lows' a Stage 2
    advance should step upward through."""
    out = []
    for i in range(k, len(seq) - k):
        w = seq[i - k:i + k + 1]
        if seq[i] == min(w) and seq[i] < seq[i - k] and seq[i] < seq[i + k]:
            out.append((i, seq[i]))
    return out


def advance_quality(closes: list[float | None], vols: list[float | None],
                    ma_weeks: int = _MA, partial: bool = False) -> dict:
    """Weinstein Stage 2 confirmation checklist — volume expansion, rising
    average, higher lows, and price holding above the average through dips."""
    px = [c for c in closes if c is not None]
    vs = [v for c, v in zip(closes, vols) if c is not None]
    need = ma_weeks + _ADV_W
    out: dict[str, Any] = {"adv_testable": False, "adv_score": None,
                           "adv_weeks": _ADV_W, "adv_ma_weeks": ma_weeks}
    if len(px) < need:
        out["adv_note"] = f"needs {need} weekly bars, has {len(px)}"
        return out
    ma = _sma_series(px, ma_weeks)
    mwin = ma[-_ADV_W:]
    if any(x is None for x in mwin):
        out["adv_note"] = "moving average not warmed up across the window"
        return out
    win = px[-_ADV_W:]

    slope = _pct(ma[-1], ma[-1 - _SLOPE_W]) if len(ma) > _SLOPE_W else None
    above_pct = sum(1 for p, mm in zip(win, mwin) if p > mm) / _ADV_W * 100
    lows = _swing_lows(win)
    higher = all(lows[i][1] > lows[i - 1][1] for i in range(1, len(lows))) if len(lows) >= 2 else None
    # Volume excludes an in-progress week (see _split_windows).
    _vs = vs[:-1] if (partial and vs) else vs
    vr = [v for v in _vs[-_ADV_W // 2:] if v]
    vp = [v for v in _vs[-_ADV_W:-_ADV_W // 2] if v]
    vmult = (sum(vr) / len(vr)) / (sum(vp) / len(vp)) if vr and vp else None

    c1 = vmult is not None and vmult >= _ADV_VOL_MULT
    c2 = slope is not None and slope > _FLAT
    c3 = higher is True
    c4 = px[-1] > (ma[-1] or 0) and above_pct >= _ADV_ABOVE_PCT
    out.update({
        "adv_testable": True,
        "adv_vol_mult": None if vmult is None else round(vmult, 2),
        "adv_ma_slope_pct": None if slope is None else round(slope, 2),
        "adv_swing_lows": len(lows),
        "adv_higher_lows": higher,
        "adv_above_pct": round(above_pct, 1),
        "adv_c1_volume_expanding": c1, "adv_c2_ma_rising": c2,
        "adv_c3_higher_lows": c3, "adv_c4_price_above": c4,
        "adv_score": int(c1) + int(c2) + int(c3) + int(c4),
        "adv_textbook": c1 and c2 and c3 and c4,
        "adv_thresholds": {"vol_mult_min": _ADV_VOL_MULT, "above_pct_min": _ADV_ABOVE_PCT,
                           "slope_min": _FLAT, "swing_k": _ADV_SWING_K},
    })
    return out


# ── Stage 3 (top) and Stage 4 (decline) checklists ───────────────────────────
# Stage 3 is distribution: the advance stalls, the average rounds over, and
# volume and volatility BOTH pick up as holders sell into strength. Stage 4 is
# the decline itself — average falling, price beneath it — with rising volume
# as an optional aggravating factor rather than a requirement, since plenty of
# declines simply bleed away on thin trade.
#
# Thresholds are set against measured universe percentiles, not convention:
# volume multiple p75 = 1.32 and volatility ratio p75 = 1.23 across 5,490
# stocks, so "high" here means roughly top-quartile rather than arbitrary.
_TOP_W = 26            # window for the top/decline tests (30 + 26 = 56 <= cache)
_TOP_VOL_MULT = 1.25   # volume vs the preceding equal window
_TOP_VOLA_RATIO = 1.20 # realised volatility vs the preceding equal window
_TOP_FLAT = 3.0        # |MA slope| that counts as "flattening" (wider than _FLAT)
_DECL_VOL_MULT = 1.25  # optional: volume expanding into the decline


def _weekly_vol(seq: list[float]) -> float | None:
    """Population stdev of weekly returns, in %."""
    rets = [seq[i] / seq[i - 1] - 1 for i in range(1, len(seq)) if seq[i - 1]]
    if len(rets) < 3:
        return None
    mu = sum(rets) / len(rets)
    return (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 * 100


def _split_windows(closes, vols, w, partial: bool = False):
    """Split the window into recent/prior halves for volume and volatility.

    VOLUME excludes an in-progress week; PRICE does not. Volume accumulates
    through the week while price does not, so a partial bar always looks like
    low volume — mid-week that dragged the recent average down and understated
    expansion by ~3.4%, enough to push 82 stocks (1.5%) wrongly below the 1.5x
    test. Same rule _one already applied to vol_ratio."""
    px = [c for c in closes if c is not None]
    vs = [v for c, v in zip(closes, vols) if c is not None]
    if partial and vs:
        vs = vs[:-1]
    half = w // 2
    vr = [v for v in vs[-half:] if v]
    vp = [v for v in vs[-w:-half] if v]
    vmult = (sum(vr) / len(vr)) / (sum(vp) / len(vp)) if vr and vp else None
    v_recent, v_prior = _weekly_vol(px[-half:]), _weekly_vol(px[-w:-half])
    vola = (v_recent / v_prior) if (v_recent and v_prior) else None
    return px, vmult, v_recent, vola


def top_quality(closes: list[float | None], vols: list[float | None],
                ma_weeks: int = _MA, partial: bool = False) -> dict:
    """Stage 3 distribution checklist — heavy volume, widening swings, and the
    average rolling flat after an advance."""
    out: dict[str, Any] = {"top_testable": False, "top_score": None,
                           "top_weeks": _TOP_W, "top_ma_weeks": ma_weeks}
    px_all = [c for c in closes if c is not None]
    if len(px_all) < ma_weeks + _TOP_W:
        out["top_note"] = f"needs {ma_weeks + _TOP_W} weekly bars, has {len(px_all)}"
        return out
    ma = _sma_series(px_all, ma_weeks)
    if ma[-1] is None or len(ma) <= _SLOPE_W:
        out["top_note"] = "moving average not warmed up"
        return out
    px, vmult, wk_vol, vola = _split_windows(closes, vols, _TOP_W, partial)
    slope = _pct(ma[-1], ma[-1 - _SLOPE_W])
    c1 = vmult is not None and vmult >= _TOP_VOL_MULT
    c2 = vola is not None and vola >= _TOP_VOLA_RATIO
    c3 = slope is not None and abs(slope) <= _TOP_FLAT
    out.update({
        "top_testable": True,
        "top_vol_mult": None if vmult is None else round(vmult, 2),
        "top_vola_ratio": None if vola is None else round(vola, 2),
        "top_weekly_vol_pct": None if wk_vol is None else round(wk_vol, 2),
        "top_ma_slope_pct": None if slope is None else round(slope, 2),
        "top_c1_high_volume": c1, "top_c2_high_volatility": c2, "top_c3_ma_flat": c3,
        "top_score": int(c1) + int(c2) + int(c3),
        "top_textbook": c1 and c2 and c3,
        "top_thresholds": {"vol_mult_min": _TOP_VOL_MULT, "vola_ratio_min": _TOP_VOLA_RATIO,
                           "ma_flat_max": _TOP_FLAT},
    })
    return out


def decline_quality(closes: list[float | None], vols: list[float | None],
                    ma_weeks: int = _MA, partial: bool = False) -> dict:
    """Stage 4 checklist. Two CORE tests (average falling, price beneath it);
    rising volume is reported as an OPTIONAL aggravating factor and is
    deliberately excluded from the core score."""
    out: dict[str, Any] = {"decl_testable": False, "decl_score": None,
                           "decl_weeks": _TOP_W, "decl_ma_weeks": ma_weeks}
    px_all = [c for c in closes if c is not None]
    if len(px_all) < ma_weeks + _TOP_W:
        out["decl_note"] = f"needs {ma_weeks + _TOP_W} weekly bars, has {len(px_all)}"
        return out
    ma = _sma_series(px_all, ma_weeks)
    if ma[-1] is None or len(ma) <= _SLOPE_W:
        out["decl_note"] = "moving average not warmed up"
        return out
    px, vmult, _, _ = _split_windows(closes, vols, _TOP_W, partial)
    slope = _pct(ma[-1], ma[-1 - _SLOPE_W])
    pv = _pct(px[-1], ma[-1])
    c1 = slope is not None and slope < -_FLAT
    c2 = pv is not None and pv < 0
    opt = vmult is not None and vmult >= _DECL_VOL_MULT
    out.update({
        "decl_testable": True,
        "decl_ma_slope_pct": None if slope is None else round(slope, 2),
        "decl_price_vs_ma_pct": None if pv is None else round(pv, 2),
        "decl_vol_mult": None if vmult is None else round(vmult, 2),
        "decl_c1_ma_falling": c1, "decl_c2_price_below": c2,
        "decl_opt_volume_rising": opt,
        "decl_score": int(c1) + int(c2),          # core only: 0-2
        "decl_confirmed": c1 and c2,
        "decl_thresholds": {"slope_max": -_FLAT, "vol_mult_min": _DECL_VOL_MULT},
    })
    return out


def base_quality(closes: list[float | None], vols: list[float | None],
                 ma_weeks: int = _BASE_MA, partial: bool = False) -> dict:
    """Weinstein Stage 1 base checklist. Returns each criterion separately plus
    a score, so a caller can demand all four or merely count them."""
    px = [c for c in closes if c is not None]
    vs = [v for c, v in zip(closes, vols) if c is not None]
    need = ma_weeks + _BASE_W + _BASE_PRIOR_W
    out: dict[str, Any] = {"base_ma_weeks": ma_weeks, "base_weeks": _BASE_W,
                           "base_testable": False, "base_score": None}
    if len(px) < need:
        out["base_note"] = f"needs {need} weekly bars, has {len(px)}"
        return out

    ma = _sma_series(px, ma_weeks)
    ms = [x for x in ma[-_BASE_W:] if x is not None]
    if len(ms) < _BASE_W:
        out["base_note"] = "moving average not warmed up across the base"
        return out

    base = px[-_BASE_W:]
    mean = sum(base) / len(base)
    band = (max(base) - min(base)) / mean * 100 if mean else None
    drift = (ms[-1] / ms[0] - 1) * 100 if ms[0] else None
    pre = px[:-_BASE_W]
    peak = max(pre[-_BASE_PRIOR_W:])
    decline = (px[-_BASE_W] / peak - 1) * 100 if peak else None
    _vs = vs[:-1] if (partial and vs) else vs
    vb = [v for v in _vs[-_BASE_W:] if v]
    vd = [v for v in _vs[-(_BASE_W + _BASE_PRIOR_W):-_BASE_W] if v]
    vtrend = ((sum(vb) / len(vb)) / (sum(vd) / len(vd)) - 1) * 100 if vb and vd else None

    c1 = decline is not None and decline <= _BASE_DECLINE
    c2 = band is not None and band <= _BASE_BAND
    c3 = vtrend is not None and vtrend <= _BASE_VOL
    c4 = drift is not None and abs(drift) <= _BASE_FLAT
    out.update({
        "base_testable": True,
        "base_decline_pct": None if decline is None else round(decline, 2),
        "base_band_pct": None if band is None else round(band, 2),
        "base_vol_trend_pct": None if vtrend is None else round(vtrend, 2),
        "base_ma_drift_pct": None if drift is None else round(drift, 2),
        "base_c1_prior_decline": c1, "base_c2_range_bound": c2,
        "base_c3_volume_dryup": c3, "base_c4_ma_flat": c4,
        "base_score": int(c1) + int(c2) + int(c3) + int(c4),
        "base_textbook": c1 and c2 and c3 and c4,
        "base_thresholds": {"decline_max": _BASE_DECLINE, "band_max": _BASE_BAND,
                            "vol_max": _BASE_VOL, "ma_flat_max": _BASE_FLAT},
    })
    return out


def _sma_series(vals: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(vals)
    run = 0.0
    for i, v in enumerate(vals):
        run += v
        if i >= n:
            run -= vals[i - n]
        if i >= n - 1:
            out[i] = run / n
    return out


def _ema_series(vals: list[float], span: int) -> list[float | None]:
    """Seeded with the SMA of the first `span` points, then standard
    alpha = 2/(span+1) recursion — the conventional EMA construction."""
    out: list[float | None] = [None] * len(vals)
    if len(vals) < span:
        return out
    alpha = 2.0 / (span + 1)
    e = sum(vals[:span]) / span
    out[span - 1] = e
    for i in range(span, len(vals)):
        e = alpha * vals[i] + (1 - alpha) * e
        out[i] = e
    return out


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1) * 100


def _dir(slope_pct: float | None) -> str | None:
    if slope_pct is None:
        return None
    if slope_pct > _FLAT:
        return "rising"
    if slope_pct < -_FLAT:
        return "falling"
    return "flat"


def is_partial_last_bar(market: str, last_date: str, today: date | None = None) -> bool:
    """US bars are labelled by week START (Monday) so the week ends +4d; CN bars
    are labelled by week END. Either way the bar is incomplete if its week has
    not finished yet. Pure date arithmetic — no guessing from the data."""
    if not last_date:
        return False
    today = today or date.today()
    try:
        d = datetime.strptime(last_date, "%Y%m%d").date()
    except ValueError:
        return False
    week_end = d + timedelta(days=4) if market == "us" else d
    return week_end >= today


def _classify(price_vs_ma: float | None, slope: float | None, prior_slope: float | None) -> int | None:
    """Weinstein's four stages from position + trend direction.

    Stages 2 and 4 are unambiguous (price above a rising MA / below a falling
    one). Stages 1 and 3 BOTH show a flattening MA and are distinguished only
    by what came before — a base forms after a decline, a top forms after an
    advance — so the prior-leg slope is used to tell them apart. That
    disambiguation is an explicit inference, not a formula stated in the book.
    """
    if price_vs_ma is None or slope is None:
        return None
    d = _dir(slope)
    above = price_vs_ma >= 0
    if above and d == "rising":
        return 2
    if not above and d == "falling":
        return 4
    if d == "flat":
        # A base (Stage 1) and a top (Stage 3) BOTH show a flat MA, and are
        # told apart by what preceded them. But that prior-trend test is only
        # meaningful when price is actually oscillating AROUND the average —
        # which is what "basing" means. Bug found 2026-08-19: without the
        # proximity gate, MSFT (price +12.1% ABOVE a flat EMA30, prior leg
        # falling) was labelled "Stage 1 Basing" purely because the earlier
        # leg fell. A stock 12% clear of its average is not building a base.
        # So: proximity decides first, prior-trend only inside the band.
        if abs(price_vs_ma) <= _NEAR:
            return 3 if _dir(prior_slope) == "rising" else 1
        return 3 if above else 1   # far above a stalled MA = topping; far below = washed out
    # Mixed: direction and position disagree — treat as the transition it is.
    if above and d == "falling":
        return 3          # still above the average but the trend has rolled over
    return 1              # below the average while it turns up = early base


_STAGE_LABEL = {
    1: "Stage 1 — Basing",
    2: "Stage 2 — Advancing",
    3: "Stage 3 — Topping",
    4: "Stage 4 — Declining",
}


def _one(closes: list[float | None], vols: list[float | None],
         idx_closes: list[float | None], partial: bool) -> dict[str, Any]:
    """All indicators for a single stock. Arrays are aligned index-for-index
    to the shared weekly date axis; None marks a missing week."""
    # Valid (stock, index) pairs only — Mansfield RS needs both sides present.
    px, rp = [], []
    for c, ic in zip(closes, idx_closes):
        if c is None:
            continue
        px.append(c)
        rp.append((c / ic * 100) if (ic not in (None, 0)) else None)
    n = len(px)
    out: dict[str, Any] = {"weeks_available": n, "insufficient_history": n < _MIN_WEEKS}
    if n < _MA:
        return out                      # cannot even form the 30-week average

    sma = _sma_series(px, _MA)
    ema = _ema_series(px, _MA)
    last = px[-1]
    out["price"] = round(last, 3)
    for tag, series in (("sma30", sma), ("ema30", ema)):
        cur = series[-1]
        prev = series[-1 - _SLOPE_W] if n > _SLOPE_W else None
        prior = series[-1 - _PRIOR_W] if n > _PRIOR_W else None
        slope = _pct(cur, prev)
        out[tag] = round(cur, 3) if cur is not None else None
        out[f"price_vs_{tag}"] = round(_pct(last, cur), 2) if cur else None
        out[f"{tag}_slope"] = round(slope, 2) if slope is not None else None
        out[f"{tag}_slope_dir"] = _dir(slope)
        out[f"_{tag}_prior"] = _pct(prev, prior)

    # Stage: canonical from SMA, plus the EMA variant.
    for tag, key in (("sma30", "stage"), ("ema30", "stage_ema")):
        st = None if out["insufficient_history"] else _classify(
            out.get(f"price_vs_{tag}"), out.get(f"{tag}_slope"), out.pop(f"_{tag}_prior", None))
        out[key] = st
        out[f"{key}_label"] = _STAGE_LABEL.get(st) if st else (
            "Insufficient history" if out["insufficient_history"] else None)
    out.pop("_sma30_prior", None)
    out.pop("_ema30_prior", None)

    # Mansfield RS — ((RP / SMA(RP,52)) - 1) * 100, per the standard formula.
    rp_valid = [v for v in rp if v is not None]
    if len(rp_valid) >= _RS:
        base = sum(rp_valid[-_RS:]) / _RS
        out["mansfield_rs"] = round((rp_valid[-1] / base - 1) * 100, 2) if base else None
    else:
        out["mansfield_rs"] = None

    # Raw cross-sectional RS scores. Percentile-ranked later in
    # build_stage_map, since a percentile needs the whole universe.
    out["rs_ibd_raw"] = out["rs_jt_raw"] = None
    if len(px) >= _RS_MIN_WEEKS:
        def _roc(w):
            base = px[-1 - w]
            return (px[-1] / base - 1) if base else None
        r3, r6, r9, r12 = _roc(_RS_W3), _roc(_RS_W6), _roc(_RS_W9), _roc(_RS_W12)
        if None not in (r3, r6, r9, r12):
            out["rs_ibd_raw"] = 0.4 * r3 + 0.2 * r6 + 0.2 * r9 + 0.2 * r12
        base12 = px[-1 - _RS_W12]
        if base12:
            # 12-1: skip the most recent bar (short-term reversal).
            out["rs_jt_raw"] = px[-2] / base12 - 1

    # Volume ratio — completed weeks ONLY (see module docstring).
    v_all = [(c, v) for c, v in zip(closes, vols) if c is not None]
    v_seq = [v for _, v in v_all]
    if partial and v_seq:
        v_seq = v_seq[:-1]
    v_seq = [v for v in v_seq if v is not None]
    if len(v_seq) >= _VOL_W + 1:
        baseline = sum(v_seq[-_VOL_W - 1:-1]) / _VOL_W
        out["vol_ratio"] = round(v_seq[-1] / baseline, 2) if baseline else None
        out["vol_week_complete"] = True
    else:
        out["vol_ratio"] = None
        out["vol_week_complete"] = None

    # Stage 1 base checklist (see base_quality's docstring for why it uses a
    # shorter average than the stage label does).
    try:
        out.update(base_quality(closes, vols, partial=partial))
    except Exception:                                  # noqa: BLE001
        out["base_testable"] = False
    try:
        out.update(advance_quality(closes, vols, partial=partial))
    except Exception:                                  # noqa: BLE001
        out["adv_testable"] = False
    try:
        out.update(top_quality(closes, vols, partial=partial))
    except Exception:                                  # noqa: BLE001
        out["top_testable"] = False
    try:
        out.update(decline_quality(closes, vols, partial=partial))
    except Exception:                                  # noqa: BLE001
        out["decl_testable"] = False

    # Base resistance — max weekly close over the lookback, always EXCLUDING
    # the current bar (you compare today's price against the prior ceiling,
    # never against a window that contains today).
    hist = px[:-1]
    if len(hist) >= _RES_W:
        res = max(hist[-_RES_W:])
        out["resistance"] = round(res, 3)
        out["dist_to_resistance"] = round((last / res - 1) * 100, 2) if res else None
        out["above_resistance"] = last > res
    else:
        out["resistance"] = out["dist_to_resistance"] = out["above_resistance"] = None
    return out


_MEMO: dict[str, dict] = {}


def build_stage_map(market: str) -> dict[str, dict]:
    """Whole-market stage/indicator map keyed by symbol. Returns {} if the
    weekly cache has not been built yet.

    Memoised on the underlying cache's build timestamp: this runs on EVERY
    filter request, and recomputing ~5,500 stocks each time is pure waste when
    the weekly cache only changes once a day. The memo key includes the row
    count and the partial-bar flag so a rebuild, a resize, or the week rolling
    over all invalidate it correctly.
    """
    from . import price_history
    h = price_history.get_history(market)
    dates, idx, stocks = h["dates"], h["index"], h["stocks"]
    if not dates or not stocks or not idx:
        return {}
    partial_flag = is_partial_last_bar(market, dates[-1])
    key = (h.get("built_at"), len(dates), len(stocks), dates[-1], partial_flag)
    memo = _MEMO.get(market)
    if memo and memo.get("key") == key:
        return memo["map"]
    out: dict[str, dict] = {}
    for sym, s in stocks.items():
        try:
            out[sym] = _one(s.get("c") or [], s.get("v") or [], idx, partial_flag)
        except Exception:
            continue          # one bad series must never abort the whole market

    # Percentile RS. This is the step that cannot live in _one: a percentile is
    # inherently CROSS-SECTIONAL, so it needs every stock scored first. Ranked
    # 1-99 with ties sharing the average rank, so a cluster of identical scores
    # cannot hand one of them an arbitrarily better percentile.
    for raw_key, pct_key in (("rs_ibd_raw", "rs_pct"), ("rs_jt_raw", "rs_pct_jt")):
        scored = [(sym, v[raw_key]) for sym, v in out.items() if v.get(raw_key) is not None]
        n_s = len(scored)
        for v in out.values():
            v[pct_key] = None
        if n_s < 2:
            continue
        scored.sort(key=lambda kv: kv[1])
        i = 0
        while i < n_s:
            j = i
            while j + 1 < n_s and scored[j + 1][1] == scored[i][1]:
                j += 1
            avg_rank = (i + j) / 2.0
            pct = round(avg_rank / (n_s - 1) * 98 + 1, 1)
            for k in range(i, j + 1):
                out[scored[k][0]][pct_key] = pct
            i = j + 1

    _MEMO[market] = {"key": key, "map": out}
    return out


def stage_meta(market: str) -> dict:
    from . import price_history
    h = price_history.get_history(market)
    dates = h["dates"]
    from . import price_history as _ph
    return {
        "weeks": len(dates),
        "as_of": dates[-1] if dates else None,
        "benchmark": _ph.price_history_status(market).get("benchmark"),
        "last_bar_partial": is_partial_last_bar(market, dates[-1]) if dates else None,
        "built_at": h.get("built_at", 0.0),
        "ma_period": _MA, "rs_period": _RS, "slope_weeks": _SLOPE_W,
        "vol_window": _VOL_W, "resistance_window": _RES_W, "flat_band_pct": _FLAT,
    }

def stage_for(market: str, symbol: str) -> dict | None:
    """Stage Analysis for ONE stock, for the stock-detail page.

    Reuses the memoised whole-market map rather than recomputing a single
    series, so the detail page and the screener can never disagree — they read
    the identical numbers. Returns None when the weekly cache has not been
    built or the symbol is not covered (e.g. under 30 weeks of history).
    """
    try:
        from . import price_history
        price_history.ensure_price_history(market)
        v = build_stage_map(market).get(symbol)
        if not v:
            return None
        out = dict(v)
        out["meta"] = stage_meta(market)
        return out
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Phase 4 — top-down: market stage -> industry-group stage -> stock stage
#
# Weinstein screens top-down: a Stage 2 breakout inside a group that is ITSELF
# advancing has better odds than the same setup in a lagging group. This builds
# a synthetic index per industry group and runs the identical stage logic on
# it, so a group and a stock are judged by the same rules.
#
# GROUPING. `industry` is used for BOTH markets rather than US's 12 broad
# `sector` values — those are far too coarse (Finance alone holds 1,208 names).
# US has 152 industries (136 with >=5 members), CN 110 (105 with >=5), which is
# comparable granularity and matches what Weinstein means by "industry group".
# ══════════════════════════════════════════════════════════════════════════════

_GROUP_MIN = 5          # a "group" needs enough members to mean anything
_GROUP_MEMO: dict[str, dict] = {}


def _symbol_groups(market: str) -> dict[str, str]:
    """symbol -> industry, read from the already-built market snapshot.

    Deliberately does NOT force a snapshot build: this is called from the stock
    detail page too, and triggering a whole-universe fetch there would be a
    surprising cost. If the snapshot is cold we simply have no groups yet.
    """
    try:
        from . import price_history
        cached = price_history.get_history(market).get("groups") or {}
        if cached:
            return cached
    except Exception:
        pass
    try:    # fall back to a live snapshot if one happens to be warm already
        from .filter_screener import _US, _CN
        rows = _CN["rows"] if market == "cn" else _US["rows"]
        return {r["symbol"]: (r.get("industry") or "").strip()
                for r in rows if r.get("industry")}
    except Exception:
        return {}


def _equal_weight_index(symbols: list[str], stocks: dict, n: int) -> list[float]:
    """Chained equal-weight return index for a group.

    Averaging member PRICE LEVELS would be wrong — a ¥1,000 stock would swamp a
    ¥5 one. Instead chain the weekly RETURN across members present in both
    adjacent weeks, which handles suspensions and mid-window IPOs without
    interpolating anything.

    Weeks whose price ratio falls outside [1/3, 3] are DROPPED as corporate-
    action artifacts rather than treated as returns. Bug found 2026-08-20: RCON
    printed 0.03 -> 3.67 in one week (+12,133%, an unadjusted reverse split);
    with a plain mean that single member added ~+607% to a 20-member group,
    pushing "Oilfield Services/Equipment" to a nonsensical Mansfield RS of
    +534. Switching to a median killed the outlier but was also wrong: an
    equal-weight portfolio return IS the mean by definition, and the median
    systematically under-reports right-skewed equity returns (it collapsed the
    entire CN leaderboard to <= +2.15). So: drop the artifacts, keep the mean.
    A 3x weekly move is essentially always a split, not a return; genuine
    extremes inside that band are preserved untouched.
    """
    out = [100.0] * n
    lvl = 100.0
    for i in range(1, n):
        rets = []
        for s in symbols:
            c = stocks[s]["c"]
            a, b = c[i - 1], c[i]
            if a and b and a > 0 and (1.0 / 3.0) <= (b / a) <= 3.0:
                rets.append(b / a - 1.0)
        if rets:
            lvl *= (1.0 + sum(rets) / len(rets))
        out[i] = lvl
    return out


def build_group_stages(market: str) -> dict:
    """{'market': {...}, 'groups': {industry: {...}}} — memoised like the
    stock map, since it is derived from the same daily-refreshed cache."""
    from . import price_history
    h = price_history.get_history(market)
    dates, idx, stocks = h["dates"], h["index"], h["stocks"]
    if not dates or not stocks or not idx:
        return {"market": None, "groups": {}}
    groups = _symbol_groups(market)
    key = (h.get("built_at"), len(dates), len(stocks), len(groups), dates[-1])
    memo = _GROUP_MEMO.get(market)
    if memo and memo.get("key") == key:
        return memo["val"]

    n = len(dates)
    partial = is_partial_last_bar(market, dates[-1])

    # The market's own stage, from the benchmark series. Its RS-vs-itself is
    # definitionally zero, so it is nulled rather than shown as a real reading.
    market_v = _one(idx, [None] * n, idx, partial)
    market_v["mansfield_rs"] = None
    market_v["vol_ratio"] = None

    smap = build_stage_map(market)
    by_group: dict[str, list[str]] = {}
    for sym, g in groups.items():
        if g and sym in stocks:
            by_group.setdefault(g, []).append(sym)

    out: dict[str, dict] = {}
    for g, syms in by_group.items():
        if len(syms) < _GROUP_MIN:
            continue
        try:
            gi = _equal_weight_index(syms, stocks, n)
            v = _one(gi, [None] * n, idx, partial)
        except Exception:
            continue
        stages = [smap[s]["stage"] for s in syms if s in smap and smap[s].get("stage")]
        v["members"] = len(syms)
        v["scored_members"] = len(stages)
        for st in (1, 2, 3, 4):
            v[f"pct_stage{st}"] = round(100 * sum(1 for x in stages if x == st) / len(stages), 1) if stages else None
        v.pop("vol_ratio", None)          # a synthetic index has no meaningful volume
        v.pop("vol_week_complete", None)
        out[g] = v

    # Leadership rank by Mansfield RS — 1 = strongest group vs the market.
    ranked = sorted((g for g in out if out[g].get("mansfield_rs") is not None),
                    key=lambda g: -out[g]["mansfield_rs"])
    for i, g in enumerate(ranked):
        out[g]["rs_rank"] = i + 1
        out[g]["rs_rank_of"] = len(ranked)

    val = {"market": market_v, "groups": out}
    _GROUP_MEMO[market] = {"key": key, "val": val}
    return val


def top_down_for(market: str, symbol: str) -> dict | None:
    """Market -> industry group -> stock, for the detail page's top-down panel.
    Returns None when the weekly cache is not built."""
    try:
        gs = build_group_stages(market)
        if not gs.get("market"):
            return None
        grp = _symbol_groups(market).get(symbol) or None
        return {
            "market": gs["market"],
            # Readable benchmark label — a bare "000985.SH" means nothing on screen.
            "market_name": {"us": "S&P 500 (SPY)",
                            "cn": "中证全指 · CSI All-Share"}.get(market),
            "group_name": grp,
            "group": gs["groups"].get(grp) if grp else None,
            "stock": build_stage_map(market).get(symbol),
        }
    except Exception:
        return None

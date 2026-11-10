"""China VIX (QVIX) — 30-day implied vol from the 50ETF option chain.

Ported from the standalone research project (Quant/Projects/AShare_Fear_Greed_Index,
china_vix.py). Faithful Black-76 ATM-vol construction off the put-call-parity
forward, near/next expiries interpolated to 30 days, plus a put-call skew leg.

Kept as a STANDALONE REFERENCE — deliberately NOT part of the 6-leg Fear & Greed
composite (the research shows A-share implied vol is direction-blind: high QVIX
marks big moves of EITHER sign, and its forward-return quintiles are anti-contrarian,
so it is not a timing signal. The SKEW is the directional read: puts bid = fear).

Seeded from the project's validated weekly history (data/cn_vix.csv); updates
incrementally by computing only the missing recent Fridays, so refreshes are cheap.
"""
from __future__ import annotations

import math
import threading
import time
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
_CACHE = _DATA / "cn_vix.csv"
_TTL = 20 * 3600
_R = 0.02

_state = {"building": False, "meta": None}
_lock = threading.Lock()


# ── Black-76 ATM-IV machinery (verbatim port) ────────────────────────────────

def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _b76(cp, F, K, T, sig):
    if sig <= 0 or T <= 0:
        return max(0.0, (F - K) if cp == "C" else (K - F)) * math.exp(-_R * T)
    d1 = (math.log(F / K) + 0.5 * sig * sig * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    disc = math.exp(-_R * T)
    if cp == "C":
        return disc * (F * _ncdf(d1) - K * _ncdf(d2))
    return disc * (K * _ncdf(-d2) - F * _ncdf(-d1))


def _iv(cp, price, F, K, T):
    import numpy as np
    disc = math.exp(-_R * T)
    intrinsic = max(0.0, (F - K) if cp == "C" else (K - F)) * disc
    upper = (F * disc) if cp == "C" else (K * disc)
    if price <= intrinsic + 1e-6 or price >= upper - 1e-6 or T <= 0 or price <= 0:
        return np.nan
    lo, hi = 1e-4, 5.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _b76(cp, F, K, T, mid) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _sigma_for_expiry(chain, T):
    import numpy as np
    piv = chain.pivot_table(index="exercise_price", columns="call_put", values="price")
    if "C" not in piv or "P" not in piv:
        return np.nan, np.nan, None
    piv = piv.dropna(subset=["C", "P"])
    if len(piv) < 3:
        return np.nan, np.nan, None
    katm = (piv["C"] - piv["P"]).abs().idxmin()
    F = katm + math.exp(_R * T) * (piv.loc[katm, "C"] - piv.loc[katm, "P"])
    strikes = piv.index.to_numpy()
    near = strikes[np.argsort(np.abs(strikes - F))[:3]]
    ivs = []
    for K in near:
        for cp in ("C", "P"):
            v = _iv(cp, piv.loc[K, cp], F, K, T)
            if not np.isnan(v):
                ivs.append(v)
    return (np.nanmean(ivs) if ivs else np.nan), F, piv


def _skew_for_expiry(piv, F, T):
    import numpy as np
    if piv is None:
        return np.nan
    kp = piv.index[np.argmin(np.abs(piv.index - F * 0.95))]
    kc = piv.index[np.argmin(np.abs(piv.index - F * 1.05))]
    ivp = _iv("P", piv.loc[kp, "P"], F, kp, T)
    ivc = _iv("C", piv.loc[kc, "C"], F, kc, T)
    return ivp - ivc


def _meta():
    import pandas as pd
    from . import cn_data
    if _state["meta"] is not None:
        return _state["meta"]
    ob = cn_data.call("opt_basic", {"exchange": "SSE"},
                      "ts_code,name,call_put,exercise_price,maturity_date,delist_date")
    m = pd.DataFrame(ob)
    m = m[m["name"].str.contains("50ETF", na=False) & ~m["name"].str.contains("科创", na=False)].copy()
    m["exercise_price"] = pd.to_numeric(m["exercise_price"], errors="coerce")
    m["maturity"] = pd.to_datetime(m["maturity_date"])
    _state["meta"] = m[["ts_code", "call_put", "exercise_price", "maturity"]]
    return _state["meta"]


def _qvix_on_date(meta, date):
    import numpy as np
    import pandas as pd
    from . import cn_data
    od = cn_data.call("opt_daily", {"trade_date": date, "exchange": "SSE"},
                      "ts_code,trade_date,close,settle")
    if not od:
        return None
    df = pd.DataFrame(od)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["settle"] = pd.to_numeric(df["settle"], errors="coerce")
    df["price"] = df["settle"].where(df["settle"] > 0, df["close"])
    df = df.merge(meta, on="ts_code")
    d = pd.Timestamp(date)
    df = df[(df["price"] > 0) & (df["maturity"] > d)]
    df["T"] = (df["maturity"] - d).dt.days / 365.0
    exps = sorted(e for e in df["T"].unique() if e * 365 >= 7)
    if len(exps) < 2:
        return None
    T1, T2 = exps[0], exps[1]
    s1, F1, p1 = _sigma_for_expiry(df[df["T"] == T1], T1)
    s2, F2, p2 = _sigma_for_expiry(df[df["T"] == T2], T2)
    if np.isnan(s1) or np.isnan(s2):
        return None
    t30 = 30 / 365.0
    interp = T1 < t30 < T2
    if t30 <= T1:
        var30 = s1 * s1
    elif t30 >= T2:
        var30 = s2 * s2
    else:
        V1, V2 = T1 * s1 * s1, T2 * s2 * s2
        var30 = (V1 + (V2 - V1) * (t30 - T1) / (T2 - T1)) / t30
    return {"date": d.strftime("%Y-%m-%d"), "qvix": round(100 * math.sqrt(max(var30, 0)), 2),
            "skew": round(float(_skew_for_expiry(p1, F1, T1)), 4),
            "near_days": round(T1 * 365), "next_days": round(T2 * 365), "interpolated": interp}


# ── incremental update + serve ───────────────────────────────────────────────

def _update():
    import pandas as pd
    with _lock:
        if _state["building"]:
            return
        _state["building"] = True
    try:
        df = pd.read_csv(_CACHE) if _CACHE.exists() else pd.DataFrame(columns=["date", "qvix", "skew"])
        have = set(df["date"].astype(str)) if not df.empty else set()
        last = pd.Timestamp(max(have)) if have else pd.Timestamp("2015-02-13")
        # missing Fridays from just after the last cached point up to today
        fridays = pd.date_range(last + pd.Timedelta(days=1), pd.Timestamp.today(), freq="W-FRI")
        if len(fridays) == 0:
            return
        meta = _meta()
        rows = []
        for f in fridays[-12:]:                      # cap per-run work
            r = _qvix_on_date(meta, f.strftime("%Y%m%d"))
            if r:
                rows.append(r)
            time.sleep(0.2)
        if rows:
            df = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
            df = df.drop_duplicates("date").sort_values("date")
            df.to_csv(_CACHE, index=False)
    except Exception:
        pass
    finally:
        _state["building"] = False


def ensure(background=True):
    if not _CACHE.exists():
        return
    fresh = (time.time() - _CACHE.stat().st_mtime) < _TTL
    if fresh:
        return
    if background:
        if not _state["building"]:
            threading.Thread(target=_update, daemon=True).start()
    else:
        _update()


def get_vix() -> dict:
    import pandas as pd
    if not _CACHE.exists():
        return {"status": "error", "error": "QVIX reference data not available."}
    ensure(background=True)
    df = pd.read_csv(_CACHE)
    if df.empty:
        return {"status": "error", "error": "Empty QVIX series."}
    df["date"] = df["date"].astype(str)
    last = df.iloc[-1]
    q = round(float(last["qvix"]), 1)
    sk = round(float(last["skew"]), 3)
    # skew sign = direction (puts bid = fear); qvix level = intensity (sign-blind)
    if sk > 0.02:
        skew_read = "puts bid → genuine fear / hedging demand"
    elif sk < -0.02:
        skew_read = "calls bid → euphoria / upside chasing"
    else:
        skew_read = "roughly balanced — no clear directional tilt"
    # level context vs its own history
    pct = round(float((df["qvix"] <= q).mean()) * 100)
    lvl = "elevated" if pct >= 70 else "low" if pct <= 30 else "middling"
    hist = [{"d": d, "q": round(float(qq), 1), "s": round(float(ss), 3)}
            for d, qq, ss in zip(df["date"], df["qvix"], df["skew"])]
    return {
        "status": "ready", "date": str(last["date"]),
        "qvix": q, "skew": sk, "skew_read": skew_read,
        "level_pct": pct, "level_word": lvl,
        "history": hist, "n": len(df),
        "note": "Standalone option-implied-vol reference — NOT part of the Fear & Greed composite. "
                "QVIX level is direction-blind (high = big moves either way; buying high A-share vol has "
                "historically lost). The SKEW is the directional fear read. Weekly (Fridays).",
    }

"""A-share institutional money flow — whole-market large/extra-large-order net
flow (主力资金, the standard CN order-size-bucket proxy for institutional-sized
trading), bucketed by sector.

A northbound (Stock Connect) leg was removed 2026-08-10: `moneyflow_hsgt`'s
north_money/south_money fields were NEVER negative across 300 sampled trading
days (May 2025-Aug 2026) — real net buying/selling flips sign on outflow days,
so this field no longer behaves like net flow. HKEX discontinued real-time
Stock Connect buy/sell disclosure in April 2024 (replaced with aggregate
turnover data); whatever Tushare now derives for this field, it isn't the net
institutional flow the old label claimed, independent of a separate 100x unit
bug that was also present (the field is documented in 百万元/millions, this
code multiplied by 万元/ten-thousands). Don't resurrect this leg without first
confirming what the field actually represents post-2024.

moneyflow (this file's remaining data source) is unaffected — it's the CN
exchanges' own order-size classification, not Stock Connect flow, and its
whole-market net genuinely flips sign day to day (verified against live data).

This is a single bulk Tushare call — moneyflow covers the WHOLE market at one
trade_date in one call — so this is a live read computed per-request, no
background cache needed.
"""
from __future__ import annotations


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_institutional_flow() -> dict:
    from . import cn_data

    # latest trade date from a liquid name (daily_basic is already known-reliable
    # for this — same pattern filter_screener.py uses)
    ref = cn_data.latest_row("daily_basic", "000001.SZ", "ts_code,trade_date")
    trade_date = ref.get("trade_date") if ref else None

    main_force = None
    retail_soi = None
    sector_flow: list[dict] = []
    if trade_date:
        try:
            # Same call covers institutional (large/extra-large) AND retail-skewed
            # (small) order buckets — CN-RET1 in the Indicators Pool doc (v3.0),
            # added here at zero extra cost since these rows are already fetched.
            rows = cn_data.call(
                "moneyflow", {"trade_date": trade_date},
                "ts_code,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,"
                "buy_sm_amount,sell_sm_amount",
            )
            buy = sum((_to_float(r.get("buy_lg_amount")) or 0) + (_to_float(r.get("buy_elg_amount")) or 0) for r in rows)
            sell = sum((_to_float(r.get("sell_lg_amount")) or 0) + (_to_float(r.get("sell_elg_amount")) or 0) for r in rows)
            main_force = {"net": buy - sell, "buy": buy, "sell": sell, "n_stocks": len(rows)}

            sm_buy = sum(_to_float(r.get("buy_sm_amount")) or 0 for r in rows)
            sm_sell = sum(_to_float(r.get("sell_sm_amount")) or 0 for r in rows)
            sm_total = sm_buy + sm_sell
            retail_soi = {
                "net": sm_buy - sm_sell, "buy": sm_buy, "sell": sm_sell,
                "soi": (sm_buy - sm_sell) / sm_total if sm_total else None,
                "n_stocks": len(rows),
            }

            basic = {b["ts_code"]: b for b in cn_data._stock_basic_list()}
            by_ind: dict[str, dict] = {}
            for r in rows:
                ind = (basic.get(r.get("ts_code")) or {}).get("industry") or "Unclassified"
                b_amt = (_to_float(r.get("buy_lg_amount")) or 0) + (_to_float(r.get("buy_elg_amount")) or 0)
                s_amt = (_to_float(r.get("sell_lg_amount")) or 0) + (_to_float(r.get("sell_elg_amount")) or 0)
                slot = by_ind.setdefault(ind, {"buy": 0.0, "sell": 0.0, "n": 0})
                slot["buy"] += b_amt
                slot["sell"] += s_amt
                slot["n"] += 1
            sector_flow = sorted(
                ({"industry": k, "net_yuan": (v["buy"] - v["sell"]) * 10_000,
                  "buy_yuan": v["buy"] * 10_000, "sell_yuan": v["sell"] * 10_000, "n_stocks": v["n"]}
                 for k, v in by_ind.items()),
                key=lambda x: -x["net_yuan"],
            )
        except Exception:
            main_force = None
            retail_soi = None
            sector_flow = []

    if main_force is None:
        return {"status": "error", "error": "Could not load institutional flow data."}

    return {
        "status": "ready",
        "date": trade_date,
        "main_force": {
            "net_yuan": main_force["net"] * 10_000,
            "buy_yuan": main_force["buy"] * 10_000,
            "sell_yuan": main_force["sell"] * 10_000,
            "n_stocks": main_force["n_stocks"],
        },
        # CN-RET1 (retail-skewed small-order imbalance) — NOT verified retail
        # identity (institutions split orders small too; this is a fixed-nominal
        # order-size-band proxy per Tushare's own small/medium/large/extra-large
        # classification, ≤¥5万 = "small"). Whole-market SOI = net small-order
        # flow ÷ gross small-order flow, range [-1, +1].
        "retail_soi": None if retail_soi is None else {
            "net_yuan": retail_soi["net"] * 10_000,
            "buy_yuan": retail_soi["buy"] * 10_000,
            "sell_yuan": retail_soi["sell"] * 10_000,
            "soi": round(retail_soi["soi"], 4) if retail_soi["soi"] is not None else None,
            "n_stocks": retail_soi["n_stocks"],
        },
        "sector_flow": sector_flow,
    }

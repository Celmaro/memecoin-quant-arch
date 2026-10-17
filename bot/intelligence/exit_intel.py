"""Liquidity & executable exit intelligence (doc 2 §3, P1 priority).

Computes the gap between displayed profit and REALIZABLE profit:
  - executable exit value  = net proceeds of selling the actual position now
  - exit capacity ratio    = position notional / available liquidity
  - liquidity deterioration= current vs entry liquidity
  - R_exit                 = (net exit proceeds - entry cost) / entry cost
Uses Jupiter quote for impact where possible, DexScreener mark otherwise.
"""
import os, sys, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import dex_pair

FEE_BPS = 1.7  # desk execution estimate (%.3f = ~1.7%)

def _jupiter_impact(mint, usd):
    """Best-effort price impact at a given USD size; None if unavailable."""
    try:
        from jupiter_estimator import quote
        q = quote(mint, usd=usd, slippage_bps=500)
        # audit P2 (Oct 18): NEVER treat the dexscreener fallback (hardcoded
        # 1.0 = 100% impact) as a real quote — that skews executable-exit math.
        if q and q.get("via") == "jupiter" and q.get("price_impact_pct") is not None:
            return q["price_impact_pct"]
    except Exception:
        pass
    return None

def exit_intel(mint, pos, liq=None, entry_liq=None, entry_usd=None, size_usd=None,
               entry_px=None, mark_px=None):
    """Position-aware executable exit analysis.

    pos: the position dict (entry_px, size/size_usd, entry_liq...)
    Returns: {executable_value, displayed_value, exit_capacity_ratio,
              liquidity_pct_of_entry, r_exit, sell_impact_pct} or None on no data.
    """
    pair = dex_pair(mint)
    if not pair:
        return None
    px = float(mark_px or pair.get("priceUsd") or 0)
    liq = float(liq if liq is not None else (pair.get("liquidity") or {}).get("usd") or 0)
    if px <= 0 or (liq <= 0):
        # zero liquidity -> nothing executable
        return {"executable_value": 0.0, "displayed_value": 0.0,
                "exit_capacity_ratio": None, "liquidity_pct_of_entry": 0.0,
                "r_exit": -1.0, "sell_impact_pct": None, "dead": True}

    ep = float(entry_px or pos.get("entry_px") or 0)
    sz = float(size_usd or pos.get("size") or pos.get("size_usd") or 0)
    if ep <= 0 or sz <= 0:
        return None
    qty = sz / ep                      # token quantity held
    displayed = qty * px               # mark-to-market gross value
    notional_usd = displayed

    # price impact of selling the actual position
    impact = _jupiter_impact(mint, notional_usd)
    if impact is None:
        # fallback heuristic: impact grows with position share of liquidity
        impact = min(50.0, 5.0 * notional_usd / liq) if liq > 0 else 50.0
    net = displayed * (1 - impact / 100) * (1 - FEE_BPS / 100)
    entry_cost = sz
    r_exit = (net - entry_cost) / entry_cost if entry_cost > 0 else -1.0
    entry_liq = float(entry_liq if entry_liq is not None else pos.get("entry_liq") or liq or 0)
    return {
        "executable_value": round(net, 2),
        "displayed_value": round(displayed, 2),
        "exit_capacity_ratio": round(notional_usd / liq, 4) if liq > 0 else None,
        "liquidity_pct_of_entry": round(100 * liq / entry_liq, 1) if entry_liq > 0 else None,
        "r_exit": round(r_exit, 3),
        "sell_impact_pct": round(impact, 2),
        "dead": bool(liq <= 0),
    }
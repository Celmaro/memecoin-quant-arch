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

def _jupiter_impact(mint, usd, run_decimals):
    """Best-effort SELL-side price impact at a given USD position size; None if unavailable.

    audit P1.6 (Oct 18): use a real token->SOL SELL quote (the actual exit direction),
    converting the USD position size into raw token units via `run_decimals`, instead of
    using a buy-route quote and treating it as sell-side impact."""
    try:
        from jupiter_estimator import quote_sell
        raw = usd / max(0.00000001, (run_decimals or 1))  # token amount in raw units
        q = quote_sell(mint, int(round(raw)) if raw > 1 else 1, slippage_bps=500)
        if q and q.get("via") == "jupiter":
            # impact_fraction is a decimal fraction (0.05 = 5%); return as PERCENT for
            # downstream display consistency (exit_intel multiplies by /100 below).
            return q["impact_fraction"] * 100.0
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

    # price impact of selling the actual position (P1.6: SELL route, real token qty)
    qty_raw = None
    try:
        from jupiter_estimator import token_decimals
        dec = token_decimals(mint)
        qty_raw = int(round(qty * (10 ** dec)))
    except Exception:
        qty_raw = None
    impact = _jupiter_impact(mint, notional_usd, qty_raw) if qty_raw else None
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
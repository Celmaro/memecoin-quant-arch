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

# 4th audit 5.2 (Oct 23): FEE_BPS used to be a hardcoded 1.7 while the paper
# EXECUTOR books PAPER_FEE_PCT=3.0 — exit-intel's executable-value estimate and
# the book's PnL disagreed by 1.3% on every exit. ONE fee source: the executor.
try:
    sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
    from bot.position_manager.scaleout import PAPER_FEE_PCT
    FEE_BPS = PAPER_FEE_PCT
except Exception:
    FEE_BPS = 3.0  # mirror scaleout.PAPER_FEE_PCT fallback

def _jupiter_impact(mint, qty_raw):
    """Best-effort SELL-side price impact for the ACTUAL raw token quantity held.

    FIX (Oct 22, ext audit P2): the OLD signature took (usd, run_decimals) and the
    call site passed qty_raw into the run_decimals slot — quote_sell got ~1 raw
    unit and impact read 0% on EVERY exit analysis. quote_sell expects raw units
    (decimals already applied), so pass qty_raw straight through.
    Returns impact as a PERCENT (0.05 = 5%); None if unavailable."""
    try:
        from jupiter_estimator import quote_sell
        q = quote_sell(mint, int(round(qty_raw)) if qty_raw and qty_raw > 1 else 1, slippage_bps=500)
        if q and q.get("via") == "jupiter":
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
    # Oct 21 fix: None liq = UNKNOWN (feed hasn't measured it yet) — must NOT
    # collapse to 0 and mark dead. Only CONFIRMED zero liquidity is dead.
    liq_raw = liq if liq is not None else (pair.get("liquidity") or {}).get("usd")
    liq = float(liq_raw) if liq_raw not in (None, "") else None
    if px <= 0 or (liq is not None and liq <= 0):
        # zero liquidity -> nothing executable
        return {"executable_value": 0.0, "displayed_value": 0.0,
                "exit_capacity_ratio": None, "liquidity_pct_of_entry": (0.0 if liq is not None else None),
                "r_exit": -1.0, "sell_impact_pct": None, "dead": liq is not None}

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
    impact = _jupiter_impact(mint, qty_raw) if qty_raw else None
    if impact is None:
        # fallback heuristic: impact grows with position share of liquidity
        impact = min(50.0, 5.0 * notional_usd / liq) if (liq is not None and liq > 0) else 50.0
    net = displayed * (1 - impact / 100) * (1 - FEE_BPS / 100)
    entry_cost = sz
    r_exit = (net - entry_cost) / entry_cost if entry_cost > 0 else -1.0
    entry_liq = float(entry_liq if entry_liq is not None else pos.get("entry_liq") or liq or 0)
    return {
        "executable_value": round(net, 2),
        "displayed_value": round(displayed, 2),
        "exit_capacity_ratio": round(notional_usd / liq, 4) if (liq is not None and liq > 0) else None,
        "liquidity_pct_of_entry": round(100 * liq / entry_liq, 1) if (entry_liq > 0 and liq is not None) else None,
        "r_exit": round(r_exit, 3),
        "sell_impact_pct": round(impact, 2),
        "dead": bool(liq is not None and liq <= 0),
    }
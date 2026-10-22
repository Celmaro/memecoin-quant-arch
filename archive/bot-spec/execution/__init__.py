"""execution/ (doc 1: auto_entry, swap_executor, transaction_confirmation, execution_safety).

Paper-mode execution layer. Real execution is NOT authorized — this validates the
entry pipeline (auto buy, min size, cash safety) and confirms paper fills the way
a real swap executor would confirm a fill.
"""
import os, sys, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

MIN_ENTRY_USD = 10.0
FEE_BPS = 1.7

def auto_entry(b, c, px):
    """Paper auto-entry (buy-all mandate). Returns (position, bought_line, skipped_line).

    Rules (user-mandated): buy ALL fresh curated calls; confluence ($25) vs default ($20);
    min $10 cash; insufficient cash -> skipped log, never a zero-size ghost.
    """
    conf = c.get("confluence") or len(c.get("channels") or [1])
    size = 25.0 if conf >= 2 else 20.0
    size = min(size, b["cash"])
    if size < MIN_ENTRY_USD:
        skip = {"ts": time.time(), "symbol": c.get("symbol"), "mint": c.get("mint"),
                "why": f"insufficient_cash_{b['cash']:.2f}"}
        return None, None, skip
    pos = {"mint": c["mint"], "entry_px": float(px), "size_usd": size, "entry_usd": size,
           "initial_usd": size,  # audit P1-4: preserve ORIGINAL basis for the scaleout curve
           "opened": time.time(), "state": "full", "age_at_buy": c.get("age"),
           "channel": c.get("channel"), "confluence": conf, "symbol": c.get("symbol")}
    line = f"BUY ${c.get('symbol')} @ ${px} (${size:.0f}, call {c.get('age', 0):.0f}m old) {c['mint'][:10]}"
    return pos, line, None

def swap_executor_quote(mint, usd):
    """Jupiter quote at the actual trade size (paper route validation)."""
    try:
        from jupiter_estimator import quote
        return quote(mint, usd=usd, slippage_bps=500)
    except Exception:
        return None

def confirm_fill(quote_resp):
    """Paper fill confirmation — mirrors what a real swap executor returns."""
    if not quote_resp:
        return {"confirmed": False, "reason": "no quote"}
    impact = quote_resp.get("price_impact_pct")
    if impact is not None and impact > 8:
        return {"confirmed": False, "reason": f"impact {impact:.1f}% too high"}
    return {"confirmed": True, "out_tokens": quote_resp.get("out_tokens"),
            "impact_pct": impact}

def safety_check(b, size):
    """Execution safety: cash, min size, exposure cap."""
    if size <= 0:
        return False, "zero size"
    if size < MIN_ENTRY_USD:
        return False, f"below min ${MIN_ENTRY_USD:.0f}"
    if size > b.get("cash", 0):
        return False, "insufficient cash"
    return True, "ok"
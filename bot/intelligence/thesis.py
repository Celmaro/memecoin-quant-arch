"""Thesis tracker (doc 2 §10) — per-position thesis with dimensions, so the bot
can explain "demand strong but liquidity risk rising" instead of one opaque score.

Dimensions: demand, holder_quality, supply_risk, liquidity, momentum,
            social_attention, market_context, execution_confidence
Each dim: -1 (bearish) .. +1 (bullish), plus a short evidence note.
"""
import os, sys, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from bot.intelligence.microstructure import microstructure
from bot.intelligence.lifecycle import STATES


def evaluate(mint, pos, signals, extra=None):
    """Build thesis dimensions from signals + microstructure + lifecycle."""
    dims = {}
    notes = {}
    s = " ".join(signals).lower()
    extra = extra or {}

    # Demand
    d = 0.0
    if "new money" in s or "fomo_inflow" in s or "buy imbalance" in s: d += 0.5
    if "volume_drying" in s or "inflow dried" in s or "sell imbalance" in s: d -= 0.6
    dims["demand"] = d
    notes["demand"] = "fresh wallets buying" if d > 0 else ("inflow fading" if d < 0 else "flat")

    # Holder quality (retention + wallet distribution)
    hq = 0.0
    ret = extra.get("retention")
    if ret is not None:
        hq += 0.7 if ret >= 0.9 else (-0.6 if ret < 0.6 else 0.0)
    if "good_wallet_selling" in s: hq -= 0.7
    dims["holder_quality"] = round(hq, 2)
    notes["holder_quality"] = f"retention {ret*100:.0f}%" if ret is not None else "unknown"

    # Supply risk
    sr = 0.0
    if "wash" in s or "fragility" in s or "bundle" in s or "insider" in s or "rugger" in s: sr -= 0.7
    if "top10" in s or "concentration" in s: sr -= 0.4
    dims["supply_risk"] = sr
    notes["supply_risk"] = "wash/fragile" if sr < 0 else "clean"

    # Liquidity
    liq = 0.0
    liq_pct = extra.get("liq_pct_of_entry")
    if extra.get("dead"): liq = -1.0
    elif liq_pct is not None: liq = 0.6 if liq_pct >= 80 else (-0.8 if liq_pct < 50 else 0.0)
    if "liq_collapse" in s or "liq=0" in s: liq = -1.0
    dims["liquidity"] = liq
    notes["liquidity"] = f"{liq_pct:.0f}% of entry" if liq_pct is not None else "unknown"

    # Momentum
    mom = 0.0
    mult = extra.get("mult")
    if mult is not None:
        mom = 0.6 if mult >= 1.2 else (-0.5 if mult <= 0.9 else 0.0)
    if "climax" in s or "divergence" in s: mom -= 0.3
    dims["momentum"] = round(mom, 2)
    notes["momentum"] = f"{mult:.2f}x from entry" if mult is not None else "unknown"

    # Social attention (proxy: channel/follow-up mentions vs participation)
    soc = 0.0
    if "followup" in s or "confluence" in s: soc += 0.3
    if "attention" in s: soc += 0.2
    dims["social_attention"] = soc
    notes["social_attention"] = "follow-ups/confluence active" if soc else "quiet"

    # Market context (SOL regime — simplified; full regime needs more data)
    ctx = 0.0
    sol = extra.get("sol_change")
    if sol is not None:
        ctx = 0.3 if sol > 1 else (-0.3 if sol < -1 else 0.0)
    dims["market_context"] = ctx
    notes["market_context"] = f"SOL {sol:+.1f}%" if sol is not None else "neutral"

    # Execution confidence
    ec = 0.5
    if extra.get("exit_capacity_ratio") is not None and extra["exit_capacity_ratio"] > 0.2: ec -= 0.4
    if extra.get("data_quality") == "low": ec -= 0.2
    dims["execution_confidence"] = round(ec, 2)
    notes["execution_confidence"] = "thin vs position" if ec < 0.3 else "ok"

    overall = round(sum(dims.values()) / len(dims), 2) if dims else 0.0
    return {"dims": dims, "notes": notes, "overall": overall,
            "thesis_state": ("strengthening" if overall >= 0.25 else
                             "deteriorating" if overall <= -0.25 else "stable")}
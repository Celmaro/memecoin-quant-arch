"""Token lifecycle state machine (doc 2 §5) — every position gets an estimated
lifecycle state that changes with evidence, not just elapsed minutes.
States: DISCOVERY, EXPANSION, BROAD_MOMENTUM, DISTRIBUTION_RISK, EXHAUSTION, COLLAPSE.
"""
import time

STATES = ("DISCOVERY", "EXPANSION", "BROAD_MOMENTUM", "DISTRIBUTION_RISK", "EXHAUSTION", "COLLAPSE")

def classify(age_min, holder_retention=None, fresh_buyers=None, unique_buyers=None,
             liq=None, entry_liq=None, vol_h1=None, entry_vol=None, mult=None, wash=False):
    """Return (state, reasons). Evidence-driven, not time-only."""
    reasons = []
    if age_min < 15:
        reasons.append("early")
    # holder-side evidence
    if holder_retention is not None and holder_retention < 0.6:
        reasons.append("holders leaving")
    elif holder_retention is not None and holder_retention >= 0.9:
        reasons.append("holders staying")
    if fresh_buyers is not None and fresh_buyers <= 1 and age_min > 30:
        reasons.append("no fresh buyers")
    elif fresh_buyers is not None and fresh_buyers >= 5:
        reasons.append("fresh wallets arriving")
    # liquidity-side evidence
    liq_bad = (entry_liq or 0) > 0 and (liq or 0) < (entry_liq or 0) * 0.5
    if liq_bad:
        reasons.append("liquidity halved")
    if (liq or 0) <= 0:
        reasons.append("zero liquidity")
    # volume-side evidence
    vol_bad = (entry_vol or 0) > 0 and (vol_h1 or 0) < (entry_vol or 0) * 0.4
    if vol_bad:
        reasons.append("volume dried")
    # price-side evidence
    if mult is not None and mult <= 0.85:
        reasons.append("price below entry")
    elif mult is not None and mult >= 1.3:
        reasons.append("big green run")
    if wash:
        reasons.append("wash trading")

    # decide state
    if liq_bad or (liq or 0) <= 0 or mult is not None and mult <= 0.7:
        return "COLLAPSE", reasons
    if vol_bad and (holder_retention is None or holder_retention < 0.6):
        return "EXHAUSTION", reasons
    if (fresh_buyers is not None and fresh_buyers <= 1 and age_min > 30) or \
       (holder_retention is not None and holder_retention < 0.6):
        return "DISTRIBUTION_RISK", reasons
    if age_min < 15 and not vol_bad:
        return "DISCOVERY", reasons
    if fresh_buyers is not None and fresh_buyers >= 3 and vol_h1 and (entry_vol or 0) and vol_h1 >= entry_vol:
        return "BROAD_MOMENTUM", reasons
    if vol_h1 and (entry_vol or 0) and vol_h1 >= entry_vol * 0.7:
        return "EXPANSION", reasons
    return "DISCOVERY", reasons

def transition(current, age_min, **kw):
    """Return the next state + whether it changed, so callers can alert on changes."""
    nxt, reasons = classify(age_min, **kw)
    changed = nxt != current
    return nxt, reasons, changed
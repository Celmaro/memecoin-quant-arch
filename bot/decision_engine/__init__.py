"""Decision engine (doc 1: decision_engine/) — evidence aggregation, thesis tracking,
risk engine and hold/add/scale-out/exit policies. Produces the doc-1 decision contract:
{position_id, action, sell_fraction, confidence, reason_codes, evidence_timestamp, ...}
"""
import time, json, os, sys
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ACTION_HOLD = "HOLD"
ACTION_ADD = "ADD"
ACTION_SCALE_OUT = "SCALE_OUT"
ACTION_FULL_EXIT = "FULL_EXIT"
ACTION_EMERGENCY = "EMERGENCY_EXIT"

# Doc-2 thesis dimensions
THESIS_DIMS = ("demand", "holder_quality", "supply_risk", "liquidity",
               "momentum", "social_attention", "market_context", "execution_confidence")


def _evidences(signals):
    """Split engine signals into evidence_for / evidence_against by keyword."""
    pos_kw = ["fresh buyer", "new money", "retention", "fomo", "buoy", "buy imbalance",
              "smart money", "conviction", "strong", "inflow", "accumulation"]
    neg_kw = ["dried", "dumping", "sell", "collapse", "exit", "stop", "churn", "washing",
              "distribut", "declining", "liquidity", "failed", "warning", "climax", "divergence"]
    for_h = [s for s in signals if any(k in s.lower() for k in pos_kw)]
    ag_h = [s for s in signals if any(k in s.lower() for k in neg_kw)]
    return for_h[:4], ag_h[:4]


def build_contract(mint, pos, signals, action, sell_fraction, confidence,
                   thesis_state, reason_codes, extra=None):
    """Assemble and persist a doc-1 decision contract."""
    ev_for, ev_ag = _evidences(signals)
    contract = {
        "position_id": f"pos_{pos.get('ts', time.time()):.0f}_{mint[:8]}",
        "action": action,
        "sell_fraction": sell_fraction,
        "confidence": round(confidence, 2),
        "reason_codes": reason_codes,
        "evidence_timestamp": time.time(),
        "thesis_state": thesis_state,
        "evidence_for": ev_for,
        "evidence_against": ev_ag,
        "data_quality": "high" if signals else "low",
        "reason": "; ".join(ev_ag[-2:]) or "no adverse evidence",
        "checks": extra or {},
    }
    return contract


def risk_score(signals, action):
    """Doc-2 risk engine: 0 (low) to 100 (critical) from signal weight."""
    weight = {
        "LIQ_COLLAPSE": 30, "LIQ=0": 40, "EMERGENCY": 35, "STOP": 30, "EXIT_ALL": 35,
        "CLIP": 10, "CLIP_50": 12, "CLIP_100": 20, "HOLDER INFLOW DRIED": 18,
        "HOLDER CHURN": 18, "RETENTION": 12, "GOOD_WALLET_SELLING": 22,
        "WASH/LPI": 15, "FRAGILITY": 15, "SLOW-GRAD": 8, "VOLUME_DRYING": 18,
        "VOLUME_CLIMAX_STALL": 14, "ROUND_STALL": 10, "DIVERGENCE": 12,
    }
    base = {"HOLD": 5, "ADD": 8, "SCALE_OUT": 25, "FULL_EXIT": 45, "EMERGENCY_EXIT": 80}.get(action, 20)
    added = 0
    for s in signals:
        for k, w in weight.items():
            if k in s:
                added += w
                break
    return min(100, base + added)


def decide(mint, pos, signals, mc, liq, mult, age_min, extra=None):
    """Doc-1 decision engine entry: maps monitor signals -> policy action + contract.

    Policies (doc 2 §10 thesis engine + doc 1 §4 lifecycle):
      - HOLD: thesis acceptable, no adverse evidence
      - ADD: fresh money + rising participation + remaining cash headroom (exposure limit)
      - SCALE_OUT: partial profit taking / adverse evidence accumulating
      - FULL_EXIT: thesis broken / stop / liquidity gone
      - EMERGENCY_EXIT: critical event (liq collapse, authority change, sell avalanche)
    """
    # thesis state from evidence balance
    ev_for, ev_ag = _evidences(signals)
    net = len(ev_for) - len(ev_ag)
    if not signals:
        thesis_state = "stable"
    elif net >= 2:
        thesis_state = "strengthening"
    elif net <= -1:
        thesis_state = "deteriorating"
    else:
        thesis_state = "stable"
    # Build 2: TTL-decayed evidence override (spec §3) — weighted for/against
    ttl = (extra or {}).get("ttl")
    if ttl:
        wfor, wag = ttl[0], ttl[1]
        if wag > wfor + 0.5:
            thesis_state = "deteriorating"
        elif wfor > wag + 0.5:
            thesis_state = "strengthening"

    # exposure limit (doc 1 §4: ADD only when exposure limits allow)
    size = float(pos.get("size") or pos.get("size_usd") or 0)
    cash = float(extra.get("cash") or 0) if extra else 0
    exposure_ok = size + 10 <= (cash + size) * 1.0  # keep below all-in at any single add size

    # policy decisions (priority: emergency > exit > scale > add > hold)
    reason_codes = []
    action = ACTION_HOLD
    sell_fraction = 0.0
    confidence = 50.0

    em_triggered = [s for s in signals if any(k in s for k in ("LIQ_COLLAPSE", "LIQ=0", "EMERGENCY", "STOP", "EXIT_ALL"))]
    if em_triggered:
        action = ACTION_EMERGENCY
        sell_fraction = 1.0
        confidence = 92
        reason_codes = ["EMERGENCY_TRIGGERED"]
    elif any("FULL" in s or "EXIT" in s for s in signals):
        action = ACTION_FULL_EXIT
        sell_fraction = 1.0
        confidence = 80
        reason_codes = ["EXIT_POLICY"]
    elif any(("CLIP_100" in s or "CLIP2" in s) for s in signals):
        action = ACTION_SCALE_OUT
        sell_fraction = 1.0
        confidence = 72
        reason_codes = ["FULL_PROFIT_CLIP"]
    elif any(("CLIP_50" in s or "CLIP1" in s) for s in signals):
        action = ACTION_SCALE_OUT
        sell_fraction = 0.5
        confidence = 66
        reason_codes = ["HALF_PROFIT_CLIP"]
    elif ("ADD" in signals) or ("NEW MONEY" in str(signals) and exposure_ok and thesis_state != "deteriorating"):
        action = ACTION_ADD
        sell_fraction = 0.0
        confidence = 58
        reason_codes = ["FRESH_WALLET_INFLOW"]
    elif thesis_state == "deteriorating":
        action = ACTION_SCALE_OUT
        sell_fraction = 0.25
        confidence = 62
        reason_codes = ["THESIS_DETERIORATING"]
    else:
        action = ACTION_HOLD
        sell_fraction = 0.0
        confidence = 55
        reason_codes = ["THESIS_STABLE"] if signals else ["NO_SIGNALS"]

    return build_contract(mint, pos, signals, action, sell_fraction, confidence,
                          thesis_state, reason_codes, extra)
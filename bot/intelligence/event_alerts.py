"""Event-driven forensic alerts (doc 2 §9) — structured events with severity,
so the decision engine gets event evidence instead of isolated indicator alerts.
"""
import time


def _sev(level): return {"sev": level, "ts": time.time()}

def large_holder_distribution(wallet_signal):
    """A significant holder or cluster transferring toward an apparent sale."""
    if not wallet_signal:
        return None
    if wallet_signal.get("signal") == "DISTRIBUTION":
        return {"type": "LARGE_HOLDER_DISTRIBUTION", **_sev("high"),
                "detail": f"{wallet_signal['distributing']} of {wallet_signal['known_tracked']} tracked top holders distributing"}
    return None

def liquidity_withdrawal(exit_info):
    if not exit_info:
        return None
    if exit_info.get("dead") or (exit_info.get("liquidity_pct_of_entry") is not None and exit_info["liquidity_pct_of_entry"] < 50):
        return {"type": "LIQUIDITY_WITHDRAWAL", **_sev("critical"),
                "detail": f"liq {exit_info.get('liquidity_pct_of_entry')}% of entry"}
    return None

def abnormal_sell_pressure(micro):
    if not micro:
        return None
    if micro.get("cvd") is not None and micro["cvd"] < -0.4 and micro.get("n_sells", 0) > 10:
        return {"type": "ABNORMAL_SELL_PRESSURE", **_sev("high"),
                "detail": f"CVD {micro['cvd']:+.2f}, {micro['n_sells']} sells in sample"}
    return None

def buyer_quality_deterioration(micro, wallet_signal):
    if not micro or not wallet_signal:
        return None
    if micro.get("buy_concentration", 0) >= 0.6 and wallet_signal.get("signal") != "ACCUMULATION":
        return {"type": "BUYER_QUALITY_DETERIORATION", **_sev("medium"),
                "detail": f"buy concentration {micro['buy_concentration']:.2f} (few whales)"}
    return None

def profit_liq_divergence(exit_info, mult):
    if not exit_info or mult is None:
        return None
    if mult >= 1.1 and exit_info.get("exit_capacity_ratio") is not None and exit_info["exit_capacity_ratio"] > 0.3:
        return {"type": "PROFIT_LIQUIDITY_DIVERGENCE", **_sev("medium"),
                "detail": f"{mult:.2f}x profit but position is {exit_info['exit_capacity_ratio']*100:.0f}% of pool liq"}
    return None

def signal_reversal(followups):
    """Original signal source issued a correction/reversal (from signal_context)."""
    if not followups:
        return None
    for f in followups:
        txt = (f.get("text") or "").lower()
        if any(k in txt for k in ("exit", "dump", "sell", "stop", "rug", "avoid", "fake")):
            return {"type": "SIGNAL_REVERSAL", **_sev("high"),
                    "detail": f"{f.get('channel')}: {txt[:80]}"}
    return None

def detect_all(mint, wallet_signal=None, exit_info=None, micro=None, mult=None, followups=None):
    """Run all detectors; returns list of structured events.

    Each detector takes ONLY its declared params (audit F3C fix) — passing a
    uniform 5-arg tuple previously shifted arguments and silently TypeError'd.
    """
    evs = []
    calls = [
        (large_holder_distribution, (wallet_signal,)),
        (liquidity_withdrawal, (exit_info,)),
        (abnormal_sell_pressure, (micro,)),
        (buyer_quality_deterioration, (micro, wallet_signal)),
        (profit_liq_divergence, (exit_info, mult)),
        (signal_reversal, (followups,)),
    ]
    for fn, args in calls:
        try:
            e = fn(*args)
            if e: evs.append(e)
        except Exception:
            pass
    return evs
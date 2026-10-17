"""Evidence TTL decay (spec §3): observations carry half_life + expires_at.
w_eff(t) = 0 if expired else confidence0 * 2^(-(t - observed)/half_life)

Half-life table (doc 2018e70e §3):
  quote_executable   2s    hard-expiry 5s
  order_flow_5m      45s   180s
  cohort_wallet_sell 300s  1800s
  creator_history    86400s (life of position)
"""
import time

HALF_LIVES = {
    "quote_executable": (2, 5),
    "order_flow_5m": (45, 180),
    "cohort_wallet_sell": (300, 1800),
    "monitor_signal": (300, 1800),      # generic position_monitor signal
    "micro_cvd": (45, 180),
    "exit_capacity": (45, 180),
    "holder_growth": (300, 1800),
    "wallet_distribution": (300, 1800),
    "creator_history": (86400, 86400 * 7),
    "aegis_forensic": (86400, 86400 * 7),
    "signal_followup": (900, 3600),
}

def feature_of(signal_text):
    """Map a monitor signal string to a decay class by keyword."""
    s = signal_text.lower()
    if "liq" in s or "exit capacity" in s:
        return "exit_capacity"
    if "cvd" in s or "micro" in s or "imbalance" in s or "divergence" in s:
        return "micro_cvd"
    if "holder" in s or "retention" in s or "churn" in s or "wallet dist" in s or "good_wallet" in s:
        return "holder_growth" if "holder" in s else "wallet_distribution"
    if "new money" in s or "fresh" in s:
        return "holder_growth"
    if "creator" in s or "authority" in s or "rug" in s or "wash" in s or "behavior" in s:
        return "aegis_forensic"
    return "monitor_signal"

def w_eff(feature, observed_ts, confidence0=1.0, now=None):
    """Effective weight of an observation. Expired -> 0."""
    half, expires = HALF_LIVES.get(feature, (300, 1800))
    now = now or time.time()
    age = now - observed_ts
    if age >= expires:
        return 0.0
    return confidence0 * (2 ** (-age / half))

def weight_signals(signals_with_ts, now=None):
    """signals_with_ts: list of (signal_text, observed_ts). Returns
    (weighted_for, weighted_against, details) using the decision engine's
    positive/negative keyword split with decay applied."""
    try:
        from bot.decision_engine import _evidences
    except Exception:
        from decision_engine import _evidences
    now = now or time.time()
    # reuse keyword split per-signal to classify polarity
    wfor, wagainst, details = 0.0, 0.0, []
    for text, ts in signals_with_ts:
        pol_for, pol_against = _evidences([text])
        w = w_eff(feature_of(text), ts, 1.0, now)
        if pol_for:
            wfor += w
        elif pol_against:
            wagainst += w
        details.append({"signal": text[:60], "feature": feature_of(text),
                        "age_s": round(now - ts, 1), "weight": round(w, 3)})
    return round(wfor, 3), round(wagainst, 3), details


def historical_weight_signals(mint, current_signals, now=None):
    """Audit P1-5: decay uses REAL observation timestamps from evidence_events,
    not a fresh 'now' stamp per signal. Dedupes recurring signals (same text)
    so a persistent condition doesn't gain weight just because the monitor
    re-emits it each tick.

    Returns (weighted_for, weighted_against, details) with current_signals
    stamped at `now` (they are genuinely fresh) + unexpired historical rows
    weighted by their actual observed_at.
    """
    now = now or time.time()
    try:
        from bot.persistence import ensure_schema
        from desk_common import db
        ensure_schema()
        con = db()
        rows = con.execute(
            "SELECT signal_text, observed_at, confidence FROM evidence_events "
            "WHERE mint=? AND observed_at > ? ORDER BY observed_at DESC",
            (mint, now - 3600)).fetchall()
        con.close()
    except Exception:
        rows = []
    # Dedupe: keep the most recent row per identical signal text
    latest = {}
    for r in rows:
        txt = r["signal_text"] or ""
        if txt and txt not in latest:
            latest[txt] = (float(r["observed_at"]), float(r["confidence"] or 1.0))
    signals_with_ts = [(s, now) for s in current_signals]
    for txt, (ts, conf) in latest.items():
        signals_with_ts.append((txt, ts))
    # collapse duplicate current vs historical same-text (current wins)
    seen = {}
    for txt, ts in signals_with_ts:
        seen[txt] = ts
    merged = list(seen.items())
    # weight with per-row confidence from evidence rows
    try:
        from bot.decision_engine import _evidences
    except Exception:
        from decision_engine import _evidences
    wfor, wagainst, details = 0.0, 0.0, []
    for txt, ts in merged:
        pol_for, pol_against = _evidences([txt])
        conf = latest.get(txt, (0, 1.0))[1] if txt in latest else 1.0
        w = w_eff(feature_of(txt), ts, conf, now)
        if pol_for:
            wfor += w
        elif pol_against:
            wagainst += w
        details.append({"signal": txt[:60], "feature": feature_of(txt),
                        "age_s": round(now - ts, 1), "weight": round(w, 3),
                        "historical": txt in latest})
    return round(wfor, 3), round(wagainst, 3), details
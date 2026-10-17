"""Adaptive monitoring tiers (spec §3): CRITICAL / HIGH / NORMAL / BACKGROUND.

Each open position sits in one band; poll cadence and escalation follow the
doc's promotion/demotion triggers. Used by desk_tick/paper_tick to decide how
deep to analyze per tick (budget), not to skip positions.
"""

TIERS = ("CRITICAL", "HIGH", "NORMAL", "BACKGROUND")
CADENCE_S = {"CRITICAL": 15, "HIGH": 60, "NORMAL": 300, "BACKGROUND": 900}

def classify(pos, r=None, now=None):
    """Return tier for a position. r = last analyze() result (optional)."""
    import time as _t
    now = now or _t.time()
    age_min = (now - float(pos.get("opened") or pos.get("ts") or now)) / 60
    # promotion triggers
    if age_min <= 5:
        return "CRITICAL"
    mult = (r or {}).get("mult")
    if mult is not None:
        # within 5% of stop (-15%) or clip (+15%) bands -> critical
        if mult <= 0.8975 or (1.1425 <= mult <= 1.1575) or mult >= 1.40:
            return "CRITICAL"
    if (r or {}).get("signals"):
        s = " ".join((r or {}).get("signals")).lower()
        if "liq" in s or "emergency" in s or "stop" in s:
            return "CRITICAL"
        if "volume" in s or "climax" in s or "dried" in s:
            return "HIGH"
    if age_min <= 10:
        return "HIGH"
    remaining_frac = 1.0
    initial = float(pos.get("initial_usd") or pos.get("entry_usd") or 0)
    if initial:
        remaining_frac = float(pos.get("entry_usd") or 0) / initial
    if remaining_frac <= 0.25 and (mult is None or mult >= 1.0):
        return "BACKGROUND"   # moonbag, basis recovered (or no live mult)
    return "NORMAL"

def cadence_for(pos, r=None):
    return CADENCE_S[classify(pos, r)]

def analyze_budget(positions, results=None, max_full=4):
    """Choose which positions get full analyze() this tick: CRITICAL/HIGH always,
    NORMAL if budget remains, BACKGROUND at most 1 per 10 ticks."""
    order = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "BACKGROUND": 3}
    items = []
    for mint, pos in positions.items():
        t = classify(pos, (results or {}).get(mint))
        items.append((order[t], mint, t))
    items.sort()
    chosen, skipped = [], []
    for i, (_, mint, t) in enumerate(items):
        if t in ("CRITICAL", "HIGH") or i < max_full:
            chosen.append((mint, t))
        else:
            skipped.append((mint, t))
    return chosen, skipped
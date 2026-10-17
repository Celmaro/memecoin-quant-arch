"""Scale-out engine (doc 93738f99 §6 / spec §6): target-remaining inventory curve.

Stages are defined as TARGET REMAINING RATIO of ORIGINAL inventory (self-healing):
  R0=1.0, R1=0.50 (clip1), R2=0.25 (clip2), R3=0.0 (final exit).
For stage k: TargetAtoms = initial * R_k
             sell_delta = max(0, current - reserved - target)
If a previous partial fill or manual reduction drifted the position, the next
stage automatically corrects onto the curve instead of compounding the error.
"""
import os, sys, time, json
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

STAGES = {  # stage -> (target_remaining_ratio, default_trigger_mult)
    1: (0.50, 1.15),
    2: (0.25, 1.45),
    3: (0.00, 1.80),
}

def load_book():
    return json.load(open(os.path.expanduser("~/memecoin-quant/paper_book.json")))

def save_book(b):
    import tempfile
    p = os.path.expanduser("~/memecoin-quant/paper_book.json")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=1); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, p)

def target_remaining(book, sym, stage):
    """Target remaining USD basis (of original entry) after stage k."""
    p = book["positions"].get(sym)
    if not p:
        return None
    initial = float(p.get("initial_usd") or p.get("entry_usd") or 0)
    r = STAGES.get(stage, (0.0,))[0]
    return initial * r

def sell_delta(book, sym, stage):
    """Self-healing atoms (USD basis) to sell to land on stage-k curve.
    Returns max(0, current - reserved - target)."""
    p = book["positions"].get(sym)
    if not p:
        return 0.0
    current = float(p.get("entry_usd") or p.get("size_usd") or 0)
    reserved = float(p.get("reserved_exit_usd") or 0)
    tgt = target_remaining(book, sym, stage)
    return max(0.0, current - reserved - tgt)

def reserve(book, sym, usd, intent_key=""):
    """Doc §1 reserved_exit_atoms analog: lock inventory for an in-flight sell.
    Prevents double execution when two tickers act on the same signal."""
    p = book["positions"].get(sym)
    if not p:
        return False
    current = float(p.get("entry_usd") or 0)
    reserved = float(p.get("reserved_exit_usd") or 0)
    if usd <= 0 or usd > current - reserved:
        return False
    p["reserved_exit_usd"] = round(reserved + usd, 4)
    p.setdefault("reservations", []).append({"ts": time.time(), "usd": usd, "key": intent_key})
    save_book(book)
    return True

def release(book, sym, usd):
    """Release a reservation (after fill reconciled or intent expired)."""
    p = book["positions"].get(sym)
    if not p:
        return
    p["reserved_exit_usd"] = round(max(0.0, float(p.get("reserved_exit_usd") or 0) - usd), 4)
    save_book(book)

def execute_stage(book, sym, stage, mult, reason="", how="stage"):
    """Sell exactly enough to land on the stage-k target. Returns trade record or None.
    Marks stage done so later ticks never re-sell the same stage."""
    p = book["positions"].get(sym)
    if not p:
        return None
    done = set(p.get("stages_done") or [])
    if stage in done:
        return None
    delta = sell_delta(book, sym, stage)
    if delta <= 0 and float(p.get("reserved_exit_usd") or 0) > 0:
        # audit P1 (Oct 18): only consume THIS intent's reservation, never the
        # whole reserved pool (other intents may have reserved inventory for a
        # different sell). Match by intent_key `{sym}:{how}{stage}`.
        intent_key = f"{sym}:{how}{stage}"
        mine = [r for r in (p.get("reservations") or []) if r.get("key") == intent_key]
        if mine:
            delta = sum(float(r.get("usd") or 0) for r in mine)
        else:
            delta = 0.0
    if delta <= 0:
        p.setdefault("stages_done", []).append(stage)
        save_book(book)
        return None
    proceeds = delta * mult
    pnl = proceeds - delta
    b = book
    b["cash"] = round(b.get("cash", 0) + proceeds, 2)
    b["realized"] = round(b.get("realized", 0) + pnl, 2)
    p["entry_usd"] = round(float(p["entry_usd"]) - delta, 4)
    p["size_usd"] = p["entry_usd"]
    p["reserved_exit_usd"] = 0.0
    p.setdefault("stages_done", []).append(stage)
    # audit P0-2 (Oct 18): after stage-1 partial, the book position MUST become
    # "half" so the `state == "half"` bracket branch (TP2/exit) can ever run.
    # Previously state stayed "full" forever -> stage-2 conditions unreachable.
    if stage == 1 and not p.get("state") == "half":
        p["state"] = "half"
    closed = float(p["entry_usd"]) < 0.01 or stage == 3
    if closed:
        b["positions"].pop(sym, None)
    b.setdefault("trades", []).append({"ts": time.time(), "symbol": sym, "mint": p.get("mint"),
        "mult": round(mult, 4), "sold_usd": round(delta, 2), "pnl": round(pnl, 2),
        "how": f"{how}{stage}", "reason": reason, "closed": closed})
    save_book(b)
    return {"sold_usd": round(delta, 2), "pnl": round(pnl, 2), "closed": closed,
            "stage": stage, "remaining": 0.0 if closed else p["entry_usd"]}

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

# Doc1-1 (Blix parity): paper execution cost — 3%/side, same as blixtrade brain.js.
# Doc1-4 (Blix parity): below this exit liquidity a position is UNSELLABLE and any
# exit is scored as a total loss (matches blixtrade brain.js minExitLiq + illiq flag).
PAPER_FEE_PCT = 3.0
PAPER_COST = (1 - PAPER_FEE_PCT / 100) / (1 + PAPER_FEE_PCT / 100)  # ≈ 0.943
MIN_EXIT_LIQ_USD = 1000.0
# Oct 19 phantom-loss fix: liq == 0 is an API miss (DexScreener 429 / fallback
# empty), NOT proof the token is dead. Before booking a total loss we:
#   - retry dex_pair up to 2x (Fix 1)
#   - require 2 CONSECUTIVE confirmed-low reads (or a rugcheck flag) before
#     UNSELLABLE is booked (Fix 2) — a single 0 never writes off a position.
LIQ_RETRIES = 2
LIQ_CONFIRM_MIN = 2


def _resolve_exit_liq(mint, liq, p=None):
    """Resolve exit liquidity with retries. Returns (resolved_liq, low_read_count).

    - Treats liq in (None, <=0) as UNKNOWN and re-reads via dex_pair up to
      LIQ_RETRIES more times (each read 1.1s+ throttle; bounded).
    - returns the MAX observed (a single provider miss shouldn't count), plus
      how many of the reads were confirmed < MIN_EXIT_LIQ_USD.
    """
    # liq=None: the CALLER didn't provide data (normal TP/CLIP exits) —
    # do not block them; no retry, no low-count. Only an explicit liq<=0
    # (a real lookup returned zero/missing) enters the retry path.
    if liq is None:
        return None, 0
    reads = []
    if liq > 0:
        reads.append(float(liq))
    else:
        try:
            from desk_common import dex_pair
            for _ in range(LIQ_RETRIES):
                mkt = dex_pair(mint, allow_fallback=True, timeout=10, retries=1) or {}
                v = float(((mkt.get("liquidity") or {}).get("usd")) or 0)
                reads.append(v)
                if v > 0:
                    break
        except Exception:
            pass
    if not reads:
        return 0.0, 0
    resolved = max(reads)
    low = sum(1 for v in reads if 0 < v < MIN_EXIT_LIQ_USD)
    return resolved, low


def _rugcheck_confirms_dead(mint):
    """True only when rugcheck itself returns a rugged/danger verdict — the
    fast-path that may still book UNSELLABLE on the first low read."""
    try:
        from desk_common import rugcheck
        rep = rugcheck(mint) or {}
        if not rep:
            return False
        if rep.get("danger") is True or rep.get("risk") in ("danger", "critical"):
            return True
        risks = " ".join(str(r.get("name") or "") for r in (rep.get("risks") or []))
        if any(k in risks.lower() for k in ("rugpull", "mint authority", "freeze authority", "liquidity removed")):
            return True
    except Exception:
        pass
    return False


# Fix 4 (Oct 19): never book an emergency exit worse than -28% when the
# on-chain tape price is >= the paper's own mult. Uses the PumpAPI replay
# archive when the exit hour is within reach; falls back to a DexScreener
# re-read otherwise. Returns the max confirmed on-chain multiple or None.
MAX_BOOKED_DUMP_MULT = 0.72  # 1 - 0.28 (the loosest stop tier)


def _onchain_mult(mint, mult, entry_px=None):
    """Best-effort on-chain price sanity check for an emergency exit.

    If the tape shows the token trading >= MAX_BOOKED_DUMP_MULT of the paper's
    entry at exit time, the emergency exit's fill is capped at that level
    instead of a deeper phantom dump. Returns (capped_mult, source) where
    capped_mult is None when no evidence (then caller keeps the raw mult).
    """
    try:
        import urllib.request, json as _j
        from desk_common import dex_pair
        # quick DexScreener cross-read (cheap, no tape download)
        mkt = dex_pair(mint, allow_fallback=True, timeout=8, retries=1) or {}
        px = mkt.get("priceUsd")
        if px and entry_px:
            px = float(px)
            if px > 0 and entry_px > 0:
                live_mult = px / float(entry_px)
                if live_mult >= MAX_BOOKED_DUMP_MULT and live_mult < mult:
                    return live_mult, "dexscreener"
    except Exception:
        pass
    return None, None
# Doc2-7: don't sell dust tranches — a planned partial smaller than this collapses
# into the next tranche (fees eat small sells on Solana under congestion).
MIN_TRANCHE_USD = 2.5

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

def execute_stage(book, sym, stage, mult, reason="", how="stage", liq=None, fee_pct=None, min_tranche=None):
    """Sell exactly enough to land on the stage-k target. Returns trade record or None.
    Marks stage done so later ticks never re-sell the same stage.

    Doc1-1: proceeds carry PAPER_FEE_PCT (3%/side). Doc1-4: when `liq` < MIN_EXIT_LIQ_USD
    the sale is scored as a TOTAL LOSS (proceeds 0, illiq flag) — you cannot actually
    exit into dust. Doc2-7: partial tranches below MIN_TRANCHE_USD are skipped (fees
    would eat them) unless they close the position."""
    p = book["positions"].get(sym)
    if not p:
        return None
    done = set(p.get("stages_done") or [])
    if stage in done:
        # P2 (Oct 20): a "done" stage must still re-enter when there is sellable
        # basis — either unreserved overshoot (cur−resv−tgt > 0) OR this intent's
        # own reserved-but-unsold inventory (a stage-3 full exit sold only its
        # $80 of $100 while another intent held the residual). The old hard
        # `return None` stranded the residual forever.
        cur = float(p.get("entry_usd") or 0)
        resv = float(p.get("reserved_exit_usd") or 0)
        tgt = target_remaining(book, sym, stage) or 0
        intent_key = f"{sym}:{how}{stage}"
        mine = [r for r in (p.get("reservations") or []) if r.get("key") == intent_key]
        if (cur - resv - tgt <= 0.01) and not mine:
            return None
        # else fall through: sell_delta below recomputes the real delta
        # (matching reservation consumed via the delta<=0 branch)
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
        # P2 (Oct 20): only mark the stage done when there is NOTHING to sell even
        # ignoring reservations. A different intent's in-flight reservation makes
        # sell_delta negative (current - reserved - target) — marking done here
        # strands the residual (stage permanently done, $20 never closes).
        unreserved = float(p.get("entry_usd") or p.get("size_usd") or 0) - target_remaining(book, sym, stage)
        if unreserved <= 0.01:
            p.setdefault("stages_done", []).append(stage)
            save_book(book)
        return None
    fee_pct = fee_pct if fee_pct is not None else PAPER_FEE_PCT
    min_tranche = min_tranche if min_tranche is not None else MIN_TRANCHE_USD
    current = float(p["entry_usd"])
    # Doc2-7: skip dust partials unless this sale closes the position
    if delta < min_tranche and current - delta >= 0.01:
        save_book(book)  # leave stage undone; next tick can combine
        return None
    # Oct 19 phantom-loss fix: resolve liq with retries, require confirmation.
    # liq=None and liq==0.0 (unknown after retries) are NEVER write-off
    # triggers. Only confirmed low (0 < liq < $1000) twice, or rugcheck-dead,
    # may book UNSELLABLE.
    mint_for_liq = p.get("mint")
    resolved_liq, low_reads = _resolve_exit_liq(mint_for_liq, liq, p)
    confirmed_low = resolved_liq is not None and 0 < resolved_liq < MIN_EXIT_LIQ_USD
    if resolved_liq is None or resolved_liq >= MIN_EXIT_LIQ_USD:
        p["_low_liq_streak"] = 0
        proceeds = delta * mult * (1 - fee_pct / 100)
        pnl = proceeds - delta
        illiq = False
    else:
        # P3 (Oct 20): streak must count INDEPENDENT reads. Two executor calls in
        # one tick (decision then tier fallback) share the SAME snapshot — bumping
        # on both books UNSELLABLE from a single observation. Gate the bump: only
        # a read >=60s after the last counted read increments the streak.
        _now = time.time()
        last_bump = float(p.get("_low_liq_bump_ts") or 0)
        independent = (_now - last_bump) >= 60
        streak = int(p.get("_low_liq_streak") or 0)
        if confirmed_low and independent:
            streak += 1
            p["_low_liq_bump_ts"] = _now
        p["_low_liq_streak"] = streak
        rug_dead = _rugcheck_confirms_dead(mint_for_liq) if (confirmed_low or resolved_liq <= 0) else False
        if rug_dead or (confirmed_low and streak >= LIQ_CONFIRM_MIN):
            # Fix 2 (Oct 19, user-approved): LIVE reclassification — never book a
            # total loss against a positive tape. If the on-chain price is >= 1.0x
            # entry (coin UP), the low-liq read is stale/broken (wrong pool pair /
            # provider snapshot), so fill at the real mult instead of zero. This
            # replaces the manual reclassify_unsellable.py one-off.
            reclass_mult = None
            try:
                from desk_common import dex_pair as _dex_pair
                _m = _dex_pair(mint_for_liq, allow_fallback=True, timeout=8, retries=1) or {}
                _px = _m.get("priceUsd")
                _ep = float(p.get("entry_px") or 0)
                if _px and _ep > 0 and float(_px) > 0:
                    lm = float(_px) / _ep
                    if lm >= 1.0:
                        reclass_mult = lm
            except Exception:
                pass
            if reclass_mult is not None:
                proceeds = delta * reclass_mult * (1 - fee_pct / 100)
                pnl = proceeds - delta
                reason = reason + "+RECLASSIFIED_UP"
                illiq = False
                p["_low_liq_streak"] = 0
            else:
                proceeds = 0.0
                pnl = -delta
                reason = reason + "+UNSELLABLE"
                illiq = True
                p["_low_liq_streak"] = 0
        else:
            # First confirmed-low read or unknown(0): hold, never write off.
            save_book(book)
            return None
    b = book
    b["cash"] = round(b.get("cash", 0) + proceeds, 2)
    b["realized"] = round(b.get("realized", 0) + pnl, 2)
    p["entry_usd"] = round(current - delta, 4)
    p["size_usd"] = p["entry_usd"]
    # P2 (Oct 20): reconcile ONLY this intent's reservation(s) — zeroing the whole
    # reserved_exit_usd pool let a different intent's locked inventory vanish
    # without selling (stranded basis, stage marked done, residual can't close).
    intent_key = f"{sym}:{how}{stage}"
    kept = [r for r in (p.get("reservations") or []) if r.get("key") != intent_key]
    p["reservations"] = kept
    p["reserved_exit_usd"] = round(sum(float(r.get("usd") or 0) for r in kept), 4)
    p.setdefault("stages_done", []).append(stage)
    # audit P0-2 (Oct 18): after stage-1 partial, the book position MUST become
    # "half" so the `state == "half"` bracket branch (TP2/exit) can ever run.
    # Previously state stayed "full" forever -> stage-2 conditions unreachable.
    if stage == 1 and not p.get("state") == "half":
        p["state"] = "half"
    closed = float(p["entry_usd"]) < 0.01  # audit P0.4 (Oct 18): NEVER close on stage==3 alone — a
    # full-exit intent may have sold only its own reservation (e.g. $80 of $100) while another
    # intent reserved the rest; popping on stage==3 would DISCARD the residual basis + its cash.
    if closed:
        b["positions"].pop(sym, None)
    b.setdefault("trades", []).append({"ts": time.time(), "symbol": sym, "mint": p.get("mint"),
        "mult": round(mult, 4), "sold_usd": round(delta, 2), "pnl": round(pnl, 2),
        "how": f"{how}{stage}", "reason": reason, "closed": closed, "fee_pct": fee_pct,
        "illiq": illiq, "liq_at_exit": liq})
    save_book(b)
    return {"sold_usd": round(delta, 2), "pnl": round(pnl, 2), "closed": closed,
            "stage": stage, "remaining": 0.0 if closed else p["entry_usd"]}

def execute_fraction(book, sym, frac, mult, reason="", how="frac", liq=None, fee_pct=None, min_tranche=None):
    """audit P0.3 (Oct 18): honor an EXACT requested sell_fraction of the REMAINING basis
    (e.g. a thesis-deterioration SCALE_OUT of 0.25 sells 25% of what's left — the staged
    executor cannot express that, it only sells to stage-curve targets). Returns trade
    record or None. Marks state half when a partial remains, closed when basis depleted.

    Doc1-1/1-4 + Doc2-7: same fee / unsellable-total-loss / dust-tranche rules as execute_stage."""
    p = book["positions"].get(sym)
    if not p or frac <= 0:
        return None
    current = float(p.get("entry_usd") or 0)
    reserved = float(p.get("reserved_exit_usd") or 0)
    sellable = max(0.0, current - reserved)
    delta = min(sellable, current * frac)
    if delta <= 0.001:
        return None
    fee_pct = fee_pct if fee_pct is not None else PAPER_FEE_PCT
    min_tranche = min_tranche if min_tranche is not None else MIN_TRANCHE_USD
    # Doc2-7: skip dust partials unless this sale closes the position
    if delta < min_tranche and current - delta >= 0.01:
        return None
    # Oct 19 phantom-loss fix (same as execute_stage): resolve liq w/ retries.
    # liq=None / unknown(0.0) never writes off; only confirmed low twice
    # (or rugcheck-dead) books UNSELLABLE.
    mint_for_liq = p.get("mint")
    resolved_liq, low_reads = _resolve_exit_liq(mint_for_liq, liq, p)
    confirmed_low = resolved_liq is not None and 0 < resolved_liq < MIN_EXIT_LIQ_USD
    if resolved_liq is None or resolved_liq >= MIN_EXIT_LIQ_USD:
        p["_low_liq_streak"] = 0
        proceeds = delta * mult * (1 - fee_pct / 100)
        pnl = proceeds - delta
        illiq = False
    else:
        # P3 (Oct 20): streak must count INDEPENDENT reads. Two executor calls in
        # one tick (decision then tier fallback) share the SAME snapshot — bumping
        # on both books UNSELLABLE from a single observation. Gate the bump: only
        # a read >=60s after the last counted read increments the streak.
        _now = time.time()
        last_bump = float(p.get("_low_liq_bump_ts") or 0)
        independent = (_now - last_bump) >= 60
        streak = int(p.get("_low_liq_streak") or 0)
        if confirmed_low and independent:
            streak += 1
            p["_low_liq_bump_ts"] = _now
        p["_low_liq_streak"] = streak
        rug_dead = _rugcheck_confirms_dead(mint_for_liq) if (confirmed_low or resolved_liq <= 0) else False
        if rug_dead or (confirmed_low and streak >= LIQ_CONFIRM_MIN):
            # Fix 2 (Oct 19, user-approved): LIVE reclassification — never book a
            # total loss against a positive tape. If the on-chain price is >= 1.0x
            # entry (coin UP), the low-liq read is stale/broken (wrong pool pair /
            # provider snapshot), so fill at the real mult instead of zero. This
            # replaces the manual reclassify_unsellable.py one-off.
            reclass_mult = None
            try:
                from desk_common import dex_pair as _dex_pair
                _m = _dex_pair(mint_for_liq, allow_fallback=True, timeout=8, retries=1) or {}
                _px = _m.get("priceUsd")
                _ep = float(p.get("entry_px") or 0)
                if _px and _ep > 0 and float(_px) > 0:
                    lm = float(_px) / _ep
                    if lm >= 1.0:
                        reclass_mult = lm
            except Exception:
                pass
            if reclass_mult is not None:
                proceeds = delta * reclass_mult * (1 - fee_pct / 100)
                pnl = proceeds - delta
                reason = reason + "+RECLASSIFIED_UP"
                illiq = False
                p["_low_liq_streak"] = 0
            else:
                proceeds = 0.0
                pnl = -delta
                reason = reason + "+UNSELLABLE"
                illiq = True
                p["_low_liq_streak"] = 0
        else:
            save_book(book)
            return None
    b = book
    b["cash"] = round(b.get("cash", 0) + proceeds, 2)
    b["realized"] = round(b.get("realized", 0) + pnl, 2)
    p["entry_usd"] = round(current - delta, 4)
    p["size_usd"] = p["entry_usd"]
    # P2 (Oct 20): same per-intent reconciliation as execute_stage — never zero the
    # whole reserved pool (other intents' locked inventory would vanish unsold).
    fkey = f"{sym}:{how}"
    kept = [r for r in (p.get("reservations") or []) if r.get("key") != fkey]
    p["reservations"] = kept
    p["reserved_exit_usd"] = round(sum(float(r.get("usd") or 0) for r in kept), 4)
    p.setdefault("stages_done", []).append(3 if p["entry_usd"] < 0.01 else 1)
    if p["entry_usd"] >= 0.01:
        p["state"] = "half"
    closed = p["entry_usd"] < 0.01
    if closed:
        b["positions"].pop(sym, None)
    b.setdefault("trades", []).append({"ts": time.time(), "symbol": sym, "mint": p.get("mint"),
        "mult": round(mult, 4), "sold_usd": round(delta, 2), "pnl": round(pnl, 2),
        "how": how, "reason": reason, "closed": closed, "fee_pct": fee_pct,
        "illiq": illiq, "liq_at_exit": liq})
    save_book(b)
    return {"sold_usd": round(delta, 2), "pnl": round(pnl, 2), "closed": closed,
            "stage": 3 if closed else 1, "remaining": 0.0 if closed else p["entry_usd"]}

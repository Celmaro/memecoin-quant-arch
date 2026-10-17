"""Risk gate (blueprint §6 / spec §5 Rule 0): policy PROPOSES, gate VALIDATES.

Checks: sellable inventory (reservations), exposure caps, data freshness
(WAIT_RECONCILE freeze), quote staleness. Deterministic — no AI score.
"""
import time

MAX_SINGLE_SELL_FRACTION = 1.0     # allow full exits
MAX_ADD_FRACTION_OF_ENTRY = 0.5    # doc Rule 4: adds <= 50% of initial entry
MAX_OPEN_EXPOSURE_USD = 200.0      # paper account cap
DATA_STALE_S = 120                 # DexScreener snapshot older than this -> freeze

def gate(contract, book, sym, market_ts=None, quote_ts=None, now=None):
    """Validate a proposed decision contract. Returns (allowed_action, notes).
    May downgrade: SCALE_OUT->smaller, ADD->HOLD, or HOLD->WAIT_RECONCILE."""
    now = now or time.time()
    notes = []
    action = contract.get("action", "HOLD")
    frac = float(contract.get("sell_fraction") or 0)

    pos = book["positions"].get(sym)
    current = float((pos or {}).get("entry_usd") or 0)
    reserved = float((pos or {}).get("reserved_exit_usd") or 0)
    sellable = current - reserved

    # Rule 0: data freeze
    ts_ref = quote_ts or market_ts
    if ts_ref and now - ts_ref > DATA_STALE_S:
        notes.append(f"data stale {now - ts_ref:.0f}s > {DATA_STALE_S}s")
        if action in ("ADD",):
            return "WAIT_RECONCILE", notes
        if action in ("SCALE_OUT",):
            # stale data may not trigger profit-taking, but exits still allowed
            notes.append("exit allowed on stale data; adds blocked")

    # inventory check for sells
    if action in ("SCALE_OUT", "FULL_EXIT", "EMERGENCY_EXIT") and pos is not None:
        want = current * frac
        if want > sellable + 0.01:
            notes.append(f"requested ${want:.2f} > sellable ${sellable:.2f} (reserved ${reserved:.2f})")
            if sellable <= 0.01:
                return "HOLD", notes  # everything already reserved in-flight
            frac = sellable / current if current else 0
            contract["sell_fraction"] = round(frac, 4)
            notes.append(f"downgraded fraction to {frac:.2f} (sellable)")

    # ADD checks: exposure cap + size cap
    if action == "ADD":
        open_exposure = sum(float(p.get("entry_usd") or 0) for p in book["positions"].values())
        add_usd = current * MAX_ADD_FRACTION_OF_ENTRY
        if open_exposure + add_usd > MAX_OPEN_EXPOSURE_USD:
            notes.append(f"exposure ${open_exposure + add_usd:.0f} > cap ${MAX_OPEN_EXPOSURE_USD}")
            return "HOLD", notes
        if book.get("cash", 0) < add_usd:
            notes.append("insufficient cash for add")
            return "HOLD", notes

    return action, notes

"""position_manager/ (doc 1: lifecycle, ledger, cost_basis, scale_out, add, full_exit).

Position state transitions: selling a portion updates remaining qty; adding updates
cost basis. Every decision operates on the REMAINING position, never the original.
"""
import os, sys, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

def load_book():
    import json
    try:
        return json.load(open(os.path.expanduser("~/memecoin-quant/paper_book.json")))
    except Exception:
        return {"cash": 0.0, "realized": 0.0, "equity": 0.0, "positions": {}, "trades": []}

def save_book(b):
    import json, os
    path = os.path.expanduser("~/memecoin-quant/paper_book.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def scale_out(b, mint, sym, sell_fraction, exit_px):
    """Sell a FRACTION of the remaining position; update state + cash + realized + trade log."""
    pos = b["positions"].get(sym)
    if not pos:
        return None
    remaining_usd = pos.get("entry_usd", pos.get("size_usd", 0))
    mult = float(exit_px) / float(pos["entry_px"])
    proceeds = remaining_usd * sell_fraction * mult
    cost = remaining_usd * sell_fraction
    pnl = proceeds - cost
    b["cash"] = round(b["cash"] + proceeds, 2)
    b["realized"] = round(b.get("realized", 0) + pnl, 2)
    # update remaining position basis
    pos["entry_usd"] = round(remaining_usd * (1 - sell_fraction), 2)
    pos["size_usd"] = pos["entry_usd"]
    if pos["entry_usd"] < 0.01:
        del b["positions"][sym]
        closed = True
    else:
        closed = False
    b.setdefault("trades", []).append({
        "ts": time.time(), "symbol": sym, "mint": pos.get("mint"), "how": "scale_out",
        "sell_fraction": sell_fraction, "mult": round(mult, 4), "pnl": round(pnl, 2),
        "exit_px": exit_px, "closed": closed})
    save_book(b)
    return {"proceeds": round(proceeds, 2), "pnl": round(pnl, 2), "closed": closed,
            "remaining_usd": pos.get("entry_usd")}

def add_to_position(b, mint, sym, add_usd, add_px):
    """ADD: buy more; update cost basis (new average), exposure, remaining qty."""
    pos = b["positions"].get(sym)
    if not pos:
        return None
    if add_usd > b["cash"]:
        return None
    old_usd = pos["entry_usd"]
    old_px = pos["entry_px"]
    # tokens: old qty + new qty; new avg px = (old_usd + add_usd) / total qty
    old_qty = old_usd / old_px
    new_qty = add_usd / add_px
    total_qty = old_qty + new_qty
    avg_px = (old_usd + add_usd) / total_qty if total_qty else add_px
    b["cash"] = round(b["cash"] - add_usd, 2)
    pos["entry_usd"] = round(old_usd + add_usd, 2)
    pos["entry_px"] = round(avg_px, 12)
    pos["size_usd"] = pos["entry_usd"]
    pos["added_ts"] = time.time()
    b.setdefault("trades", []).append({
        "ts": time.time(), "symbol": sym, "mint": mint, "how": "add",
        "add_usd": add_usd, "add_px": add_px, "new_avg_px": round(avg_px, 12)})
    save_book(b)
    return {"new_avg_px": round(avg_px, 12), "total_usd": pos["entry_usd"],
            "cash_left": b["cash"]}

def full_exit(b, mint, sym, exit_px):
    """Sell the ENTIRE remaining position."""
    return scale_out(b, mint, sym, 1.0, exit_px)

def remaining(b, sym):
    pos = b["positions"].get(sym)
    if not pos:
        return 0.0
    return pos.get("entry_usd", pos.get("size_usd", 0))
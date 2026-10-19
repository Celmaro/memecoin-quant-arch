#!/usr/bin/env python3
"""reclassify_unsellable.py — Fix 3 (Oct 19).

Reclassifies the 5 UNSELLABLE total-loss paper trades that were UP on-chain
(mult >= 1.0) in paper_book_LOST_v4.json: recomputes honest PnL at the
recorded mult (3% fee), restores cash/realized, marks the rows.

Backs up the original file to paper_book_LOST_v4.json.reclass_bak first.
"""
import json, os, time, glob

BOOK = os.path.expanduser("~/memecoin-quant/paper_book_LOST_v4.json")
FEE_PCT = 3.0

# The 5 proven-up write-offs (mint -> known mult; script recomputes from rows)
TARGETS = {
    "8NfX91yvHXyHKn8cnMjg9Q4bCeRLKFDoVtEomXstpump",
    "CYGidBuS8WwUXfZCmb98LnLGL9Mx8SvhdqA6t3xTLoch",
    "Ds1dKZPV9k7GqtZKrAo6qi7WuHUeER4FmasxacRDpump",
    "8pYzkQ23bXbwVyoWjYEUHsGcJoHCH8kUEkWaV7Bbpump",
    "fwGVMj1kJCK1XFEyeBmTB9TxzcH6yPBMv5jT6nFpump",
}

def main():
    if not os.path.exists(BOOK):
        print("no book at", BOOK)
        return 1
    # backup
    bak = BOOK + ".reclass_bak"
    if not os.path.exists(bak):
        with open(BOOK) as f:
            data = f.read()
        with open(bak, "w") as f:
            f.write(data)
        print("backup ->", bak)

    b = json.load(open(BOOK))
    trades = b.get("trades") or []
    cash = float(b.get("cash", 0))
    realized = float(b.get("realized", 0))
    changed = 0
    print(f"start cash={cash:.2f} realized={realized:.2f} | {len(trades)} trades\n")

    for t in trades:
        mint = t.get("mint") or ""
        if mint not in TARGETS:
            continue
        if t.get("reclassified"):
            print(f"  skip {mint[:10]} (already reclassified)")
            continue
        mult = float(t.get("mult") or 0)
        if mult < 1.0:
            continue  # only reclassify the UP write-offs
        pnl_booked = float(t.get("pnl") or 0)
        delta = float(t.get("sold_usd") or abs(pnl_booked) or 0)
        # honest fill: proceeds = delta * mult * (1 - fee); pnl = proceeds - delta
        proceeds = delta * mult * (1 - FEE_PCT / 100)
        pnl_real = proceeds - delta
        # booked as total loss: proceeds 0, realized += -delta.
        # restore: cash += proceeds (it got 0), realized += (pnl_real - (-delta))
        cash += proceeds
        realized += pnl_real + delta
        t["pnl"] = round(pnl_real, 2)
        t["sold_usd"] = round(proceeds, 2)  # what a 3%-fee fill would return
        t["reason"] = str(t.get("reason", "")) + "+RECLASSIFIED_UP"
        t["reclassified"] = True
        t["reclass_pnl_orig"] = round(pnl_booked, 2)
        t["reclass_ts"] = time.time()
        changed += 1
        print(f"  {mint[:12]} mult={mult:.4f} booked {pnl_booked:+.2f} -> REAL {pnl_real:+.2f}")

    b["cash"] = round(cash, 2)
    b["realized"] = round(realized, 2)
    b["reclass_note"] = "Fix 3 Oct 19: 5 UP UNSELLABLE write-offs reclassified at on-chain mult (3% fee); cash/realized restored."
    with open(BOOK, "w") as f:
        json.dump(b, f, indent=1)
    print(f"\ndone: {changed} rows reclassified | new cash={cash:.2f} realized={realized:.2f}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
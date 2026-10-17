#!/usr/bin/env python3
"""Regression tests for decision-engine fixes from the Oct-17 audits (findings F1/F3-F5)."""
import sys, os
sys.path.insert(0, "/home/agentuser/memecoin-quant")
from bot.decision_engine import decide

pos = {"entry_px": 1e-6, "size": 20, "entry_usd": 20}
failures = []

def check(label, got, want):
    ok = got == want
    print(f"{'✔' if ok else '✘'} {label}: got {got}" + ("" if ok else f"  WANT {want}"))
    if not ok:
        failures.append(label)

# F1: informational EXIT_CAPACITY must NOT trigger FULL_EXIT/EMERGENCY (audit: substring `"EXIT" in s` did)
# Correct outcome is a partial scale-out (profit not fully realizable = take some), never a panic exit.
c = decide("xxx", pos, ["EXIT_CAPACITY: selling our $20 moves 45% of pool liq"], 100_000, 20_000, 1.2, 5, extra={"cash": 50})
check("EXIT_CAPACITY not full-exit", c["action"] in ("FULL_EXIT", "EMERGENCY_EXIT"), False)

# Real stop/clip still work
c = decide("xxx", pos, ["STOP: 0.82x <= 0.85x (stop 15%)"], 100_000, 20_000, 0.82, 5, extra={"cash": 50})
check("STOP -> EMERGENCY", c["action"], "EMERGENCY_EXIT")

c = decide("xxx", pos, ["CLIP1: 1.20x >= 1.15x (sell 50%)"], 100_000, 20_000, 1.2, 5, extra={"cash": 50})
check("CLIP1 -> SCALE_OUT 0.5", (c["action"], c["sell_fraction"]), ("SCALE_OUT", 0.5))

c = decide("xxx", pos, ["CLIP2: 1.40x >= 1.35x (exit all)"], 100_000, 20_000, 1.4, 5, extra={"cash": 50})
check("CLIP2 -> SCALE_OUT 1.0", (c["action"], c["sell_fraction"]), ("SCALE_OUT", 1.0))

c = decide("xxx", pos, ["FULL_EXIT: thesis broken"], 100_000, 20_000, 1.4, 5, extra={"cash": 50})
check("FULL_EXIT -> FULL_EXIT 1.0", (c["action"], c["sell_fraction"]), ("FULL_EXIT", 1.0))

# F5: ADD path with real cash
c = decide("xxx", pos, ["NEW MONEY: 3 fresh wallets put in $45"], 100_000, 20_000, 1.1, 5, extra={"cash": 50})
check("NEW MONEY + cash -> ADD", c["action"], "ADD")

print()
print("FAILURES:", failures if failures else "NONE — all decision-engine regressions pass")
sys.exit(1 if failures else 0)
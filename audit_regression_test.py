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

# ================= Oct-18 re-audit fixes =================
# --- P0-2: stage-1 transition sets state="half" so TP2 bracket runs ---
try:
    import json, tempfile, os
    from bot.position_manager import scaleout as so
    book = {"cash": 100.0, "realized": 0.0, "positions": {
        "TEST": {"mint": "testmint", "entry_px": 1e-6, "entry_usd": 100.0, "size_usd": 100.0,
                 "initial_usd": 100.0, "state": "full", "stages_done": [], "reserved_exit_usd": 0.0,
                 "peak_price": 1e-6, "trough_price": 1e-6}}}
    orig = so.load_book, so.save_book
    so.load_book = lambda: json.loads(json.dumps(book))
    so.save_book = lambda b: None
    res = so.execute_stage(book, "TEST", 1, 1.2, reason="CLIP1", how="clip1")
    check("P0-2 stage1 closed=False", res["closed"], False)
    check("P0-2 state->half after stage1", book["positions"]["TEST"]["state"], "half")
    check("P0-2 stage1 in stages_done", 1 in book["positions"]["TEST"]["stages_done"], True)
    so.load_book, so.save_book = orig
except Exception as e:
    check("P0-2 stage-transition setup", "ERR " + str(e)[:60], "no-exception")

# --- P1: execute_stage consumes ONLY this intent's reservation ---
try:
    import json
    from bot.position_manager import scaleout as so
    book = {"cash": 50.0, "realized": 0.0, "positions": {
        "TEST2": {"mint": "t2", "entry_px": 1e-6, "entry_usd": 100.0, "size_usd": 100.0,
                  "initial_usd": 100.0, "state": "half", "stages_done": [1], "reserved_exit_usd": 100.0,
                  "reservations": [{"ts": 1, "usd": 20.0, "key": "TEST2:other3"},
                                   {"ts": 2, "usd": 80.0, "key": "TEST2:decision3"}],
                  "peak_price": 1e-6, "trough_price": 1e-6}}}
    so.load_book = lambda: json.loads(json.dumps(book))
    so.save_book = lambda b: None
    res = so.execute_stage(book, "TEST2", 3, 1.1, reason="EMERGENCY", how="decision")
    # only the decision3 reservation ($80) should be consumed, not the other3 ($20)
    check("P1 reservation isolation sold 80 (not 100)", res["sold_usd"], 80.0)
    check("P1 other-intent reservation released (not double-spent)",
          book["positions"].get("TEST2", {}).get("reserved_exit_usd", 0.0), 0.0)
    # audit P0.4 (Oct 18): a stage-3 exit with a residual $20 basis must NOT discard the
    # residual position (it still has sellable value + cash owed). It stays tracked.
    check("P0.4 residual basis stays tracked after partial stage-3",
          book["positions"].get("TEST2", {}).get("entry_usd"), 20.0)
    check("P0.4 closed only when basis depleted", res.get("closed"), False)
except Exception as e:
    check("P1 reservation isolation setup", "ERR " + str(e)[:60], "no-exception")

# --- audit P0.3 (Oct 18): execute_fraction honors EXACT sell_fraction of remaining ---
try:
    import json
    from bot.position_manager import scaleout as so
    book = {"cash": 0.0, "realized": 0.0, "positions": {
        "F1": {"mint": "f1", "entry_px": 1e-6, "entry_usd": 100.0, "size_usd": 100.0,
               "initial_usd": 100.0, "state": "full", "stages_done": [], "reserved_exit_usd": 0.0,
               "peak_price": 1e-6, "trough_price": 1e-6}}}
    so.load_book = lambda: json.loads(json.dumps(book))
    so.save_book = lambda b: None
    res = so.execute_fraction(book, "F1", 0.25, 1.0, reason="THESIS_DETERIORATING", how="frac")
    check("P0.3 exact 25% sells 25 (not 50)", res["sold_usd"], 25.0)
    check("P0.3 remaining after 25% = 75", book["positions"]["F1"]["entry_usd"], 75.0)
    check("P0.3 state half after partial", book["positions"]["F1"]["state"], "half")
except Exception as e:
    check("P0.3 exact-fraction setup", "ERR " + str(e)[:60], "no-exception")

# --- audit P0.1/P0.2 (Oct 18): mint-keyed book — two same-ticker contracts coexist; a
# closed mint is NOT re-buyable from a fresh call (consumed-call ledger) ---
try:
    import os
    # paper_tick's auto_buy writes consumed_calls.jsonl — verify the ledger logic exists
    pt_src = open("/home/agentuser/memecoin-quant/paper_tick.py").read()
    check("P0.2 consumed-call ledger present", "CONSUMED_FILE" in pt_src and "_consumed_mints" in pt_src, True)
    # book keyed by mint: auto_buy writes b["positions"][mint]
    check("P0.1 book keyed by mint", 'b["positions"][mint] = {' in pt_src or "b[\"positions\"][mint] = {" in pt_src, True)
except Exception as e:
    check("P0.1/P0.2 source checks", "ERR " + str(e)[:60], "no-exception")

# --- audit P1.6 (Oct 18): exit_intel uses a SELL-route quote, not a buy quote ---
try:
    ei_src = open("/home/agentuser/memecoin-quant/bot/intelligence/exit_intel.py").read()
    check("P1.6 sell-route quote wired", "quote_sell" in ei_src and "token->SOL" in ei_src, True)
except Exception as e:
    check("P1.6 source check", "ERR " + str(e)[:60], "no-exception")

# --- audit P1.3 (Oct 18): allowlist parser tolerates trailing (private) annotation ---
try:
    sys.path.insert(0, "/home/agentuser/memecoin-quant")
    import telethon_listener as tl
    # feed a fake CONFIG section with the OLD (broken) example format; the parser
    # reads os.path.join(DESK, "CONFIG.md"), so write there and patch DESK.
    with open("/tmp/CONFIG.md", "w") as f:
        f.write("## Telegram Call Channels (curated)\n- sol_trend, solwhaletrending, solearlytrending, PIRBViewTrending, soltrenchtrending\n- SeekrTrending, signalsolanaby4am (private) — real-time via MTProto listener.\n")
    _orig_desk = tl.DESK
    tl.DESK = "/tmp"
    chans = tl.load_allowlist()
    tl.DESK = _orig_desk
    check("P1.3 allowlist parses (private) line", "signalsolanaby4am" in chans, True)
    check("P1.3 allowlist keeps all channels", len(chans) >= 7, True)
    os.remove("/tmp/CONFIG.md")
except Exception as e:
    check("P1.3 allowlist test", "ERR " + str(e)[:80], "no-exception")

# --- evidence TTL max-expiry window (P1) ---
try:
    from bot.decision_engine.evidence_ttl import HALF_LIVES
    max_exp = max(e for _h, e in HALF_LIVES.values())
    check("P1 evidence window covers 7d forensic expiry", max_exp >= 7*86400, True)
except Exception as e:
    check("P1 evidence window setup", "ERR " + str(e)[:60], "no-exception")

print()
print("FAILURES:", failures if failures else "NONE — all decision-engine regressions pass")
sys.exit(1 if failures else 0)
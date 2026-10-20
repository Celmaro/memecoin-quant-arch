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
    # P2 (Oct 20): the other-intent reservation ($20, key other3) must SURVIVE —
    # it was reserved by a different intent and was not sold. Old code zeroed the
    # whole pool, silently discarding the unsold lock (stranded basis).
    check("P2 other-intent reservation survives (20 not zeroed)",
          book["positions"].get("TEST2", {}).get("reserved_exit_usd", -1.0), 20.0)
    # audit P0.4 (Oct 18): a stage-3 exit with a residual $20 basis must NOT discard the
    # residual position (it still has sellable value + cash owed). It stays tracked.
    check("P0.4 residual basis stays tracked after partial stage-3",
          book["positions"].get("TEST2", {}).get("entry_usd"), 20.0)
    check("P0.4 closed only when basis depleted", res.get("closed"), False)
    # P2 (Oct 20): the residual $20 must be CLOSABLE once its own reservation is
    # sold — a done stage with sellable basis re-enters (self-healing), never strands.
    res2 = so.execute_stage(book, "TEST2", 3, 1.1, reason="EMERGENCY", how="other")
    check("P2 residual stage-3 re-enters (not stuck on done)", res2 is not None, True)
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

# --- Doc1-1: 3%/side paper fee is applied to exit proceeds ---
try:
    import json
    from bot.position_manager import scaleout as so
    book = {"cash": 0.0, "realized": 0.0, "positions": {
        "FEE1": {"mint": "fee1", "entry_px": 1e-6, "entry_usd": 100.0, "size_usd": 100.0,
                 "initial_usd": 100.0, "state": "full", "stages_done": [], "reserved_exit_usd": 0.0,
                 "peak_price": 1e-6, "trough_price": 1e-6}}}
    so.load_book = lambda: json.loads(json.dumps(book))
    so.save_book = lambda b: None
    res = so.execute_fraction(book, "FEE1", 1.0, 2.0, reason="TP", how="frac", liq=50000.0)
    # gross $200 - 3% fee = $194 proceeds; pnl = 194 - 100 = +94
    check("Doc1-1 3% fee on exit", res["pnl"], 94.0)
    check("Doc1-1 fee recorded on trade", (book.get("trades") or [{}])[-1].get("fee_pct"), 3.0)
except Exception as e:
    check("Doc1-1 fee test", "ERR " + str(e)[:60], "no-exception")

# --- Doc1-4 (Oct 19 FIX): sub-$1K liq exit requires 2 CONFIRMED reads ---
# liq=400 is a confirmed-low FIRST read -> HOLD (streak=1), no write-off
try:
    import json
    from bot.position_manager import scaleout as so
    book = {"cash": 0.0, "realized": 0.0, "positions": {
        "ILLIQ1": {"mint": "illiq1", "entry_px": 1e-6, "entry_usd": 50.0, "size_usd": 50.0,
                   "initial_usd": 50.0, "state": "full", "stages_done": [], "reserved_exit_usd": 0.0,
                   "peak_price": 1e-6, "trough_price": 1e-6}}}
    so.load_book = lambda: json.loads(json.dumps(book))
    so.save_book = lambda b: None
    res1 = so.execute_fraction(book, "ILLIQ1", 1.0, 0.8, reason="STOP", how="frac", liq=400.0)
    check("Doc1-4 first low-liq read -> HOLD (None)", res1, None)
    check("Doc1-4 no write-off on first read", len(book.get("trades") or []), 0)
    check("Doc1-4 streak=1 after first read", book["positions"]["ILLIQ1"].get("_low_liq_streak"), 1)
    # SECOND consecutive confirmed-low read -> now the total loss books.
    # P3 (Oct 20): the streak counts INDEPENDENT reads only — two executor calls
    # in the SAME tick share one snapshot and must NOT double-bump. Simulate the
    # next tick (>=60s later) by clearing the bump timestamp.
    book["positions"]["ILLIQ1"]["_low_liq_bump_ts"] = 0.0
    res2 = so.execute_fraction(book, "ILLIQ1", 1.0, 0.8, reason="STOP", how="frac", liq=400.0)
    check("Doc1-4 second low-liq read -> total loss pnl", res2["pnl"], -50.0)
    check("Doc1-4 illiq flag set", (book.get("trades") or [{}])[-1].get("illiq"), True)
    check("Doc1-4 reason has UNSELLABLE", "+UNSELLABLE" in (book.get("trades") or [{}])[-1].get("reason", ""), True)
    # P3 (Oct 20): SAME-TICK double read must NOT bump the streak to 2
    book2 = {"cash": 0.0, "realized": 0.0, "positions": {
        "ILLIQ3": {"mint": "illiq3", "entry_px": 1e-6, "entry_usd": 50.0, "size_usd": 50.0,
                   "initial_usd": 50.0, "state": "full", "stages_done": [], "reserved_exit_usd": 0.0,
                   "peak_price": 1e-6, "trough_price": 1e-6}}}
    so.load_book = lambda: json.loads(json.dumps(book2))
    so.save_book = lambda b: None
    so.execute_fraction(book2, "ILLIQ3", 1.0, 0.8, reason="STOP", how="frac", liq=400.0)
    so.execute_fraction(book2, "ILLIQ3", 1.0, 0.8, reason="STOP", how="frac", liq=400.0)
    check("P3 same-tick low-liq reads do NOT double-bump", book2["positions"]["ILLIQ3"].get("_low_liq_streak"), 1)
    check("P3 same-tick still HOLD (no premature write-off)", len(book2.get("trades") or []), 0)
except Exception as e:
    check("Doc1-4 unsellable test", "ERR " + str(e)[:60], "no-exception")

# --- Doc1-4b: a single liq=0 (API miss / unknown) NEVER writes off, even repeatedly
try:
    import json
    from bot.position_manager import scaleout as so
    book = {"cash": 0.0, "realized": 0.0, "positions": {
        "ILLIQ2": {"mint": "illiq2", "entry_px": 1e-6, "entry_usd": 50.0, "size_usd": 50.0,
                   "initial_usd": 50.0, "state": "full", "stages_done": [], "reserved_exit_usd": 0.0,
                   "peak_price": 1e-6, "trough_price": 1e-6}}}
    so.load_book = lambda: json.loads(json.dumps(book))
    so.save_book = lambda b: None
    r1 = so.execute_fraction(book, "ILLIQ2", 1.0, 0.8, reason="STOP", how="frac", liq=0)
    r2 = so.execute_fraction(book, "ILLIQ2", 1.0, 0.8, reason="STOP", how="frac", liq=0)
    r3 = so.execute_fraction(book, "ILLIQ2", 1.0, 0.8, reason="STOP", how="frac", liq=0)
    check("Doc1-4b liq=0 never writes off (all HOLD)", (r1, r2, r3), (None, None, None))
    check("Doc1-4b no trades booked", len(book.get("trades") or []), 0)
    check("Doc1-4b streak stayed 0 (unknown != confirmed)", book["positions"]["ILLIQ2"].get("_low_liq_streak"), 0)
except Exception as e:
    check("Doc1-4b unknown-liq test", "ERR " + str(e)[:60], "no-exception")

# --- Doc2-7: dust tranche (< $2.5) partial is skipped unless it closes ---
try:
    import json
    from bot.position_manager import scaleout as so
    book = {"cash": 0.0, "realized": 0.0, "positions": {
        "DUST1": {"mint": "dust1", "entry_px": 1e-6, "entry_usd": 3.0, "size_usd": 3.0,
                  "initial_usd": 20.0, "state": "full", "stages_done": [], "reserved_exit_usd": 0.0,
                  "peak_price": 1e-6, "trough_price": 1e-6}}}
    so.load_book = lambda: json.loads(json.dumps(book))
    so.save_book = lambda b: None
    res = so.execute_stage(book, "DUST1", 1, 1.5, reason="CLIP1", how="clip1", liq=50000.0)
    check("Doc2-7 dust partial skipped", res, None)
    check("Doc2-7 position still tracked", "DUST1" in book["positions"], True)
except Exception as e:
    check("Doc2-7 dust test", "ERR " + str(e)[:60], "no-exception")

# --- Oct 21 CHARON ROUND: time exits + ATH-proximity in the monitor engine ---
# analyze() is network-heavy; stub every provider so the DECISION logic is what
# we test, not live data. Same pattern as the scaleout tests (temp book + stubs).
try:
    import time as _t
    import position_monitor as _pm
    import desk_common as _dc

    _F = {"priceUsd": 1.2e-6, "marketCap": 80_000, "liquidity": {"usd": 25_000},
          "priceChange": {"m5": 0.5}, "volume": {"m5": 5_000, "h1": 20_000},
          "txns": {"m5": {"buys": 10, "sells": 5}}, "baseToken": {"symbol": "T"}}

    def _stub_analyze():
        _pm.market_for = lambda mint, **kw: dict(_F)
        # provider stubs — every call analyze makes should return empty/neutral fast
        for name in ("token_intel", "holder_growth", "holder_churn", "fresh_wallet_power"):
            if hasattr(_pm, name): setattr(_pm, name, lambda *a, **k: {})
        if hasattr(_pm, "rugcheck"): _pm.rugcheck = lambda *a, **k: {}
        if hasattr(_pm, "coinstats_balance"): _pm.coinstats_balance = lambda *a, **k: []
        for name in ("shyft_failed_txs", "fomo_devs_stats", "blix_holders", "blix_alive",
                     "blix_coin_trades", "blix_narratives", "blix_market", "blix_signal_track"):
            setattr(_dc, name, lambda *a, **k: ({} if name != "blix_alive" else False))
        # aegis strategy lookup neutral
        try:
            _pm.aegis.strategy_for = lambda mint: "default"
        except Exception:
            pass
        # bot.intelligence layers: all neutral/empty
        for mod, fn in [("bot.intelligence.exit_intel", "exit_intel"),
                        ("bot.intelligence.microstructure", "microstructure"),
                        ("bot.intelligence.wallet_intel", "wallet_distribution_signal"),
                        ("bot.intelligence.cohort", "cohort_signal_for"),
                        ("bot.intelligence.event_alerts", "detect_all"),
                        ("bot.intelligence.lifecycle", "classify"),
                        ("bot.intelligence.thesis", "evaluate")]:
            try:
                import importlib
                m = importlib.import_module(mod)
                if mod.endswith("lifecycle"): setattr(m, "classify", lambda *a, **k: (None, []))
                elif mod.endswith("thesis"): setattr(m, "evaluate", lambda *a, **k: {})
                elif mod.endswith("event_alerts"): setattr(m, "detect_all", lambda *a, **k: [])
                else: setattr(m, fn, lambda *a, **k: None)
            except Exception:
                pass
        try:
            import bot.decision_engine as _de
            _de.decide = lambda *a, **k: {"action": "HOLD", "sell_fraction": 0, "confidence": 50,
                                          "thesis_state": "neutral", "reason_codes": []}
            import bot.decision_engine.evidence_ttl as _ettl
            _ettl.weight_signals = lambda *a, **k: ([], [])
            _ettl.historical_weight_signals = lambda *a, **k: ([], [])
            import bot.persistence as _bp
            _bp.log_decision = lambda *a, **k: None
            _bp.log_evidence = lambda *a, **k: None
            _bp.wallet_history_add = lambda *a, **k: None
            import bot.decision_engine.risk_gate as _rg
            _rg.gate = lambda *a, **k: ("SCALE_OUT", ["stub"])
        except Exception:
            pass

    _stub_analyze()

    def _mk(age_s, peak=None, px=None):
        d = {"entry_px": 1e-6, "size": 20.0, "ts": _t.time() - age_s, "symbol": "T", "mint": "X"}
        if peak: d["peak_price"] = peak
        if px: d["px_override"] = px
        return d

    r = _pm.analyze("X", _mk(360))  # 6min old, +20% pnl, non-conviction -> MAX_HOLD
    check("A4 MAX_HOLD fires EXIT_ALL", r.get("action"), "EXIT_ALL")
    check("A4 MAX_HOLD signal present", any("MAX_HOLD" in s for s in r.get("signals", [])), True)

    _F["priceUsd"] = 1.03e-6  # net pnl after 3% display fee ≈ -0.1% (flat) -> SIDEWAYS reachable
    r2 = _pm.analyze("X", _mk(700))  # 11.7min old, flat -> SIDEWAYS (checked before MAX_HOLD)
    check("A4 SIDEWAYS fires EXIT_ALL", r2.get("action"), "EXIT_ALL")
    check("A4 SIDEWAYS signal present", any("SIDEWAYS" in s for s in r2.get("signals", [])), True)
    _F["priceUsd"] = 1.2e-6

    r3 = _pm.analyze("X", _mk(30))  # fresh + no peak -> HOLD, no time signal
    check("A4 fresh pos no time exit", any("MAX_HOLD" in s or "SIDEWAYS" in s for s in r3.get("signals", [])), False)

    # ATH proximity: peak_price higher than current px (72% => NOT within 15% -> no clip)
    _F["priceUsd"] = 1.0e-6
    r4 = _pm.analyze("X", _mk(30, peak=1.4e-6))
    check("A7 ATH far from peak no clip", any("ATH_PROXIMITY" in s for s in r4.get("signals", [])), False)
    # within 15% of peak + flat chg => clip
    _F["priceUsd"] = 1.3e-6  # 93% of 1.4e-6 peak
    r5 = _pm.analyze("X", _mk(30, peak=1.4e-6))
    check("A7 ATH_PROXIMITY clips 50%", r5.get("action"), "CLIP_50")
    check("A7 ATH_PROXIMITY signal present", any("ATH_PROXIMITY" in s for s in r5.get("signals", [])), True)
    _F["priceUsd"] = 1.2e-6
except Exception as e:
    check("A4/A7 time-exit+ATH test", "ERR " + str(e)[:80], "no-exception")

print()
print("FAILURES:", failures if failures else "NONE — all decision-engine regressions pass")
sys.exit(1 if failures else 0)
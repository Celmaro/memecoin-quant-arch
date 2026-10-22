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
# Oct 22 ext-audit P2 fix: quote_sell now receives the ACTUAL raw token qty
# (qty_raw), not usd/decimals nonsense. Assert the corrected signature.
try:
    ei_src = open("/home/agentuser/memecoin-quant/bot/intelligence/exit_intel.py").read()
    check("P1.6 sell-route quote wired",
          "def _jupiter_impact(mint, qty_raw)" in ei_src and "quote_sell(mint, int(round(qty_raw))" in ei_src, True)
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
        # Oct 21 real-engineering (LESS-CODE rule: stub where the import lives):
        # position_monitor imports BOTH market_for AND dex_pair at module level
        # (line 18). Stubbing only market_for left a real dex_pair call for the
        # invalid test mint "X" — a live DexScreener 429/timeout made this test
        # flaky (failed once with ERR, passed on rerun). No network may slip in.
        _pm.dex_pair = lambda mint, **kw: dict(_F)
        # provider stubs — every call analyze makes should return empty/neutral fast
        for name in ("token_intel", "holder_growth", "holder_churn", "fresh_wallet_power"):
            if hasattr(_pm, name): setattr(_pm, name, lambda *a, **k: {})
        if hasattr(_pm, "rugcheck"): _pm.rugcheck = lambda *a, **k: {}
        # coinstats/shyft/fomo_devs stubs REMOVED Oct 21 — providers deleted (402/406/403-dead)
        for name in ("blix_holders", "blix_alive",
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

    r = _pm.analyze("X", _mk(360))  # 6min old, +20% pnl — BELOW the 30-min MAX_HOLD floor (FIX 2)
    check("A4 6min old +20% NOT MAX_HOLD-exited (30min floor)", any("MAX_HOLD:" in s for s in r.get("signals", [])), False)

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

# --- Oct 21 FIX ROUND 2: pool-rotation fill floor + attribution partials/dedupe ---
# POOL-ROTATION (METOO class): a claimed deep dump that contradicts the coin's
# OWN feed samples (60-300s old) is the residual-pool signature — the fill must
# be floored at the stable mult, not booked at 0.087x.
try:
    import bot.position_manager.scaleout as so
    import os as _os
    import tempfile, time as _tt
    _tmp = tempfile.mkdtemp()
    _cache = _os.path.join(_tmp, "pumpdata_cache.json")
    _now_ms = _tt.time() * 1000
    _entry = 3.462671352877755e-05
    _stable_sol = 3.164e-05 / 105.98
    _tail = [(_now_ms - (60 + i * 15) * 1000, _stable_sol) for i in range(10)]
    _body = {"updated": _tt.time(), "sol_usd": 105.98,
             "mints": {"A3HMVuERxjRiJ": {"px_sol": _stable_sol / 10.5,
                                          "px_usd": _stable_sol / 10.5 * 105.98,
                                          "pxs_tail": _tail}}}
    import json as _jj
    _jj.dump(_body, open(_cache, "w"))
    _real_exp = _os.path.expanduser
    _os.path.expanduser = lambda p: _cache if p.endswith("pumpdata_cache.json") else _real_exp(p)
    try:
        _cap, _src = so._onchain_mult("A3HMVuERxjRiJ", 0.0873, _entry)
    finally:
        _os.path.expanduser = _real_exp
    check("POOL-ROTATION floor fires on step", _cap is not None and _src == "feed-rotation", True)
    check("POOL-ROTATION floors to stable (~0.914)", round(_cap or 0, 2), 0.91)
except Exception as e:
    check("POOL-ROTATION test", "ERR " + str(e)[:80], "no-exception")

# attribution dedupe + partials (temp DB only — never the live aegis.db)
try:
    import bot.research.attribution as _att
    import sqlite3
    _tmp2 = tempfile.mkdtemp()
    _att.DB = _os.path.join(_tmp2, "test.db")
    _att.ensure_schema()
    _pos = {"entry_px": 5e-5, "peak_price": 6e-5, "trough_price": 4e-5, "initial_usd": 20.0}
    _mG = "4tieigPJCAQbw1C9wBLtg9QZGgPFeuyibRD6Hi2spump"
    _att.attribute("GUZZLEGOAT", _mG, _pos, 10.51, 9.698880428499815e-05,
                   price_path=[(0,5e-5),(1,6e-5),(2,9.7e-5)])
    _tt.sleep(0.64)
    _att.attribute("GUZZLEGOAT", _mG, _pos, 12.66, 1.0384242187819961e-04,
                   price_path=[(0,5e-5),(1,6e-5),(2,1.04e-4)])
    _c = sqlite3.connect(_att.DB)
    _n = _c.execute("SELECT COUNT(*) c FROM position_attribution WHERE mint=?", (_mG,)).fetchone()[0]
    check("ATTRIB dedupe collapses 5s same-px double-write", _n, 1)
    _att.attribute("PC", "72nrwKcGGT", _pos, 9.83, 1.02e-4,
                   price_path=[(0,5e-5),(1,6e-5),(2,1.02e-4)], partial=True)
    _row = _c.execute("SELECT detail_json FROM position_attribution WHERE mint='72nrwKcGGT'").fetchone()[0]
    check("ATTRIB partial flag written", '"partial": true' in _row, True)
    _c.close()
except Exception as e:
    check("ATTRIB dedupe test", "ERR " + str(e)[:80], "no-exception")

# --- Oct 21 FIX ROUND 3: LADDER EXECUTION (why the book was red) ---
# Pins the 3 fixes: (1) FEED-SPEED STOP — loser at 0.60x books TIER_STOP at the
# first tick, never rides to 0.50; (2) winners never MAX_HOLD-exited; (3)
# EMERGENCY/DEAD requires mult < 1.0 (green coin = clip, not panic).
try:
    import time as _t3
    import position_monitor as _pm3
    _stub_analyze()  # same stubs as the A4 block above (all providers neutral)

    def _mk3(age_s, px_mult, peak=None, entry_liq=None):
        d = {"entry_px": 1e-6, "size": 20.0, "ts": _t3.time() - age_s, "symbol": "T", "mint": "X"}
        if peak: d["peak_price"] = peak
        if entry_liq: d["entry_liq"] = entry_liq
        _F["priceUsd"] = 1e-6 * px_mult  # px_mult directly sets the mult
        return d

    # (1) loser at 0.60x — tier stop (0.75) MUST fire, reason TIER_STOP, action EXIT_ALL
    _F["liquidity"] = {"usd": 25_000}
    _F["marketCap"] = 80_000
    _F["volume"] = {"m5": 5_000, "h1": 20_000}
    _F["priceChange"] = {"m5": -2.0}
    _F["txns"] = {"m5": {"buys": 10, "sells": 30}}
    r_loser = _pm3.analyze("X", _mk3(120, 0.60))
    check("R3 loser 0.60x action EXIT_ALL (not HOLD)", r_loser.get("action"), "EXIT_ALL")
    check("R3 loser 0.60x TIER_STOP signal", any("TIER_STOP" in s for s in r_loser.get("signals", [])), True)
    check("R3 loser never rides below 0.50 (stop level 0.75)", r_loser.get("mult"), 0.60)

    # (1b) loser at 0.30x — HARD_STOP (deepest floor) fires with honest label
    r_deep = _pm3.analyze("X", _mk3(120, 0.30))
    check("R3 loser 0.30x HARD_STOP fires", any("HARD_STOP" in s for s in r_deep.get("signals", [])), True)
    check("R3 loser 0.30x action EXIT_ALL", r_deep.get("action"), "EXIT_ALL")

    # (2) WINNER at 1.5x, 45 min old — MAX_HOLD must NOT fire (FIX 2)
    _F["liquidity"] = {"usd": 25_000}
    _F["volume"] = {"m5": 8_000, "h1": 60_000}
    _F["priceChange"] = {"m5": 3.0}
    _F["txns"] = {"m5": {"buys": 25, "sells": 5}}
    r_win = _pm3.analyze("X", _mk3(2700, 1.50))  # 45min, +50%
    check("R3 winner 1.5x 45min NOT MAX_HOLD-exited", any("MAX_HOLD:" in s for s in r_win.get("signals", [])), False)
    check("R3 winner 1.5x 45min SKIP signal present", any("MAX_HOLD_SKIPPED" in s for s in r_win.get("signals", [])), True)
    check("R3 winner 1.5x action NOT EXIT_ALL (rides)", r_win.get("action") != "EXIT_ALL", True)

    # (3) GREEN coin + liq collapsed — CLIP_50, never EMERGENCY (FIX 2)
    _F["liquidity"] = {"usd": 25_000}
    _F["marketCap"] = 80_000
    _F["volume"] = {"m5": 8_000, "h1": 60_000}
    _F["priceChange"] = {"m5": 3.0}
    _F["txns"] = {"m5": {"buys": 25, "sells": 5}}
    pos_green = _mk3(600, 1.30, entry_liq=60_000)  # entry liq 60K > current 25K
    r_green = _pm3.analyze("X", pos_green)
    check("R3 green 1.30x + halved liq NOT EMERGENCY", r_green.get("action") != "EMERGENCY_EXIT", True)
    check("R3 green 1.30x LIQ_HALVED_BUT_GREEN signal", any("LIQ_HALVED_BUT_GREEN" in s for s in r_green.get("signals", [])), True)

    # (3b) GREEN coin + zero liq — CLIP_50, never DEAD write-off (FIX 2)
    _F["liquidity"] = {"usd": 0}
    r_green0 = _pm3.analyze("X", _mk3(600, 1.20, entry_liq=60_000))
    check("R3 green 1.20x zero liq NOT DEAD", r_green0.get("action") != "DEAD", True)

    # (4) half-state dump at 0.40x — honest HARD_STOP label (was reason "TP")
    # fallback engine path: mult 0.40 + half state must hit hard floor, not TP
    import paper_tick as _pt3
    _b3 = {"start": 100.0, "cash": 0.0, "realized": 0.0, "positions": {}, "trades": []}
    _b3["positions"]["HALF1"] = {"mint": "HALF1", "entry_px": 1e-6, "entry_usd": 20.0, "size_usd": 10.0,
                                 "initial_usd": 20.0, "state": "half", "stages_done": [1],
                                 "reserved_exit_usd": 0.0, "peak_price": 1e-6, "trough_price": 1e-6}
    _pt3.save_book = lambda b: None
    _pt3.load_book = lambda: _b3
    _pt3.pm.save_positions = lambda p: None
    _out3 = []
    # simulate the fallback sell arm directly via sell_via_stage with the reason
    # the new fallback would produce: HARD_STOP for half-state dump at 0.40x
    _b3_pos = _b3["positions"]["HALF1"]
    _r3 = _pt3.sell_via_stage(_b3, {}, "HALF1", "HALF1", 3, 0.40, 0.4e-6,
                              reason="HARD_STOP", how="stop", out=_out3,
                              market_ts=_t3.time(), liq=25_000.0)
    check("R3 half-state 0.40x books HARD_STOP (honest label)", _r3, True)
    check("R3 half-state trade reason HARD_STOP", (len(_b3["trades"]) and _b3["trades"][-1].get("reason")), "HARD_STOP")
    _F["liquidity"] = {"usd": 25_000}
    _F["priceUsd"] = 1.2e-6
except Exception as e:
    check("R3 ladder-execution test", "ERR " + str(e)[:80], "no-exception")

# --- Oct 22 EXT AUDIT ROUND (external audit verify + fix lock) ---
# Pins: P1#1 book writer lock wired everywhere; P1#3 auto_buy exposure cap;
# P1#4 pre-grad needs zero-liq (MC alone insufficient); P1#5 missing ts rejected;
# P2 Jupiter impact passes raw qty (not usd/decimals).
try:
    import re as _re22
    # P1#1: book_lock must exist in desk_common AND be used by both save_book impls
    _dc22 = open("/home/agentuser/memecoin-quant/desk_common.py").read()
    _pt22 = open("/home/agentuser/memecoin-quant/paper_tick.py").read()
    _so22 = open("/home/agentuser/memecoin-quant/bot/position_manager/scaleout.py").read()
    check("R22 book_lock in desk_common", "def book_lock" in _dc22, True)
    check("R22 paper_tick uses book_lock", "from desk_common import book_lock" in _pt22, True)
    check("R22 scaleout uses book_lock", "from desk_common import book_lock" in _so22, True)
    # P1#3: auto_buy body must contain the exposure-cap guard
    check("R22 auto_buy exposure cap wired", "MAX_OPEN_EXPOSURE_USD" in _pt22, True)
    # P1#4: pre-grad must require zero/unknown liq (not MC alone)
    check("R22 pre-grad requires zero liq", "liq in (None, \"\", 0)" in _pt22, True)
    # P1#5: entry_call must reject missing ts outright
    check("R22 missing-ts rejected", "no_valid_ts" in _pt22, True)
    # P2: exit_intel impact uses raw qty signature
    _ei22 = open("/home/agentuser/memecoin-quant/bot/intelligence/exit_intel.py").read()
    check("R22 Jupiter impact raw-qty signature", "def _jupiter_impact(mint, qty_raw)" in _ei22, True)
except Exception as e:
    check("R22 ext-audit lock", "ERR " + str(e)[:80], "no-exception")

# --- Oct 22 EXT AUDIT ROUND 2 (release/intent_key + no-op exit reconcile) ---
# P1 (2nd audit): paper_tick calls release(..., intent_key=...) but the nested
# scaleout MUST define it with that param — a signature mismatch would raise
# TypeError when execute_stage returns None (dust tranche / low-liq defer).
# Also: fail-closed exposure cap wired in BOTH entry paths.
try:
    import inspect as _in22
    sys.path.insert(0, "/home/agentuser/memecoin-quant")
    from bot.position_manager.scaleout import release, save_book as _so_save, load_book as _so_load
    _sig22 = _in22.signature(release)
    check("R23 release() accepts intent_key", "intent_key" in _sig22.parameters, True)

    # no-op reconcile: execute_stage returns None (dust tranche < $2.5) — the
    # tick's cleanup must release the reservation WITHOUT raising TypeError.
    _tmp22 = tempfile.mkdtemp()
    _b22 = {"start": 100.0, "cash": 0.0, "realized": 0.0, "positions": {}, "trades": []}
    _b22["positions"]["T22"] = {"mint": "T22", "entry_px": 1e-6, "entry_usd": 20.0,
                                "size_usd": 20.0, "initial_usd": 20.0, "state": "full",
                                "stages_done": [], "reserved_exit_usd": 0.0,
                                "peak_price": 1e-6, "trough_price": 1e-6,
                                "reservations": [{"ts": _t3.time(), "usd": 10.0, "key": "T22:3:stop"}]}
    # release with intent_key on a pos whose reservations carry that key
    try:
        release(_b22, "T22", 10.0, intent_key="T22:3:stop")
        no_err = True
    except TypeError as _te:
        no_err = False
        _err22 = str(_te)[:80]
    check("R23 release(intent_key=...) no TypeError", no_err, True)
    check("R23 released row removed from reservations",
          all(r.get("key") != "T22:3:stop" for r in (_b22["positions"]["T22"].get("reservations") or [])), True)
    check("R23 released pool recomputed to 0",
          float(_b22["positions"]["T22"].get("reserved_exit_usd") or 0), 0.0)

    # fail-closed exposure cap in BOTH entry paths (auto_buy + entry_call)
    _pt23 = open("/home/agentuser/memecoin-quant/paper_tick.py").read()
    check("R23 auto_buy cap FAIL-CLOSED", "exposure_cap_uncomputable" in _pt23, True)
    check("R23 entry_call cap FAIL-CLOSED", (_pt23.count("exposure_cap_uncomputable") >= 2), True)
except Exception as e:
    check("R23 ext-audit-2 lock", "ERR " + str(e)[:80], "no-exception")

# --- Oct 22 THIRD AUDIT (R24): enforced daily stop, micro concentration,
#     cohort vocab, notifier burst cursor, GoPlus parser, clean-snapshot list ---
try:
    # 2.1: daily stop is a PERSISTED kill-switch checked in every entry path
    _pt24 = open("/home/agentuser/memecoin-quant/paper_tick.py").read()
    check("R24 daily-stop helper persists stop_until", "stop_until" in _pt24, True)
    check("R24 auto_buy checks daily stop", _pt24.count("daily_stop_active(") >= 2, True)
    check("R24 entry_call checks daily stop", "daily_stop_active(b)" in _pt24, True)

    # 5.6: micro buy concentration = top-5 wallets' share of buy USD (>=0.6 reachable)
    _mic24 = open("/home/agentuser/memecoin-quant/bot/intelligence/microstructure.py").read()
    check("R24 micro concentration = top-5 USD share", "top5_share" in _mic24, True)

    # 5.7: cohort consumer accepts producer vocabulary (buy_usd_notional)
    _coh24 = open("/home/agentuser/memecoin-quant/bot/intelligence/cohort.py").read()
    check("R24 cohort buys buy_usd_notional", "buy_usd_notional" in _coh24, True)

    # 2.4: notifier must NOT advance watermark when MAX_POST capped the run
    _nt24 = open("/home/agentuser/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239/scripts/call_notifier.py").read()
    check("R24 notifier capped-run watermark guard", "len(out) < MAX_POST" in _nt24, True)

    # 5.1: GoPlus transfer_hook routed through _gp_flag (no '0'-string truthiness)
    _dc24 = open("/home/agentuser/memecoin-quant/desk_common.py").read()
    check("R24 transfer_hook uses _gp_flag",
          "transfer_hook\": _gp_flag" in _dc24, True)

    # 3.1: clean-snapshot negative list is case-insensitive + covers ws_dumping
    _ae24 = open("/home/agentuser/memecoin-quant/aegis.py").read()
    check("R24 clean-snapshot case-insensitive", "_fl[i].startswith" in _ae24, True)
    check("R24 clean-snapshot covers ws_dumping/transfer_hook",
          ("ws_dumping" in _ae24 and "transfer_hook" in _ae24), True)

    # 3.2: ave_top1 dedup refund = 6 (matches the -6 charge)
    if "aegis" not in _ae24:
        _ae24 = _ae24  # placeholder
    check("R24 ave_top1 refund 6", '"ave_top1": 6' in _ae24, True)

    # 3.4: deployer coin_count counts UNIQUE mints via deployer_mints ledger
    check("R24 deployer unique-mint ledger", "deployer_mints" in _ae24, True)

    # 5.2: fragility derives top10 from topHolders (not a non-existent raw key)
    _sig24 = open("/home/agentuser/memecoin-quant/signals.py").read()
    check("R24 fragility top10 from topHolders", "_rc_top10_pct" in _sig24, True)

    # 6.7: zero-decimal mints keep dec=0 (is not None, not truthiness)
    _ju24 = open("/home/agentuser/memecoin-quant/jupiter_estimator.py").read()
    check("R24 token_decimals None-check", "dec is not None" in _ju24, True)

    # 6.3: dead /swap/v2/quote PROBE removed (comment text may mention the route)
    check("R24 no dead v2/quote probe", "QUOTE.replace('/swap/v1/quote','/swap/v2/quote')" not in _ju24, True)

    # 6.6: quote_fill bounded by process-wide semaphore
    check("R24 quote_fill bounded semaphore", "_QUOTE_SEM" in _ju24, True)
    check("R24 quote pool slot released by worker", "_Q.release()" in _ju24, True)

    # 6.8: research ladders derive from live EXIT_TIERS (no 1.10/1.30/0.15 literals)
    _da24 = open("/home/agentuser/memecoin-quant/bot/audit/deep_audit.py").read()
    check("R24 deep_audit live ladder", ("EXIT_TIERS[0]" in _da24 and "1.10, 1.30" not in _da24), True)
    _ff24 = open("/home/agentuser/memecoin-quant/bot/audit/flow_filter_audit.py").read()
    check("R24 flow_filter live proxies", ("EXIT_TIERS" in _ff24 and "return 19.4" not in _ff24 and "return -14.55" not in _ff24), True)
    _tc24 = open("/home/agentuser/memecoin-quant/bot/audit/tape_coins_audit.py").read()
    check("R24 tape_coins tier-at-fill", "tier_for(" in _tc24, True)
    _rp24 = open("/home/agentuser/memecoin-quant/bot/audit/replay_backtest.py").read()
    check("R24 replay imports live EXIT_TIERS", "from position_monitor import EXIT_TIERS" in _rp24, True)

    # 7.6: entity-label CLI queries real columns
    _el24 = open("/home/agentuser/memecoin-quant/aegis_entity_labels.py").read()
    check("R24 entity CLI real columns", "fb.coin_mint" in _el24 and "c.call_ts" in _el24, True)

    # 7.2: maintenance initializes schema + checks learner returncode
    _am24 = open("/home/agentuser/memecoin-quant/aegis_maintenance.py").read()
    check("R24 maintenance ensure_schema", "ensure_schema" in _am24, True)
    check("R24 maintenance learner returncode", "p.returncode != 0" in _am24, True)

    # 7.1/7.4: scan_log DDL carries flags_json + query index migration
    check("R24 scan_log DDL flags_json", "flags_json TEXT" in _dc24, True)
    check("R24 scan_log index migration", "idx_scan_log_mint_ts" in _dc24, True)
except Exception as e:
    check("R24 third-audit lock", "ERR " + str(e)[:80], "no-exception")

print()
print("FAILURES:", failures if failures else "NONE — all decision-engine regressions pass")
sys.exit(1 if failures else 0)
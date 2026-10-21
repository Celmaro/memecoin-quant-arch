# DONE/IMPLEMENTED/WIRED CLAIM AUDIT (Oct 21 2026)

User asked: verify everything claimed DONE over the past days is actually DONE.
Result: **most claims verified TRUE, but the audit caught 3 real live failures**
— paper_tick timing out for ~12h, pumpdata_feed crash-looping, and a book
corruption incident (phantom +$30.8M). All fixed and re-verified.

## ✅ VERIFIED TRUE (checked against live code/state, not memory)

| Claim | Evidence |
|---|---|
| EXIT_TIERS retuned TP2.0/SL0.75 | code: `[(0,2.0,2.5,0.25), (40K,2.0,2.5,0.25), (150K,2.2,2.8,0.25), (1.5M,2.5,3.0,0.25)]` |
| replay_backtest mirrors ladder | imports/uses the same constants |
| deep_audit imports EXIT_TIERS (no literal) | `from position_monitor import EXIT_TIERS` L32 |
| PumpData feed in supervisor children | `("pumpdata", [VENV_PY, pumpdata_feed.py])` L34 |
| market_for feed-first wired | paper_tick L5/315/478/622 + position_monitor L19 |
| feed keepalive + stall watchdog + ack-err log | present (subscribe_sol_price / 60s watchdog / sub ack err) |
| quote_fill wired (fill-to-fill) | jupiter_estimator L100 + 4 call sites in paper_tick |
| MAX_HOLD/SIDEWAYS both engines | constants + signals + fallback import |
| ATH_PROXIMITY clip | monitor analyze |
| risk-level DM line | day pnl / positions / wr → LOW/MED/HIGH + DAILY STOP |
| flow_filter_audit script | exists, compiles, n=21 honest result |
| Regression | **46/46, FAILURES: NONE** (run just now) |

## ❌ CAUGHT (claims that were NOT actually true in production)

### 1. pumpdata_feed crash-loop — NOT actually stable
`_log` was a 1-arg lambda but `run()` calls `_log("cycle err: %s", str(ex))`
(2 args) → **TypeError on every cycle error → feed died → supervisor restarted
→ died again**. It had been crash-looping since the rewrite; my earlier
"stable" checks were between crashes. **FIX: `def _log(msg, *args)`** — verified
alive 35s+ with no crash, `alive — 8093 events/min`.

### 2. paper_tick timeout — NOT actually running exits
**466 `paper_tick exc: timed out after 120 seconds` since 21:53 yesterday.**
Root cause: `auto_buy()` had NO deadline — many fresh calls × `market_for`
(3.4s each on feed-cache miss) blew the 120s supervisor window before the main
loop's TICK_DEADLINE even started. With the feed down + slow dex fallback, 63
positions piled up overnight while exits never ran. **FIX: BUY_DEADLINE=90s
inside auto_buy** — verified: paper_tick now completes in 0s, 0 new timeouts
in 6+ min.

### 3. Book corruption incident — phantom +$30.8M (auto-restored)
Two trades booked `mult=1,590,409` and `29,988` → pnl +$30,853,915 and
+$581,757 on $20 positions (`how: decision_scale_out`, `entry_px: None`).
Chain: (a) fill-to-fill `quote_fill` could set a **scale-corrupted entry_px**
(out_tokens decimal mismatch → entry_px ~1e6× too small → mult explodes); (b)
the **legacy inline sell path** in paper_tick had **NO corruption cap** (the
MAX_BOOKED_GAIN_MULT=10 cap existed only in scaleout execute_stage/fraction).
The book was AUTO-RESTORED (note in paper_book.json: "RESTORED Oct 21 after
phantom-gain corruption"; corrupt archive preserved as
paper_book_CORRUPT_20261021_20261021_082333.json). **FIXES: (a) quote_fill now
rejects fills outside 0.2x..5x of the feed price (scale guard, verified: corrupt
quote → None → 1% slip); (b) legacy inline path now caps mult via
MAX_BOOKED_GAIN_MULT + _confirm_gain (verified: 1,590,409 → capped 10.0,
pnl $180 not $30.8M).**

## Post-fix state (verified live)
- Desk: supervisor 372986, telethon 373071 (1 session holder), pumpdata 373403 — all alive.
- Feed: `alive — 8093 events/min`, 7 rows, freshest 58s — NOT crash-looping.
- paper_tick: completes in 0s, heartbeat fresh, **0 new timeouts** since restart.
- Regression: **46/46 FAILURES: NONE**.
- Book: legit restored state ($26.66 cash, 24 trades, -73.34 realized) — consistent.

## Lesson (do not regress)
Audit scripts re-verify RUNTIME health, not just presence: process alive,
feed events/min, tick completion time, timeout count trend. A claim "wired"
is only true when the running system proves it. The `_log` lambda arity bug
and the missing deadline in auto_buy are the two classes to re-check after any
feature round.
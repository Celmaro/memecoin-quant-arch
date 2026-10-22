# Verification of second attached audit (memecoin-quant-arch, e87928c9) against live code

Date: 2026-10-19 23:5x CST. Method: every P0/P1 claim checked by reading the exact cited file+line in /home/agentuser/memecoin-quant (repo) + profile-scripts dir. Note: live tree contains later fixes than the snapshot the audit reviewed; where a claim is already fixed in the live tree, that is flagged.

## Section 1 — Critical blockers

### P0 — failed exit booked as if it succeeded → CONFIRMED (live bug)
paper_tick.py:559-573: `stage == "frac"` → `execute_fraction()`. If it returns None (dust skip, unconfirmed low-liq hold, reservation-blocked), line 573 sets `stage = None`, then line 582 `if stage is None or "unavailable" in out[-1]` → **legacy inline branch (L583-597)** books `sold = entry_usd*frac*mult`, credits cash/realized, appends a trade, and for full exit does `b["positions"].pop(sym, None)` — **pops by SYMBOL from a MINT-keyed book** (P0.1). The position is NOT removed; cash is credited; next tick re-processes the "open" position. Exactly as the audit describes. The safer executor's decline becomes a paper sale. NOT fixed in live tree.

### P1 — stage completion / reservations strand inventory → CONFIRMED
scaleout.py:180-182 (`if stage in done: return None`), :194-197 (`delta<=0 → append stage to stages_done`), :239 (`reserved_exit_usd = 0.0` without reconciling reservations list). The residual-$20 example is real: after an $80/$100 decision-stage-3 sale, stage 3 is done, position stays open (closed only if entry_usd<0.01), and later stage-3 calls return None immediately → residual cannot be closed via stage logic. No per-intent reservation reconciliation. CONFIRMED.

### P1 — low-liquidity confirmation not reliable → CONFIRMED
scaleout.py:42-44 `_resolve_exit_liq` accepts a positive liq < $1000 directly as a single read (no re-read). `low_reads` (:59) is computed but not used to gate. The executor increments `_low_liq_streak` per invocation (:210-215). Two executor calls in the same tick (decision path → fallback tier engine reuses the same `mkt` snapshot from paper_tick.py:464) can push streak to 2 without two independent market observations. CONFIRMED.

### P1 — paper cost model inconsistent → CONFIRMED
scaleout.py:22-23 PAPER_FEE_PCT=3.0 (exit deduction ~0.97×proceeds). paper_tick entries use observed price × ~1% fill-latency slip (no 3% entry fee). position_monitor.py FEE_BPS=1.7 and exit_intel.py FEE_BPS=1.7 use 1.7% exit estimate. Three different cost numbers for entry/exit/monitor → monitor PnL ≠ book PnL. CONFIRMED.

### P1 — cross-process entry not serialized → CONFIRMED (design gap)
instant_trigger.py has a cross-process lock (.instant_trigger.lock) + instant_sent dedupe; paper_tick auto_buy and call_notifier entry_call run in other processes. Consumed ledger + book + positions are read-modify-write with atomic file replace only — no cross-process mutex around the whole entry. Concurrent readers can double-evaluate before a consumed marker lands. CONFIRMED as the audit states.

## Section 2 — backtest trustworthiness

### P1 — deep_audit look-ahead (86% edge) → CONFIRMED
deep_audit.py:110-129: `push60` = max price/fill over events in [call0, call0+60s]; `bs5m` = buy:sell in first 5 min after call; fill modeled at call0+1500ms. Using 60s/5min post-call data in a strategy that "enters" at +1.5s = look-ahead. Outcome walk starts at `call0` (line 135 `if ts < call0: continue`), not the fill ts — events between call0 and the modeled fill are included in the outcome. CONFIRMED — the doc's claim that the 86% TP2 rate doesn't validate an immediate-buy strategy is correct.

### P1 — outdated exit ladder → CONFIRMED (with number correction)
deep_audit.py defines EXIT_TIERS (line 28, LIVE ladder) but the simulation **hardcodes `tp1, tp2, stop = 1.10, 1.30, 0.15`** (base tier, MC≤40K) — it does NOT use the MC-dependent ladder. The audit's specific "1.30×/1.60×/0.78×" numbers don't match the code (that's the DEEP_AUDIT_20261020.md report's description), but the structural claim (backtest ≠ live strategy) holds. Partially confirmed — numbers wrong, architecture claim right.

### P1 — replay PnL drops realized clip profit → CONFIRMED
replay_backtest.py:186-189: on clip1, `pnl_clip = 0.5*(mult-1)*100 - 0.5*FEE_PCT` is computed but NOT accumulated into later TP2/STOP/HORIZON returns (:179-193 each return only `share*(mult-1)*100 - share*FEE_PCT`). A position that clipped +30% then stopped loses the clipped profit from replay PnL. Fee also applied to principal share, not proceeds×mult, unlike the live executor. CONFIRMED.

### P1 — filtered tape cache reuse corrupts sample → CONFIRMED
replay_backtest.py:50 cache file named by hour + **len(mint_set)** only; :61-68 `--reuse-any` picks the FIRST existing cache for the hour regardless of which mints it contains. Different mint sets of same size collapse. anomaly_scan/exit_verify L119-142 have similar first-usable-cache paths. Partial filtered caches can be read as full tape. CONFIRMED.

### Report arithmetic +$148.60 vs 52 TP2/107 STOP → CONFIRMED inconsistent
Weighted by the stated averages: 0.31×(+18.4%) + 0.63×(−22.5%) = **−8.47%/call → ≈ −$290 expected on 159 settled at $20** — positive +$148.60 baseline cannot follow from those outcome averages under a consistent weighting. (The report may use position-size weighting or count OPEN differently; it does not say, so the numbers don't reconcile as presented.) CONFIRMED as unresolvable-as-stated.

## Section 3 — Telegram retraction

### P1 — retraction not durable → CONFIRMED
telethon del_handler writes marker with `ts` but **no `dt`**; paper_tick auto_buy parse condition is `if d.get("dt")` (paper_tick.py:186-190) → the retraction marker is never a candidate, and the **original unretracted row remains eligible**. entry_call/instant_trigger path picks the newest matching line (the marker) so those skip correctly — but the cron/auto_buy path can still buy a deleted call. Durable status table absent. CONFIRMED exactly as the audit says.

### P1 — notifier skips same-ts burst → CONFIRMED
call_notifier.py: `fresh[:MAX_POST]` (MAX_POST=4) + watermark `next_ts = max(...)`; next loop uses strict `ts > last_ts`. >4 records sharing a timestamp → unprocessed rows at that ts are skipped forever. CONFIRMED.

### P1 — instant_sent marked before completion → ALREADY FIXED in live tree
call_notifier.py now contains "P1-5 (Oct 20): MERGE — save_state was replacing the whole state dict, wiping instant_trigger's instant_sent → double-card risk". Current code does `st["last_ts"] = next_ts; save_state(st)` on the full dict, preserving instant_sent. (instant_trigger still marks before delivery completes — a crash between mark and send still suppresses the cron retry; that part of the claim stands.)

## Section 4 — Entry strategy

### $10K gate vs bonding curve → ALREADY ADDRESSED in live tree
paper_tick.py now has `CURVE_FILL_MC_USD=69000`, `_is_curve_pre_grad()` and "FIX 3a (Oct 20)": the $10K gate is bypassed for pre-graduation mints (`liq < 10000 and not _is_curve_pre_grad(mkt)`). The live gate no longer rejects all curve tokens. The audit's recommendation (market-state-aware gate) is implemented. The "report says 22/33 of best cohort rejected" refers to an earlier state; the code commit already includes the fix.

## Section 5 — attribution

### Fractional close missing attribution → CONFIRMED
paper_tick.py:559-572 frac branch: `save_book; pm.save_positions; continue` — no `attribute()`/`record_event()` call. sell_via_stage closed path (:637+, `if res["closed"]:`) DOES call attribution. So fraction-closed trades can exist in the book without position-close events. CONFIRMED.

## Section 6 — tests/maintenance

### No .github/workflows → CONFIRMED (.github absent). regression sys.path hardcoded → CONFIRMED (audit_regression_test.py:4 `sys.path.insert(0, "/home/agentuser/memecoin-quant")`).
### Missing coverage (failed-fraction fallback, reservation reconciliation, concurrent entry, retraction consumption, same-ts bursts, cache completeness, look-ahead) → CONFIRMED — none of those are asserted.

## Overall verdict

This second audit is **substantially accurate** — unlike the first attached doc, nearly every P0/P1 structural claim survives code verification:

- **CONFIRMED as live bugs**: P0 failed-fraction fallback books a declined sale (and pops by symbol); stage/reservation stranding; low-liq streak not from independent reads; inconsistent cost model (1%/3%/1.7%); cross-process entry race; deep_audit + replay backtest look-ahead / dropped-clip-PnL / MC-agnostic ladder / cache-collision; retraction not enforced on the auto_buy path; notifier same-ts burst skip; frac-close attribution gap; report arithmetic inconsistency.
- **Already fixed in live tree** (audit reviewed an older snapshot): call_notifier instant_sent MERGE (P1-5 Oct 20); curve-aware liquidity gate (FIX 3a Oct 20).
- **Numbers partially wrong**: "fixed 1.30/1.60/0.78" in deep_audit is actually hardcoded 1.10/1.30/0.15 (still not MC-dependent); report title ladder drift is accurate but the described script numbers differ.

Recommended fix order from the audit (single deterministic exit path; reconcile reservations; serialize state; unify cost model; repair+rerun backtest; durable retraction semantics) is echoed by this verification and should be treated as real, not as speculative.
# CHARON ROUND IMPLEMENTED (Oct 21 2026) — 5 items wired + FLOW research

Implemented from the 4-repo learn report (LEARN_FULL_FOUR_BOTS_20261021.md).
Regression: **46/46 (FAILURES: NONE)**. Desk restarted healthy (supervisor
1095892, single session holder, feed 15 rows fresh).

## Item 1 — FILL-TO-FILL PAPER PRICING (wired both entry paths)
- `jupiter_estimator.quote_fill(mint, usd, timeout=6)` — NEW: REAL Jupiter buy
  quote as the paper FILL source. `fill_px = usd_in / out_tokens` (impact +
  fee included). **Hard thread-bound** (6s ceiling) so the paper tick can never
  hang on a quote — a hung/flaky Jupiter returns None and the entry falls back
  to the old 1% latency slip. Verified live: normal mint 2.0s via=jupiter;
  hung/garbage mint hard-caps at 6.0s and falls back clean.
- `paper_tick.auto_buy` + `entry_call`: quote runs AFTER size is final
  (confluence/aegis_boost/cash applied) so the fill sizes the exact order.
  BUY lines show `fill=1.8%imp $0.34fee` (real impact + fee) vs `fill=1%slip`
  on fallback. Position rows store `fill_note`.
- Feed price REMAINS the decision mark; only the recorded fill price changed.
- ⚠️ Two bugs caught during the build: (a) quote()'s own http_json is
  20s×3 — the first quote_fill just called it and could hang the tick; fixed
  with the worker-thread ceiling; (b) the fill was initially placed BEFORE
  size resolution (NameError → silent fallback) — moved after size is final in
  both paths. Both compile-guarded + regression-tested.

## Item 2 — MAX_HOLD + SIDEWAYS TIME EXITS (monitor + fallback engines)
- `position_monitor`: `MAX_HOLD_S=300` (5min quick flip), `MAX_HOLD_CONVICTION_S=
  7200` (2h user horizon for conviction), `SIDEWAYS_S=600` + `SIDEWAYS_PNL_PCT=
  2.0`. Checked in analyze() when action is HOLD/CLIP_50 (never downgrades a
  sticky stop/emergency): SIDEWAYS first (smarter early exit for flat
  positions), MAX_HOLD as the hard cap. Signals `MAX_HOLD:` / `SIDEWAYS:` →
  EXIT_ALL.
- `paper_tick` fallback tier engine: OLD flat `STALE >3h` rule REPLACED by the
  same constants (imported from position_monitor) so BACKGROUND moonbags (which
  skip deep analyze) stay on the same clock. Reason strings carry the details.
- Rationale on-file: Charon's 1,146-trade backtest (MAX_HOLD 334 trades at
  +1.1% avg keeps the book alive) + our STRATEGY_COMPARE (+180s flips
  −15.2% → −1.6%, win 28% → 53%). Default 300s chosen as the quick-flip
  horizon per CONFIG (5min at the low end of "minutes to ~2h").

## Item 7 — ATH-PROXIMITY CLIP (Charon topBlastRisk)
- `position_monitor.analyze`: when px ≥ 85% of the position's peak_price
  (high-water from the book, propagated into the monitor record) AND m5 change
  < +2% with volume → `ATH_PROXIMITY:` signal → CLIP_50; ≤ −2% m5 → EXIT_ALL.
  Complements round_stall (catches cliffs BELOW the round-number zone).
- `paper_tick` main loop now copies `p["peak_price"]` into the monitor record
  so the monitor sees the same high-water the book tracks.

## Item 10 — RISK LEVEL IN DM SUMMARY LINE
- `paper_tick` summary now computes and appends: day realized PnL vs the
  $20 daily stop (proximity %) + open-position concentration (/5) + win rate →
  risk level LOW/MEDIUM/HIGH, plus `🛑 DAILY STOP — no new entries 24h` when
  day ≤ −$20 (and `⚠️ near daily stop` at ≥ 80%). The desk now has a visible
  "stop for the day" state in every DM heartbeat.
- NOTE (per the learn report, item 5): this is still a DISPLAY — the daily
  stop is not yet ENFORCED as an entry gate. That remains open.

## Research item 3 — FLOW FILTER RE-TEST (honest result)
- Built `bot/audit/flow_filter_audit.py` — computes Charon's FLOW features
  (1h price change + 5m net-buyer ratio) from the CACHED 12h replay at CALL
  time, strictly pre-call (no look-ahead), outcomes = tape ladder TP2/STOP,
  Charon's strict both-halves acceptance rule.
- RESULT: only **21 of 171 calls have a full pre-call hour** in the call-anchored
  `_replay_cache` (the cache stores mints filtered to each hour's call set, so
  early-window mints don't carry 1h of back-history) → **n=21 < the 30/trade
  minimum → INCONCLUSIVE on this cache.** The script is correct and reusable;
  it needs the raw full-hour tapes (re-download from pumpapi.io) to reach a
  verdict. This is reported as "not enough tape", not as a pass/fail — no
  filter change was made on it.

## Verification
- Compile: paper_tick/position_monitor/jupiter_estimator/flow_filter_audit/regression — ALL OK.
- Regression: **46/46** (38 prior + 8 new: MAX_HOLD ×2, SIDEWAYS ×2, fresh-no-exit,
  ATH far ×1, ATH clip ×2).
- quote_fill: 2.0s via=jupiter on a live mint; 6.0s hard-cap + None on hang.
- Desk: restarted (supervisor 1095892), single tg_session holder, feed cache
  fresh (15 rows), paper_tick completes in-time (rc=0, no timeouts).
- Open: daily-stop enforcement (display only today), FLOW verdict needs raw tapes.
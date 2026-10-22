# EXTERNAL AUDIT VERIFICATION — Oct 22, 2026

Audit source: public GitHub repo (main branch), commit db552f0 — **reviewed against
the LIVE local tree** (`~/memecoin-quant`), which carries all Oct 19-22 fixes the
public repo lags. Every finding below was verified by reading the actual code path
and, where behavior was in question, by running it.

## Verdict by finding

| # | Finding (audit) | Local status | Evidence |
|---|---|---|---|
| P1-1 | Concurrent processes can overwrite paper book (paper_tick, scaleout, entry_call) | **CONFIRMED + FIXED** | load->mutate->save spans across processes; no lock. Repro: 2 procs, 1 buy LOST. Added `book_lock()` (fcntl flock) in desk_common, wired into both save_book impls + auto_buy/entry_call/main spans. Re-probe: both buys survive. |
| P1-2 | Instant entries bypass freshness gate (no ts) | **PARTIALLY FIXED → hardened** | instant_trigger already passes `ts` (audit lags), BUT missing/invalid ts silently skipped the age check. Now: `no_valid_ts` rejection. |
| P1-3 | auto_buy() bypasses exposure cap | **CONFIRMED + FIXED** | auto_buy had only a cash check (min(size, cash)), never the $200 face cap entry_call has. Added same `MAX_OPEN_EXPOSURE_USD` guard before the fill quote. |
| P1-4 | Pre-graduation exemption trusts MC alone | **CONFIRMED + FIXED** | `_is_curve_pre_grad` used MC < $69K only — a migrated coin that dumped below 69K with a real thin pool got the exemption. Now requires zero/unknown liq (the genuine pre-grad signature). |
| P1-5 | Notifier cursor issues (timestamp-only watermark, delivery-before-save) | **P9 keyed-cursor already live; delivery order FIXED** | Live notifier (profile scripts dir) already has handled_keys + capped 400 + merge. New fix: state saves AFTER successful send — failed DM now retries next run instead of losing the calls. |
| P1-6 | Script migration inconsistent (root vs bot/audit) | **NOT AN ISSUE locally** | Root files are 9-line delegation shims (runpy to bot/audit/), not divergent implementations. Verified all 4 (deep_audit, tape_coins_audit, replay_backtest, audit_regression_test). |
| P1-7 | Backtests don't reproduce live exit policy | **FIXED (ladder is single-source)** | replay_backtest/deep_audit either import `position_monitor.EXIT_TIERS` or carry an exact commented mirror `(0:2.00/2.50/0.25, 40K:2.00, 150K:2.20, 1.5M:2.50)` — verified identical to live. No 1.10/1.30 old-ladder literals remain. |
| P2-8 | **Jupiter exit-impact wrong unit** | **CONFIRMED REAL + FIXED** | `_jupiter_impact(mint, usd, run_decimals)` was called with `qty_raw` in the run_decimals slot → `raw = usd/qty_raw` ≈ 0 → `quote_sell(1)` → **impact ~0% on EVERY exit analysis**. Signature now `(mint, qty_raw)` passing raw units straight to quote_sell. Live-probed: 0.82-2.04% on real positions. |
| P2-9 | release() leaves stale reservation records | **ALREADY FIXED (Oct 20)** | release() removes matching rows + recomputes pool from survivors; docstring documents the corruption fix. |
| P2-10 | Unknown liq doesn't break low-liq streak | **ALREADY FIXED (Oct 19 phantom-loss fix)** | `resolved_liq is None → streak = 0`; only confirmed low (0 < liq < 1K) twice with 60s-independent reads books UNSELLABLE. |
| P2-11 | Regression not CI-enforced | **PARTIAL — accepted** | Regression suite runs manually (3× pattern). No CI on this box; hardcoded /home/agentuser paths are inherent to the single-tenant desk. |

## Repository hygiene (anomaly hunt)

- Dead provider removal (Oct 21) left **zero refs** to solscan/madeonsol/shyft/coinstats/fomo legacy — grep sweep clean.
- Root-vs-bot/audit duplication: root shims delegate via `runpy.run_path` — 9 lines each, single source of truth in bot/audit/. Keep the shim pattern (cron/aliases depend on absolute paths).
- No other anomalies found in wiring: `market_for` feed-first everywhere, `_note_fill` local-only, `save_book` guarded + atomic (fsync + os.replace), `book_lock` reentrant per-process.

## Files changed (Oct 22 round)

- `desk_common.py` — +book_lock() (shared fcntl flock)
- `paper_tick.py` — book_lock wiring (save_book/auto_buy/entry_call/main), missing-ts reject, auto_buy exposure cap, pre-grad zero-liq rule
- `bot/position_manager/scaleout.py` — book_lock wiring on save_book
- `bot/intelligence/exit_intel.py` — _jupiter_impact raw-qty signature
- `bot/audit/audit_regression_test.py` — P1.6 assert update + R22 locks
- Hermes profile scripts `call_notifier.py` — delivery-before-save ordering

## Verification after fixes

- `py_compile` all 6 modified + notifier: COMPILE_OK
- Regression suite: FAILURES: NONE ×3 runs
- Concurrency probe: no-lock loses a buy; with lock both survive ($100→80 cash, 2 positions)
- Live impact probe: 1.79%/0.82%/2.04% on real mints (was 0% pre-fix)
- Desk restarted: supervisor 903124 + telethon + pumpdata, fresh heartbeat, feed alive
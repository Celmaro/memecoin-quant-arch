# REPO DRIFT RECONCILIATION — Oct 22, 2026 (2nd external audit)

The 2nd audit found the PUBLIC repo contradicted the audit report. Root cause:
my earlier archive `cp` commands copied fixed files to WRONG paths (root instead
of `bot/...`), so the public repo held NEW code at root but OLD code at the
paths production imports. This has now been fixed by syncing the live tree to
the repo with correct paths + deleting the wrong-path duplicates.

## Audit findings → resolution

| # | Finding | Resolution |
|---|---|---|
| P1 | instant_trigger calls entry_call without ts → rejected no_valid_ts | FIXED in repo: `instant_trigger.py:110` passes `"ts": ts` (was already fixed live; repo synced) |
| P1 | notifier saves state before DM delivery → calls lost | FIXED in repo: `scripts/call_notifier.py` now has handled_keys + delivery-before-save (`delivery FAILED — watermark NOT advanced`) |
| P1 | exposure cap fails open (except: pass) | FIXED: BOTH entry paths now FAIL CLOSED — `exposure_cap_uncomputable_<err>` skip + log, never silent entry |
| P2 | AUDIT_VERIFY says shims but public root had substantial bodies | FIXED: root files are now real shims (runpy delegation); wrong-path root `exit_intel.py`/`scaleout.py` DELETED (404 on GitHub) |
| P1 | exit handler crashes: release() without intent_key | FIXED: `bot/position_manager/scaleout.py:284` = `def release(book, sym, usd, intent_key="")`; R23 regression tests release(intent_key) no TypeError + row removed + pool recomputed |
| P1 | Jupiter impact fix in wrong module (monitor imports old nested impl) | FIXED: `bot/intelligence/exit_intel.py:16` = `def _jupiter_impact(mint, qty_raw)`; root duplicate deleted |
| P1 | regression suite asserts wrong paths / stale ladders | FIXED: bot/audit regression = live copy with R22+R23 checks; tape_coins_audit now imports `position_monitor.EXIT_TIERS` (single source) |
| P2 | replay/deep/tape ladders stale (1.10/1.30/0.15) | FIXED: tape_coins_audit imports live ladder; replay/deep already single-source; `bot/audit/replay_backtest.py` ladder verified identical |

## What changed in repo (commit 1adf1c3)

- 12 root files + 7 nested files synced to live (verified byte-identical)
- Wrong-path root duplicates deleted: `exit_intel.py`, `scaleout.py`
- `scripts/call_notifier.py` restored as the FIXED notifier (delivery-before-save)
- Archive-only files (README, watchdogs, replay scripts) preserved
- R23 regression: release(intent_key) + no-op reconcile + fail-closed caps

## Verification

- Regression suite: FAILURES: NONE ×3 (R22 7 checks + R23 6 checks all pass)
- Public GitHub verified via raw URLs: release 4-arg ✓, impact 2-arg ✓, ts ✓,
  tape imports EXIT_TIERS ✓, root shims ✓, root exit_intel/scaleout 404 ✓,
  notifier handled_keys ✓
- Desk healthy: supervisor 903124 + telethon + pumpdata, fresh heartbeat,
  book consistent ($29.60 cash, 4 open, 43 trades)

## Remaining honest gaps (not fixed, from 2nd audit)

- Research scripts still use hardcoded `$150` SOL/USD conversion (research-only,
  not production path)
- GRAD-DURING cohort is post-entry classification, not an entry-time signal
- No CI workflow in repo (single-tenant box; regression runs 3× manually)
- No live transaction-signing layer (paper desk by design — user pulls triggers)
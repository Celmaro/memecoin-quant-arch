# PONYTAIL AUDIT (Oct 20 2026) — module layer CLEAN, 20 stale files archived

Methodology: Ponytail Reduction (memecoin-quant skill) — archive dead probes,
retire landmine daemons, delete unused modules, strip dead commands. Applied
to the CURRENT repo state (post-PumpData wiring).

## Module layer: CLEAN (38 naive orphans → all accounted for)

Full import-graph scan (63 live modules). All root "orphans" fall into
exactly three legitimate classes — NONE are dead code:

1. **P13 shims (10 files)**: root `deep_audit.py`, `replay_backtest.py`,
   `audit_regression_test.py`, `anomaly_scan.py`, `exit_verify.py`,
   `reclassify_unsellable.py`, `unsellable_verify.py`, `blix_settings_fix.py`
   (+2 more) — thin runpy wrappers that delegate to `bot/audit/*.py` so
   absolute-path callers (cron, aliases, docs) keep working. By design, keep.
2. **Standalone runners with `__main__`**: `desk.py`, `desk_supervisor.py`,
   `pumpdata_feed.py`, `instant_trigger.py`, `aegis_*.py` etc. — invoked as
   processes or call-time imports (whale_watch, aegis_maintenance,
   aegis_entity_labels all confirmed LIVE via call-time `from X import` the
   naive scan missed).
3. **Audit tools** in `bot/audit/` — invoked as CLI scripts, not imported.

Package-style imports (`from bot.intelligence.thesis import ...`) reference
5 bot modules the basename scan flagged — verified present and used by
position_monitor/paper_tick. No unused bot/ modules remain from the Oct-18
ponytail; the 4 deleted spec modules have not been reintroduced.

## ACTIONS TAKEN (all safe, desk untouched, verified after)

**Moved to `archive/logs/` (12 dead logs)** — all from retired daemons or
completed one-off runs; the 3 LIVE logs stay at root:
ws_monitor.log, ws_supervised.log, hmq_listener.log, hmq_supervised.log,
supervisor.log, telethon_audit.log, replay_06.log, replay_12h.log,
replay_12h_run.log, replay_24h.log, replay_cache_only.log, exit_verify_run.log

**Moved to `archive/probes/` (8 stale state files)** — no references anywhere
(grep-verified):
ws_state.json, hmq_state.json, hmq_bots.json, hmq_scans.jsonl,
positions_LOST_v1.json, positions_LOST_v2.json, positions_LOST_v3.json,
_outcome_replay.json

**KEPT at root (verified required)**:
- `paper_book_LOST_v1..v4.json` (5 files) — `bot/audit/exit_verify.py` and
  `unsellable_verify.py` glob `paper_book_LOST_v*.json` at DESK root; moving
  them breaks the audit tools.
- `paper_book_CORRUPT_resvleak_*.json` x2 + `paper_book_ARCHIVE_*.json` +
  `paper_book_CLOBBERED_by_test.json` — evidence of the Oct-20 incidents,
  small; kept for the record.
- All active daemons, caches, and state files (calls.jsonl,
  telegram_messages.jsonl, aegis.db, paper_book.json, positions.json,
  pumpdata_cache.json, etc.)

## FLAGGED (no action — tape evidence / user discretion)

- **`_grad_cache/` 3.3G + `_shrine_cache/` 1.5G** (4.8G total): Oct-10
  graduate replay raw + filtered tapes. Both replay tools re-derive on demand
  (raw `.jsonl.zst` re-downloadable from pumpapi.io / replay.shrine.trade;
  `.filtered.jsonl` re-derivable from raw). Kept because user's tape-first
  rule — the hours we downloaded are the audit evidence base. If disk pressure
  matters more than re-download cost (~10 min/hour), safe to delete the raw
  `.zst` pairs; ask user first.
- **`_replay_cache/` 862M** — the Oct-18/19 12h audit window. KEEP (core tape).
- **cron jobs**: `memecoin-call-notifier`, `memecoin-scan-curator-watchdog`,
  `memecoin-paper-tick`, `replay-12h-backtest*`, `exit-verify*` all
  paused/completed — kept as config; `memecoin-listener-watchdog` +
  `memecoin-blix-watchdog` live.

## VERIFICATION AFTER TRIM
- Desk processes: supervisor 3020094, telethon 3020160, pumpdata 3020172 —
  all alive post-move.
- `fuser tg_session.session` = exactly 1 holder (telethon listener).
- Regression suite: **FAILURES: NONE** (38/38).
- pumpdata_cache.json fresh (15 rows) — feed unaffected.
- grep across py/sh/json: no dangling references to any moved file.
- Disk 79% (11G free).
# Memecoin Quant Bot — Deep Architecture Audit

Date: 2026-10-19
Scope: `/home/agentuser/memecoin-quant`, live Python source and running processes
Method: AST/import inventory, runtime/process inspection, SQLite/schema inspection, live endpoint probes, logs, regression suite.

## REA boundary

The REA MCP provider was not registered in this session, so no fabricated REA Evidence IDs are claimed. The REA skill explicitly routes complete plain-source repositories through normal repository analysis rather than binary/JS REA tooling. The bot is a Python source tree; this dossier is the equivalent deep source/runtime architecture pass. The previously analyzed BLIX JavaScript clone remains covered by the retained REA evidence IDs in the earlier reports.

## Executive verdict

The desk is a **call-driven, paper-only Solana monitoring system** with four major planes:

1. **Ingestion:** Telethon MTProto listener reads curated Telegram channels and writes append-only JSONL.
2. **Forensics/scoring:** AEGIS combines on-chain/API evidence, deployer memory, holder/first-buyer analysis, BLIX tape, and entity labels into a score/verdict.
3. **Decision/position plane:** `position_monitor` produces a decision contract; `paper_tick` is the execution/accounting owner for paper exits and entries.
4. **Persistence/operations:** SQLite memory, paper JSON state, journaling, systemd supervisor, Hermes watchdog jobs, and a local BLIX radar.

The strongest architectural property is the paper-execution safety boundary: all 20 decision-engine regression checks pass, including exact fraction exits, reservation isolation, residual tracking, hard illiquidity loss handling, mint-keyed books, and sell-route quoting.

The biggest structural risks are:

- `first_buyers` has no uniqueness constraint; duplicate rows continue to reappear after maintenance.
- `followups` has 354 undone rows and only a manual CLI invokes the consumer.
- `coins.call_ts` and `coins.source` are NULL for all 1,025 rows; the schema advertises data that ingestion does not provide.
- `paper_tick` historically timed out at 120 seconds under network/position load, which can stall monitoring.
- `memecoin-desk.service` is enabled and active again. It currently runs a clean single-child topology, but its `Restart=always` lifecycle has caused repeated supervisor restarts and is the previously identified control-plane landmine.
- BLIX itself is partially degraded: its webapp, trade/pumpswap RPC lanes, RugCheck, Helius configuration, Telegram lane, and sanity checks report down/degraded states, although launch, freshness, RPC, DexScreener, Jupiter, and tape lanes remain usable.

## Static inventory

- 42 live Python files including `bot/`.
- Approximately 7,819 live Python source lines.
- Root orchestration: `desk_supervisor.py`, `telethon_listener.py`, `aegis.py`, `position_monitor.py`, `paper_tick.py`, `desk.py`, `desk_common.py`, `signals.py`, `journal.py`.
- Bot/spec layer: decision engine, evidence TTL, risk gate, intelligence modules, monitor tiers, persistence, scaleout, attribution.
- Archived clones and retired code were excluded from the live graph: `archive/`, `blixtrade/`, `nought/`, `Solana-Forensic-Analysis-Tool/`, virtualenv, caches.

### Dependency shape

- `desk_supervisor` → `aegis`, `desk_common`, `aegis_maintenance`.
- `telethon_listener` → `desk_common` and Telegram/JSONL state.
- `aegis` → `aegis_entity_labels`, `desk_common`, `jupiter_estimator`, `bot`.
- `position_monitor` → `aegis`, `signals`, `desk_common`, `bot`.
- `paper_tick` → `position_monitor`, `desk_common`, `confluence`, `bot`.
- `desk.py` is the broad manual CLI façade and imports most subsystems, including retired/optional curator modules.
- `desk_common.py` is the largest shared dependency and centralizes HTTP, provider helpers, SQLite schema, budgets, and event logging. It is a high fan-in single point of failure.

## Live data-flow architecture

```text
Curated Telegram channels
        │ MTProto / Telethon
        ▼
telethon_listener.py
        │
        ├── telegram_messages.jsonl  (all parsed channel observations)
        ├── calls.jsonl              (deduped mint call stream)
        ├── retractions.jsonl        (deleted-call markers)
        └── telethon_state.json      (catch-up/watermark state)
                │
                ▼
desk_supervisor.scan_new_calls()
        │ freshness, market lookup, cap, dedupe
        ▼
AEGIS.score_coin(mint, deep=True)
        │
        ├── contract/security checks
        ├── RugCheck / GoPlus / Ave / Helius / RPC
        ├── first_buyers and deployer memory
        ├── funding/entity labels
        ├── BLIX holders/tape/narrative/performance
        ├── holder churn / fresh wallet / cohort overlays
        └── scan_log + evidence_events + decision data
                │
                ├── DM/card alert path
                └── paper entry path
                        ▼
                 paper_tick.entry_call()
                        │
                        ├── liquidity and hygiene gates
                        ├── confluence sizing
                        ├── consumed_calls ledger
                        ├── paper_book.json
                        ├── positions.json / SQLite positions
                        └── decision/attribution records
                                │
                                ▼
                    paper_tick → position_monitor.analyze()
                                │
                                ├── thesis / lifecycle / microstructure
                                ├── wallet and holder intelligence
                                ├── evidence TTL weighting
                                ├── risk gate
                                └── scaleout / paper accounting
```

## Storage map

### JSON/JSONL operational state

- `telegram_messages.jsonl`: 6.39 MB; raw parsed channel observations.
- `calls.jsonl`: 3.08 MB; current call stream, 3,020 historical calls in the earlier audit.
- `paper_book.json`: 78.9 KB; paper cash, positions, realized results, trade history.
- `positions.json`: currently empty monitor-side JSON state.
- `consumed_calls.jsonl`: entry dedupe ledger.
- `holder_snapshots.json`: 1.94 MB; holder retention snapshots, capped by mint count but still large.
- `notifier_state.json`: watermark and instant-send state.
- `telethon_state.json`: small listener state.
- `*_LOST_*`: superseded crash artifacts; safe archival candidates.

### SQLite `aegis.db`

Current observed size: 12.25 MB database plus approximately 0.77 MB WAL. Integrity check passed and foreign-key check returned no violations.

| Table | Rows | Role | Main consumers |
|---|---:|---|---|
| `coins` | 1,025 | token scan identity and score snapshot | AEGIS, scoring/history |
| `scan_log` | 1,487 | score/verdict history | trend/card/journal analysis |
| `first_buyers` | 1,937 | earliest buyer/rank evidence | AEGIS rugger/bot/entity checks |
| `deployers` | 594 | creator memory and score offsets | AEGIS/entity enrichment |
| `funding_links` | 417 | funder/deployer graph | entity and cohort logic |
| `wallet_labels` | 144 | entity/category/risk labels | AEGIS check #18 |
| `wallet_entity_clusters` | 4,816 | entity grouping | cohort/entity attribution |
| `wallet_history` | 12,741 | observed wallet buy/sell events | cohort/position intelligence |
| `evidence_events` | 581 | TTL-weighted signals | decision engine |
| `decision_audit` | 806 | decision contracts and reasons | audit/research |
| `market_events` | 153 | replayable market observations | attribution/replay |
| `position_attribution` | 153 | MFE/MAE/PRR/DVA | paper performance |
| `followups` | 387 | delayed outcome checks | only manual `desk.py followups` currently |
| `call_metrics` | 654 | call outcome/channel history | journal/manual analysis |
| `leaderboard_snapshots` | 300 | cohort snapshots | cohort intelligence |

## Runtime topology observed

```text
systemd user manager (PID 1116)
  ├── memecoin-desk.service
  │     └── desk_supervisor.py
  │           └── telethon_listener.py run
  └── local BLIX Node server on 127.0.0.1:4420
```

Verified at audit time:

- Exactly one `desk_supervisor` process.
- Exactly one Telethon listener.
- Exactly one holder of `tg_session.session`, matching the listener child.
- No `hmq_listener.py`, `ws_monitor.py`, or duplicate Telethon child in the active tree.
- `memecoin-desk.service` is **enabled and active**, with `Restart=always` and `RestartSec=10`.
- Hermes jobs: listener watchdog and BLIX watchdog scheduled; scan-curator watchdog, call-notifier, and paper-tick jobs paused.
- Current supervisor source has only Telethon in `CHILDREN`; the former HMQ/WS children are retired.

### Systemd finding

The service is the previously documented landmine: it was supposed to be masked because it repeatedly resurrected the old supervisor topology. It is back and enabled. The current source has removed the old HMQ/WS children, so the active process tree is clean, but the lifecycle policy still creates repeated supervisor restarts and makes manual stop/control non-deterministic.

Recommended control-plane decision:

- Either keep systemd deliberately and rename/redefine it as the canonical supervisor service, with explicit logging, health checks, and one Telethon child;
- Or mask `memecoin-desk.service` and add a dedicated supervisor watchdog before disabling it. Do not simply kill it: `Restart=always` will resurrect it.

## Confirmed findings and recommendations

### F1 — First-buyer duplication is not actually fixed

**Observation:** `first_buyers` has no UNIQUE constraint or unique index. `aegis.py` uses `INSERT OR REPLACE`, but without a conflict target it behaves as an ordinary insert. The maintenance log recorded 256 duplicates in one pass; 5 remained at a later probe after another cycle.

**Impact:** inflated buyer counts, repeated entity probes, larger DB, misleading rank evidence.

**Recommendation:** dedupe once, then create a unique index on `(coin_mint, wallet, rank)`. Keep `INSERT OR REPLACE` only after the index exists. Add a regression asserting repeated recording leaves one row per identity.

### F2 — Followup consumer is manual-only

**Observation:** only `desk.py` invokes `journal.run_followups()`. Neither the live supervisor nor paper tick calls it. There are 354 undone rows; oldest due date is Oct 16.

**Impact:** PRFS/outcome measurement does not advance automatically; rejection and delayed-outcome learning remains stale.

**Recommendation:** run a bounded followup batch from the supervisor once per hour or once per day, with a provider budget and timeout. Preserve unknown-provider results as `UNKNOWN_DATA`, not `DEAD`. Emit metrics for due, completed, unknown, and failed.

### F3 — `coins.call_ts` and `coins.source` are dead schema fields

**Observation:** all 1,025 coin rows have `call_ts IS NULL` and `source IS NULL`.

**Impact:** timing/channel rules cannot safely use these columns; prior behavioral-label logic already hit this failure mode.

**Recommendation:** write them at the call-ingestion boundary from `calls.jsonl`, or remove them from rule assumptions and document them as nullable historical fields. Preferred: populate `source`, `call_ts`, `mc_at_call`, and `liq_at_call` when a call enters the scan pipeline.

### F4 — Paper tick has a historical timeout hazard

**Observation:** supervisor logs contain repeated `paper_tick.py timed out after 120 seconds`. A current manual run with zero open positions completed in about 0.47 seconds.

**Impact:** when positions exist and a provider hangs, the subprocess can consume two minutes and delay subsequent monitoring cycles.

**Recommendation:** enforce per-provider deadlines inside the paper path, use a bounded per-position budget, and run position ticks independently from entry scanning. A single slow Jupiter/Dex/RPC request must not block every position.

### F5 — Supervisor control plane is ambiguous

**Observation:** `memecoin-desk.service` is enabled/active again and has `Restart=always`; systemd journal confirms repeated stop/start cycles. Current child topology is clean, but the unit is the same previously retired service name.

**Impact:** concurrent sessions or manual kill operations can create confusing restarts; operational ownership is unclear.

**Recommendation:** choose one owner. Prefer a canonical, intentionally managed service with a health endpoint and PID/lock assertions. If preserving the no-systemd design, mask this unit and create a dedicated supervisor watchdog first.

### F6 — BLIX is useful but not healthy as a complete dependency

**Observation:** BLIX is live on port 4420 and has fresh launch/price data, but health reports 13 issues and `worst=down`. Down/degraded lanes include webapp, trades, pumpswap, sanity, Helius configuration, RugCheck, Telegram, AI/scout, and wallet freshness.

**Impact:** BLIX-derived enrichment is partial. AEGIS must never treat missing BLIX data as a clean result.

**Recommendation:** keep all BLIX inputs fail-closed/unknown. Surface a per-alert `BLIX coverage` indicator. Repair the Helius project ID and distinguish local provider failure from token risk. Keep core AEGIS independent of BLIX.

### F7 — Centralization in `desk_common.py`

**Observation:** `desk_common.py` has 89 functions and is imported by nearly every live module.

**Impact:** provider helper changes can affect ingestion, scoring, paper execution, and maintenance simultaneously.

**Recommendation:** split by responsibility: `providers_http`, `db_schema`, `provider_budget`, `blix_client`, and `event_log`. Add contract tests around response-shape normalization and timeout behavior.

### F8 — Retention is now present but should be enforced by schema/metrics

**Observation:** `aegis_maintenance.py` is wired daily and the latest run purged expired evidence. `scan_log` is the largest SQLite table by storage; `wallet_history` is already 12,741 rows.

**Recommendation:** retain current 30-day operational policy, add row-count/size telemetry, and archive aggregate history before deletion. Add a unique index for first buyers before relying on maintenance.

## What is working well

- Single curated-channel entry policy is preserved: external scanners and BLIX enrich calls; they do not create entries.
- Telethon ingestion is live and resolving all curated channels; new messages continue arriving.
- Entity-label enrichment is live: wallet labels and funding links have grown substantially from the earlier audit.
- Two-hop funding graph records are being populated.
- `score_coin()` remains wired to entity labels and produces risk flags.
- Paper execution remains non-signing and paper-only.
- Decision regression suite: **all checks pass; failures = none**.
- BLIX local radar is running with fresh launch and market data, despite degraded optional lanes.
- SQLite integrity and foreign-key checks pass.

## Prioritized next actions

1. Add the `first_buyers(coin_mint, wallet, rank)` unique index after dedupe.
2. Decide and implement one canonical supervisor owner; resolve the enabled `memecoin-desk.service` ambiguity.
3. Add an automatic bounded followup runner and clear/expire the 354 stale rows with explicit outcomes.
4. Populate `coins.call_ts/source` at the Telegram call boundary.
5. Break paper tick into per-position/provider timeout budgets.
6. Repair BLIX Helius/RugCheck/Telegram configuration and keep degraded coverage visible.
7. Split `desk_common.py` only after the above correctness issues are closed.

---

# F1–F6 FIXES — EXECUTED 2026-10-19 (all verified live)

## F1 — first_buyers uniqueness (DONE, holding)
- Deduped live DB 1,967 → 1,954 rows, created `CREATE UNIQUE INDEX idx_fb_uniq ON first_buyers(coin_mint, wallet, rank)`.
- Added the same index creation to `desk_common.init_schema()` for fresh DBs.
- Verified `INSERT OR REPLACE` now dedupes (2 inserts → 1 row), and maintenance runs report `first_buyer_dups: 0` since.

## F2 — followups auto-runner (DONE)
- `journal.run_followups(verbose, limit, deadline_s)` — bounded batch + hard deadline; `dex_pair` calls now use `(timeout=8, retries=1, allow_fallback=False)` (was 20s×3 → this is what hung the first batch 77s/25 rows; now 26.7s/25).
- Supervisor runs a bounded batch (limit=60, deadline_s=80) every 30 ticks **in a daemon thread** (threaded AFTER the first sync version was caught stalling the scan/paper loop — do not regress to sync).
- Backlog draining live: 354 → 184 → … open rows, log `followups: 60 processed`.
- Maintenance stale-expiry tightened 7d → 3d overdue, outcome='EXPIRED'.

## F3 — coins.call_ts/source populated (DONE)
- `desk_common.remember_call_meta(mint, channel, call_ts, symbol, mc, liq)` upserts immutable first-sighting meta (COALESCE, first wins).
- Called in `desk_supervisor.scan_new_calls` for every fresh call BEFORE the freshness gate.
- AEGIS `score_coin` coins write changed from `INSERT OR REPLACE` (which wiped call_ts/source to NULL on every rescan — the root cause) to `INSERT ... ON CONFLICT(mint) DO UPDATE` preserving first-seen values.
- Backfilled 1,032 rows from calls.jsonl history: 1,033/1,052 coins now carry source+call_ts (19 unmatchable, no call record). Verified rescan of OPEQ preserves `source=solwhaletrending, call_ts`.

## F4 — paper_tick no longer hangs (DONE)
- `paper_tick.main()` now enforces `TICK_DEADLINE = now + 100s` (< supervisor's 120s timeout); positions past the budget defer to next tick with a `⏱ tick budget hit` line instead of blocking the book.
- All in-loop `dex_pair()` calls use tight `(timeout=10, retries=1, allow_fallback=False)` (previously 20s×3).
- Verified: 0-position run completes in 0.1s (was timing out 120s under load in supervisor logs).

## F5 — supervisor ownership + send_dm (DONE)
- `desk_supervisor` now takes a flock on `desk_supervisor.lock` at startup and exits(2) if another instance owns it. Verified: second start logged `ERROR another desk_supervisor already owns …; exiting`.
- systemd unit `memecoin-desk.service` kept as canonical owner (deliberate decision — it is the single supervisor; Restart=always now safe with the lock), added `Environment=HERMES_HOME=<profile>` so `send_dm` loads the right TELEGRAM_BOT_TOKEN.
- Verified post-restart under systemd: send_dm works (no rc=1 after 18:19:38 restart; telegram lane delivered 624/24h via BLIX).

## F6 — BLIX settings repair (DONE; 1 user action left)
- Root cause: `rpcUrl/rpcWsUrl/jupiterKey/telegramToken` were EMPTY in BLIX settings (earlier script set only heliusProjectId → trades/pumpswap had no Helius endpoint, telegram lane dead).
- New `~/memecoin-quant/blix_settings_fix.py` (canonical, replaces /tmp copy) reads CONFIG.md + profile .env and POSTs all secrets; verifies via `secrets` map (masked).
- After apply + verify: **telegram ok** (624 sent/24h), **rugcheck ok**, **jupiter ok** — all recovered.
- Remaining (user action): `HELIUS_PROJECT_ID` — BLIX's helius.js reads `admin-api.helius.xyz/v0/admin/projects/<PROJECT_ID>/usage` with the key; the CONFIG.md UUID is the RPC API key, not the project ID, so the admin read still 400s. Until added to CONFIG.md (dashboard.helius.dev → project settings): helius lane stays down, trades/pumpswap WS lanes self-pace (metrics ≤25% of local daily — by-design budget guard, not a fault; REST tape/candles still work).
- sanity degraded→ (was down): 2 coins off by 8x+ (was 4-6) — improving.

## Final state (post-fix)
- systemd active, 1 supervisor + 1 telethon listener, lock file = supervisor pid, tg_session holder = listener pid.
- first_buyers dups 0; followups open 184 and draining; coins meta 1,033/1,052.
- All modules compile; decision regression suite 20/20 pass.

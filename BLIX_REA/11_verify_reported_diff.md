# Verification of attached REA forensic diff (1fe132fd → e87928c9) against live code

Date: 2026-10-19 23:xx CST. Method: every file claim checked by existence+size; every behavior claim checked by reading the actual source (repo root + profile scripts dir where noted). "Repo" = /home/agentuser/memecoin-quant.

## Stage 0 — file inventory claims

| Claim | Verdict | Evidence |
|---|---|---|
| 13 newly added files | **12/13 TRUE, 1 FALSE** | exit_verify.py✓ unsellable_verify.py✓ reclassify_unsellable.py✓ confluence.py✓ instant_trigger.py✓ anomaly_scan.py✓ replay_backtest.py✓ tape_coins_audit.py✓ deep_audit.py✓ COINS_AUDIT_20261020.md✓ DEEP_AUDIT_20261020.md✓ pattern_analysis_20261019.md✓ — but **scripts/rich_card.py is NOT in the repo** (no scripts/ dir in repo). It lives at `/home/agentuser/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239/scripts/rich_card.py` (10,976 B) with call_notifier.py (6,766 B). |
| 14 modified files | **Mostly plausible, partially unverifiable** | aegis.py (46KB, expanded), paper_tick.py, position_monitor.py, telethon_listener.py, desk_common.py, scaleout.py, audit_regression_test.py all present + clearly grown vs my earlier session inventory. CONFIG.example.md, replay_backfill.py, desk.py, journal.py exist. **bot/intelligence/exit_intel.py and bot/intelligence/microstructure.py: microstructural claim FALSE (see stub check).** |
| lifecycle.py + microstructure.py = "~50-byte unchanged stubs" | **FALSE — both fully implemented** | lifecycle.py is **2,784 B** with 6-state machine (DISCOVERY..COLLAPSE, classify(age_min, holder_retention)); microstructure.py is **2,481 B** with CVD/buy-distribution/absorption logic. They are NOT 50-byte placeholders. |
| Unchanged submodules (cohort/wallet_intel/thesis/event_alerts/monitor_tiers/attribution/evidence_ttl/risk_gate/scan_curator/signals) | **Plausible; partially FALSE today** | signals.py **WAS modified Oct 19** (I patched it earlier: added `helius_asc_txs` import). The rest (cohort, thesis, event_alerts, wallet_intel, risk_gate, evidence_ttl, attribution) exist and are unchanged from what I audited. |
| Root .py count | **FALSE: 29, not 23** | 29 root .py files (incl. aegis_learn.py, aegis_maintenance.py, blix_settings_fix.py, desk_supervisor.py, whale_watch.py, aegis_entity_labels.py not in the doc's list). |

## Stage 1A — "Exit-liquidity & unsellable defense trio" claims

| Claim | Verdict | Evidence |
|---|---|---|
| exit_verify/unsellable_verify/reclassify are **Jupiter sell-route verifiers**, fragmented across 5 files | **FALSE — wrong tool model** | exit_verify.py is a **post-hoc tape auditor**: streams PumpAPI replay archives (replay.pumpapi.io/{hour}.jsonl.zst), checks "did tape reach ≥1.5× above our exit within 2h" with median-of-5 baseline. unsellable_verify.py reads UNSELLABLE trades and replays tape to prove/disprove them. **Neither calls Jupiter nor classifies live sellability.** reclassify_unsellable.py is a one-off Fix-3 script that restores cash/realized for 5 proven-up write-offs in paper_book_LOST_v4.json (backs up to .reclass_bak first). |
| "$10 quote vs position size asymmetric honeypot trap" | **NOT PRESENT as described** | jupiter_estimator.quote() defaults usd=20 for entry impact; **exit_intel.py:61-69 passes the ACTUAL held token qty** (`qty_raw`, from sz/ep) to quote_sell with slippage 500 — it quotes the real position, not a fixed small notional. quote_sell(mint, token_amount) uses the caller's token amount. The micro-sell trap does not exist in the sell-verify path. |
| "Jupiter 429 caught as no-route → misclassify UNSELLABLE" | **NOT PRESENT** | jupiter_estimator raises `Exception("both v1+v2 quote failed")` after trying v2 then v1 (line 64); quote_sell wraps in try/except → returns None, and **exit_intel's _jupiter_impact catches Exception → returns None** (line 30), then falls back to heuristic `impact = 5*notional/liq`. A 429 never becomes UNSELLABLE; UNSELLABLE is only booked by scaleout's 2-confirmed-low-reads + rugcheck-dead logic (verified Oct 19). The claim conflates audit scripts with the live exit path. |
| "Consolidate sell verification into exit_intel" | **Already the architecture** | exit_intel is the single sell-intel function; the three root scripts are offline audit tools, not competing live paths. |

## Stage 1B — "Fast-path confluence & instant trigger" claims

| Claim | Verdict | Evidence |
|---|---|---|
| instant_trigger **bypasses bot/decision_engine/risk_gate** | **TRUE (by design)** | instant_trigger.py imports call_notifier + paper_tick.entry_call only; no risk_gate/evidence_ttl import (grep: 0 hits). It fires the card + paper entry on the curated call, with the **$10K liquidity gate + entry hygiene inside entry_call** (paper_tick.py:271-308). It does NOT pass through the decision-engine risk gate — real gap vs the doc's concern, but partially mitigated by entry_call's own gates. |
| **Race / double-entry** when two channels call same mint concurrently | **MITIGATED, not fully** | instant_trigger.py:17-61 has a **cross-process flock-style lock** (.instant_trigger.lock with stale-holder healing), marks `instant_sent` in notifier_state BEFORE the slow AEGIS build, and dedupes `mint in instant_sent[-200:]`. entry_call also writes consumed_calls.jsonl and dedupes by mint. Residual risk: two separate processes could pass the `os.path.exists(LOCK)` check in the tiny window before either writes it (no atomic O_CREAT|O_EXCL), but the instant_sent/consumed checks make a second *card* unlikely and a second *buy* near-impossible. |
| **Echo-chamber confluence** (raw channel counts without caller-independence / price-extension gate) | **TRUE — real gap** | confluence.py counts raw per-channel mentions in a 1800s window with NO caller-independence weighting and NO first-seen-MC/extension check. The doc's "confluence = buy the top of a coordinated dump" concern is valid. rich_card.py:66 uses it; paper_tick sizes $25 at conf≥2. No M_now/M_0 gate anywhere. |
| "Confluence on price already +150% on 1st call" | **Not handled** | No first-seen price/MC persistence; nothing compares current vs first-call price. TRUE. |

## Stage 1C — Backtesting / tape-audit / look-ahead claims

| Claim | Verdict | Evidence |
|---|---|---|
| replay_backtest exists; uses real curated calls + replay tape | **TRUE** | replay_backtest.py: replay.pumpapi.io archives, calls.jsonl entries, simulates entry at call_ts + LATENCY_MS=1500, Jito-bundle grouping (±3ms), EXIT_TIERS ladder, 3% fees, per-mint PnL vs live outcome. |
| **Look-ahead bias** in reclassify_unsellable + replay_backtest | **NOT PRESENT in replay_backtest** | replay_backtest evaluates **point-in-time tape prices** (the replay stream at the simulated exit timestamp) — it never uses today's live liquidity. reclassify_unsellable is a manual one-off book fix for 5 proven-up write-offs (uses the recorded mult from the trade row, not today's route state). |
| Root script sprawl | **TRUE, worse than claimed** | 29 root .py files. The doc's refactor suggestions (move confluence→bot/intelligence, tape/backtest→bot/research) are reasonable; bot/ modules are real but underused by these new scripts (they import from root, not from bot/). |

## Stage 1D — Rich card / escape claims

| Claim | Verdict | Evidence |
|---|---|---|
| rich_card.py + call_notifier updated | **PARTIAL — location differs** | rich_card.py (10,976 B) + call_notifier.py (6,766 B) live in the **profile scripts dir**, NOT repo scripts/. Repo has no scripts/ dir. |
| **Markdown/HTML escape crash on weird tickers → 400 "can't parse entities"** | **NOT PRESENT** | call_notifier.send_dm posts `{"chat_id": ..., "text": chunk}` with **NO parse_mode** → Telegram treats it as plain text; markdown metacharacters (_*[]` etc.) are harmless. No html.escape needed and none used; the claimed 400-crash path cannot occur with this payload. Chunking ≤3800 chars is the only guard present. |

## Overall verdict

The attached doc is a **partially-accurate, partially-hallucinated forensic report**:

- **CONFIRMED real findings:** (1) instant_trigger bypasses bot/decision_engine/risk_gate (gap worth closing); (2) confluence has no caller-independence or price-extension gate (echo-chamber risk real); (3) root script sprawl (29 files); (4) new backtest/tape-audit tooling exists and is point-in-time (no look-ahead); (5) rich cards live in profile scripts, not repo.
- **FALSE / unverifiable claims:** "50-byte stubs" for lifecycle/microstructure (they're 2.7KB/2.5KB implemented); "Jupiter sell-route verifier" model for exit_verify/unsellable_verify (they're PumpAPI tape auditors); "$10-vs-position-size honeypot trap" (exit_intel quotes actual held qty); "429→UNSELLABLE misclassification" (429 → None → heuristic fallback, never UNSELLABLE); root .py count 23 (actual 29); "rich_card.py in repo scripts/" (it's in profile scripts).
- The doc's Stage 2/3 product suggestions (impact asymmetry ratio, organic-volume share, slot-0 bundle %, PumpSwap lifecycle, first-caller attribution, MCP/JSON CLI) are competitive research — not claims about current code, so not "wrong", but several map to modules the doc claims are stubs (lifecycle.py, microstructure.py) which are already implemented.

Note: this verification reflects the live tree as of 2026-10-19 23:xx CST, including fixes applied earlier this session (signals.py helius_asc_txs import; supervisor wall-clock timers). The doc compared commit snapshots; the live tree may differ from e87928c9 (e.g. signals.py modified after that snapshot).

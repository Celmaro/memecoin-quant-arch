# FOURTH AUDIT VERIFY — Oct 23, 2026

Source: external deep audit of `Celmaro/memecoin-quant-arch` @ `9f595b7` ("fresh public-main snapshot").
Method: every finding verified against the LIVE tree first (public repo is a mirror), internet
consulted for external API claims (Shrine stream). All fixes regression-locked (R25, 26 locks total).

## Status legend
FIXED    — confirmed at correct production-import path + R25 lock + synced to public repo
ALREADY  — verified present in current code (audit snapshot stale or misread)
DEFER    — real but architectural; needs dedicated refactor, not a mid-tick patch
N/A      — policy/evidence item, no source defect

## P1 findings

| # | Finding | Verdict | Fix |
|---|---|---|---|
| 3.1 | is_token_mint substring search: token ACCOUNT parsed.info.mint makes `"mint" in str(data)` true | FIXED | discriminator-only (`parsed_type == "mint"`); raw/base64 = UNKNOWN (None, not False); RPC error stays distinct; caller already permissive on None (freshness gate catches wallets) |
| 3.2 | dex_pair `pairs = sane or pairs` restores rejected candidates when ALL look bad | FIXED | `if not sane: return None` — all-bad answer is UNKNOWN, never a poisoned largest-liquidity price |
| 4.1 | entry_call AEGIS veto/boost vs auto_buy buy-all — silent policy split (instant path could report aegis_veto while cron bought the call) | FIXED | unified per user mandate: AEGIS never gates/sizes in either path; score stays card context |
| 5.1 | equity valued any position without a current-tick mark at ZERO | FIXED | per-position last_mark_px/last_mark_ts/last_mark_source persisted at mark time; equity uses last-valid mark; publishes equity_quality COMPLETE/PARTIAL + unmarked_positions + oldest_mark_age_s |
| 8.1 | deep_audit round-3 fix left `EXIT_TIERS[0]` for EVERY trade | FIXED | tier_for(call_mc USD) at fill (matches live MC-adaptive policy) |
| 8.2 | flow_filter+deep_audit fee on PROFIT not PROCEEDS: 0.75× stop w/3% fee = −24.25% claimed, true −27.25% | FIXED | proceeds math `mult*(1−fee)−1` per basis fraction in BOTH scripts; tier at call_mc |
| 8.3 | tape_coins passes SOL marketCapQuote into USD tier_for | NOT REAL (already handled) — `fill_mcq = mcq * SOLUSD` (line 93) converted to USD; the audit misread the name as raw. BUT the unit-ambiguous name is a real trap: renamed fill_mc_usd + explicit `fill_mc_unit: "USD"` output |
| 8.4 | replay_backtest ladder ≠ exact live rules | PARTIAL | imported live tier_for + SOL→USD already correct (verified fee math is proceeds-correct); remaining gaps (conviction/TTL/sideways/failed-sell states) are documented approximation limits, not fixable without full monitor re-implementation — DEFER to shared-policy refactor |
| 8.5 | deep_audit_rows.json/tape_coins_rows.json stale (+19.4/−14.55 proxies) — old output labeled current | FIXED (hygiene) | result artifacts untracked from repo; .gitignore now blocks; regeneration needs raw tape + manifest — deferred to research-pipeline phase |
| 7.1 | learner: most-recent scan can be POST-entry (leakage); majority-votes incompatible paper+shadow labels; no holdout | FIXED | EARLIEST scan per mint (pre-entry snapshot); paper attribution = primary label (partial fills aggregated by attribution), followups reported separately, never mixed; chronological 70/30 train/validate with per-flag MATCH/FLIP agreement; SHADOW MODE default — live overrides frozen until human sees holdout and runs --apply |
| 6.5 | learned overrides auto-applied daily despite leakage/small N | FIXED | maintenance runs learner WITHOUT --apply (shadow only); live overrides FROZEN |
| 5.2 | exit_intel FEE_BPS=1.7 ⇒ estimate vs executor 3.0 book PnL disagree by 1.3%/exit | FIXED | exit_intel imports PAPER_FEE_PCT from scaleout (single source) |
| 4.2 | notifier MAX_POST counts output LINES; handled_keys lexically sorted | FIXED | calls_delivered counter (per-call); handled keys retained in ADD ORDER (recency) |
| 4.3 | instant_trigger marks CARD_SENT before completion — crash loses card forever | FIXED | IN_PROGRESS lease (15 min expiry, reclaimed) → commit to instant_sent ONLY after send_dm confirms |
| 4.4 | retraction pre-scan only as wide as the 80-line tail | FIXED | pre-scan ALL lines for retraction markers; 80-line tail only for age-gated candidates |
| 3.3 | mkt_ts = request-start, not source observation time | FIXED(core) | pumpdata snapshots expose observed_at (source row t) + received_at; full MarketSnapshot refactor DEFER |
| 5.8 | cohort qualifying: WR≥45% OR pnl>0 lets positive-PnL low-WR wallets in | FIXED | AND (matches doc filter PnL>0 ∧ WR≥45%) |

## P2 findings

| # | Finding | Verdict | Fix |
|---|---|---|---|
| 3.4 | pumpdata stall watchdog only reset by trade events (paywalled); sol_price pushes don't exist | FIXED | SOL-price handler refreshes heartbeat; verified by 70s probe that Shrine does NOT push periodic sol_price (one snapshot per subscribe) → when plan_required known, stall circuit SKIPPED entirely (keepalive continues); graceful fallback polling serves prices |
| 2.2 | 12 runtime artifacts still tracked (lock.0, pid, credit_budget, rows.json, reclass_bak, ...) | FIXED | .gitignore patterns extended (`*.lock.*`, `paper_book*.json.*`, dotfile probe, generated outputs); `git rm --cached` 12 files (live disk untouched) |
| 9.2 | wallet_history PK (wallet,mint,ts) doesn't serve cohort (mint,ts) hot query | FIXED | idx_wh_mint_ts (mint, ts DESC) in schema + migration |
| 6.2 | cat_bad counts positive flags as bad (registry missed holder_growth_/trusted_token/micro_cvd_pos_/deep_pool_liq_mc_) | FIXED | registry extended with REAL emitters only (verified against flags.append sites; a speculative prefix was caught and removed) |
| 5.3 | MAX_HOLD comment says "hard time exit" but code fires only on losers | FIXED (naming) | MAX_LOSER_HOLD_S alias + corrected comments in monitor + fallback engine; behavior unchanged (loser-only by design) |
| 3.5 | http_json retries doc mismatch; RateLimiter no mutex; budget() no interprocess lock | DEFER | doc-normalization + lock for shared limiter/budget is a persistence refactor (event ledger phase) |
| 9.3 | wallet_history balance column = quantity OR USD notional by event string | DEFER | split schema (quantity_raw/notional_usd/…) — migration phase |
| 9.4 | distributed schema writers | DEFER | single schema_version migration layer |

## Already-correct / audit-snapshot-stale claims
- 3.2's specific "no-liquidity" branch: dex_pair already returned None when pairs empty (verified) — the real bug was only the all-rejected fallback.
- 8.4 fee math: replay_backtest line 202 was ALREADY proceeds-correct (`(mult-1)*100 - mult*FEE_PCT` ≡ `(mult*0.97-1)*100`).
- No private keys / API tokens found in git history (scanned all blobs; hits are f-string templates + doc placeholders, no literal credentials) — confirms audit's own "not proven" line.

## Honest deferred (documented, not dropped)
- 2.3 book_lock spans network-heavy tick; `_BOOK_LOCK_HELD` not thread-reentrant → load→analyze→commit-CAS refactor
- 3.3 full MarketSnapshot type (quality/fallback_reason) — snapshot sharing phase
- 3.5 shared rate-limiter mutex / budget locking / per-endpoint cost metadata
- 7.1 remaining ML: confidence intervals, multiple-testing control, frozen-baseline shadow runs ≥2 windows (learner now shadows so we can accumulate evidence safely)
- 8.4 full monitor-parity replay (conviction/TTL/sideways/failed-sell states)
- 8.5 result regeneration w/ run manifest; 9.3 unit-split schema; 9.4/9.5 event-ledger migration
- History rewrite (P0 blobs from earlier sync commits) — still needs user approval

## Verification
- 15 files compile; regression suite 3× `FAILURES: NONE` (26 locks: R23/R24/R25)
- Live ↔ arch byte-identical (cmp all 15); notifier identical to profile-live canonical
- aegis_learn run: 482 paper samples, base win 25%, holdout 337/145, FLIPs surfaced — shadow file written, live overrides untouched
- Public repo: 12 runtime artifacts untracked, .gitignore extended, 0 tracked .pyc/runtime state (re-verified)
- git history secret scan: no literal credentials (templates/placeholders only)
- desk processes healthy pre-restart; restart pending to load fixes

## Remaining honest gaps (unchanged from round 3)
- CI workflow + hermetic offline tests (clean-checkout gate)
- One versioned ExecutionCostModel shared live+research
- Canonic SQLite ledger; JSONL/JSON as export/audit only
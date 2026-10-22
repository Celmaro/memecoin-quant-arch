# THIRD AUDIT VERIFY — Oct 22, 2026

Source: external deep audit of `Celmaro/memecoin-quant-arch` @ `b0d2d6c` (before this round).
Method: every finding verified against the LIVE tree code first (the public repo is a mirror),
internet consulted for external API claims (Jupiter Swap V2). All fixes regression-locked (R24).

## Status legend
FIXED   — confirmed by code at correct path + regression lock + synced to public repo
ALREADY — verified present in the live/current code (audit snapshot was stale)
DEFER   — real but architectural; needs a dedicated refactor, doing it mid-tick vs live book is risk
N/A     — policy/evidence item, no source defect

## P0 — repository hygiene (FIXED, pushed 4028ee6)
- .gitignore was 6 lines; sync commit `1adf1c3` pulled unrelated trees (blixtrade/, nought/,
  Solana-Forensic-Analysis-Tool/ = 285,924 lines) + runtime artifacts (notifier_state.json,
  telethon_state.json, .audit_scoring_cache.json, .instant_trigger.lock.0, calls.trigger,
  deep_audit_rows.json, tape_coins_rows.json, 42 tracked .pyc).
- Now: conservative .gitignore (647 B) untracks CONFIG.md/.env/DBs/runtime/pycache/unrelated
  trees; all 42 .pyc + runtime state json removed from index; unrelated trees removed from repo
  (live disk untouched). No private keys/API secrets were found in the added blobs by grep audit.
- Remaining: a deletion commit does NOT remove historical blobs. For full P0 closure, a
  history rewrite (filter-repo) of the two sync commits is the right follow-up — deferred,
  needs user approval + a fresh secret scan on the rewritten history.

## P1 findings
| # | Finding | Verdict | Fix |
|---|---|---|---|
| 7.1 | scan_log DDL lacks flags_json (fresh DB breaks aegis insert + learner select) | FIXED | flags_json in DDL + idempotent migration + verified on live DB |
| 7.4 | no indexes on scan_log(mint,ts) / wallet_history(mint,ts) | FIXED (scan_log) | idx_scan_log_mint_ts added; wallet_history PK already (wallet,mint,ts) — cohort query partial; DEFER dedicated index pass |
| 2.1 | daily stop is advisory text, not a control | FIXED | stop_until persisted in book on day_pnl<=-20; auto_buy + entry_call return daily_stop_* before any quote |
| 2.2 | instant_trigger drops its AEGIS score; auto_buy has no veto | FIXED | instant_trigger passes aegis_score=score (entry_call veto/boost now applies on instant path). auto_buy stays buy-all per user mandate (documented, not a bug). |
| 2.4 | notifier watermark = max_fresh_ts even when 4-cap stops early → same-ts tail lost | FIXED | watermark only advances when the cap did NOT bind; capped run re-scans, handled_keys dedupes delivered prefix |
| 6.3 | Jupiter probes non-existent /swap/v2/quote first (no such route; V2 = /order or /build) | FIXED | direct V1 quote (keyless estimate for paper desk); comment documents V1-deprecated → migrate to /order when execution goes live. Internet-verified. |
| 6.7 | token_decimals `if dec` → 0-decimal mint becomes 6 | FIXED | `dec is not None` |
| 6.8 | research ladders stale: deep_audit 1.10/1.30/0.15 proxy; flow_filter +19.4/−14.55; tape_coins tier[0] only; replay own EXIT_TIERS copy | FIXED | deep_audit + flow_filter + replay import live EXIT_TIERS/tier_for; tape_coins selects tier at fill MC; proxy PnL recomputed from live tier |
| 6.6 | quote_fill daemon thread unbounded | FIXED | process-wide BoundedSemaphore(4); worker releases slot in finally (abandoned workers still free pool) |
| 5.1 | GoPlus transfer_hook via bool() → '0' truthy −20 penalty | FIXED | _gp_flag() normalization (verified _gp_flag handles '0'/'1'/dict.status) |
| 5.2 | fragility reads raw `top10_pct` (RugCheck exposes topHolders) → +30/+15 branch dead | FIXED | _rc_top10_pct() derives from topHolders, mirrors aegis.check_top10_and_lp |
| 5.6 | micro buy_concentration ≥0.6 mathematically unreachable (k/n ≤ 1/3) | FIXED | top-5 wallets' share of buy USD |
| 5.7 | cohort consumer only counts buy/BUY; producer writes buy_usd_notional | FIXED | vocabulary normalized both directions |
| 3.1 | clean_snapshot +15 rides on adverse flags (list incomplete + case-sensitive) | FIXED | case-insensitive match + 18 new negative prefixes |
| 3.2 | ave_top1 dedup refunds 12 for a −6 charge | FIXED | refund 6 |
| 3.3 | cat_bad counts positive flags as bad (only 4 good prefixes) | FIXED | explicit _GOOD_PREFIXES registry (18 prefixes) |
| 3.4 | remember_deployer increments coin_count per rescan (not per mint) | FIXED | deployer_mints unique-ledger; coin_count counts unique launches |
| 4.3 | overrides file: no generated_ts age check; no total bound; key mismatch (transfer_fee_present vs _2.0%) | FIXED | 14-day age gate (manual files kept), ±15 total guard w/ rescale, prefix-normalized key match |
| 7.2 | maintenance queries evidence_events/wallet_history before they exist on fresh DB; ignores learner rc | FIXED | ensure_schema() first; subprocess returncode checked, stderr surfaced |
| 7.6 | entity CLI queries fb.mint / c.first_seen (never existed) | FIXED | fb.coin_mint / c.call_ts |
| 6.9 | backtest drops >99% crash events silently (can erase real rugs) | FIXED | counts filtered events; deep_audit appends FILTERED_CRASH_COUNT marker; replay prints total |
| 1.2 | block_fields() shadowed twice; root duplicate modules | FIXED | shadowed (narrower MC: only) definition removed; 6 arch root duplicates deleted (live only has shims/nested) |
| 8.4 | pumpdata cap trims by arbitrary set order → could evict open positions | FIXED | comment documents positions-first ordering (read_watchlist already builds positions-then-calls); explicit sort guard |
| 8.6 | requirements missing python-socketio / zstandard; no CI | PARTIAL-FIXED | requirements declares both; CI job still DEFER |

## ALREADY-current (stale audit snapshot)
- 4.1/4.4 — aegis_learn already picks the MOST RECENT scan per mint and prefers stored flags_json;
  `has_fj` computed but query unconditional → the 7.1 DDL fix makes this moot (column now always exists).
- 2.5 — retraction pre-scan covers ALL lines, not just tail-80 (verified at lines 340-347 paper_tick).
- 7.1 migration wording — live DB had the column via prior manual ALTER; now DDL+migration make fresh DBs work.

## DEFER (real, needs dedicated work — documented, not silently dropped)
- 2.3 — book_lock held across the whole tick (network work under lock). Real: an entry_call can wait
  behind a tick. Fix requires split load→analyze(outside lock)→commit(CAS/version). High risk to apply
  ad-hoc against a live book; scoped as a dedicated refactor with a lock-contention test.
- 7.3 — three position stores (paper_book/positions.json/SQLite positions). Canonical-SQLite migration.
- 8.2 — rate limiting is per-process; a persistent provider budget needs design.
- 8.3 — `except Exception: pass` neutrality: AEGIS data-completeness report is a feature, not a bug-fix.
- 4.2/9.x — learner is a heuristic calibration, not validated ML; needs holdout/CI/out-of-sample edge proof.
- 6.1/6.2 — fee model: 3%/side only on exit; entry-side cost and gross-vs-net equity reporting need one
  versioned ExecutionCostModel shared by executor + all research tools.
- 5.3-5.5, 5.8, 5.9 — metric unit/provenance refinements (vol interval, fresh-wallet baseline,
  holder-churn page semantics, cohort qualifiers, cache observation ts) — P2 quality items.

## Verification
- All 17 edited files compile (py_compile) — live + arch byte-identical.
- Regression suite: FAILURES: NONE ×3, including 24 new R24 locks.
- Public repo re-verified at the exact audit paths (`def release` 4-arg, `qty_raw`, `aegis_score=score`,
  `top5_share`, `stop_until` ×9) — all present at `bot/...` production paths.
- Public .gitignore now protects secrets/runtime; 0 tracked .pyc; 0 unrelated trees.
- Live desk restarted (`systemctl --user memecoin-desk.service`, user-level unit), all children
  respawned, heartbeat fresh, book consistent (cash 92.28 / equity 156.75 / 4 open / 13 trades).
- Live aegis.db migrated: scan_log.flags_json present, idx_scan_log_mint_ts present, deployment
  double-init tested idempotent.

## Remaining honest gaps
- History rewrite for the two sync commits (P0 blobs) — needs approval.
- CI workflow + hermetic path tests (Phase 4 of the audit).
- One versioned policy/cost model shared by live + research (Phase 1).
# Memecoin Quant Bot — Exhaustive Audit (module/file/claim/formula/tool/algo/data)

Date: 2026-10-19 (night pass). Method: 4 parallel deep-audit subagents (AEGIS/entity, exit-engine, ingestion/forensics, tools/data) + parent verification of every contested claim + live regression + daemon restart. Exit-engine cluster was rate-limited and re-verified directly by parent. Everything below cites file:line.

Compile: ALL live .py files py_compile clean. Regression suite: **pass, FAILURES: NONE** (incl. Doc1-4 2-read confirmation + Doc1-4b liq=0 never-write-off).

---

## 1. DATA (aegis.db, state files — read-only snapshot)

- integrity ok, WAL active, FK 0 violations, 13.8MB.
- 21 tables. Rows: coins 1107 (call_ts/source populated 1088 — F3 working), call_metrics 654 (frozen — writer retired), followups 387 (**0 undone** — F2 drained), scan_log 1604 (CAUTION 769 / RISK 656 / LIVE 190), deployers 666, first_buyers 2168 (**dups 0**, unique idx idx_fb_uniq present), funding_links 561, wallet_entity_clusters 5338, wallet_labels 191, wallet_history 14011, decision_audit 904, evidence_events 716, market_events 187, position_attribution 187, leaderboard_snapshots 300, deep_cache 111, positions 5 (stale ledger — see §7), wallets 2, actors 165.
- calls.jsonl 3322 lines / 3143 unique mints; last 5 ts 1792416467→1792417470. telegram_messages.jsonl 7862 lines / 7.2MB — **6811 (87%) are ingestion noise** (victory laps, promos, updates).
- paper_book.json: cash 69.25, equity 86.31, realized −12.62, 0 open positions at final read; 29 trades/16 closed; pnl range −12.06..+21.84. positions.json in sync (both empty). holder_snapshots.json 1.9MB (60-mint cap). consumed_calls.jsonl 100 lines. credit_budget.json: fomoapi 2500, ave_top100 200, walletintel 10 — **all exhausted today** → those layers silently degrade for the rest of the day.

## 2. AEGIS SCORER (aegis.py) — check-by-check truth

- Base 65 (A:301), clamp [0,100], verdict <40 RISK / 40-60 CAUTION / ≥60 LIVE (A:879-882). "No check > ±9" is **false** — deltas span −40 (RUGGED) to +10 (trusted_token).
- Full 50+ flag table in subagent A. Headline deltas: mint_authority −25, freeze_authority −15, RUGGED −40, insider_graph −10, transfer_fee −10, bundle/sniper −15 (deduped), lp_unlocked −15, copycat −10, top10>70% −10, lp_locked<50 −10, Ave honeypot −30, transfer_hook −20, non_transferable −15, thin_pool_liq_mc(liq/mc<0.03) −15, frag>70 −5, churn retention<0.6 −8 / >0.85 +5, entity_labels (exch −10 / rug −15 / mev −5, uncapped), whale_holds_top10 −8×min(3), narrative heat≥70 +6, clean_snapshot +15, deployer_offset [−25, +5].
- **deployer_offset real range [−25, +5]** (A:173-182) — docstring "±25" wrong on positive side; +5 is the max. rug_count only incremented by external `tag_actor(...rug_deployer)` — nothing in scan flow does it.

### Bugs confirmed (P1)
1. **clean_snapshot +15 awarded despite negatives** — `_NEG_PREFIXES` (A:867-873) misses ~15 flag families (ws_sell_pressure, thin_pool_liq_mc, holder_shrinking, lowercase ave_*, gmgn_*, rug_insider_nets, blix_top10, whale_holds, entity_labels, transfer_hook...). Case-sensitive startswith: list has uppercase `AVE_` but flags are lowercase `ave_*`.
2. **remember_deployer inflates coin_count per scan** (A:124-134) — no distinct-mint guard → rescan pumps count → false +5 clean-deployer offset + inflated "N launches".
3. **Check 23 (sol.wales) ~dead** — `out["topHolders"]` never set by check_top10_and_lp (A:44-66) → A:793 iterates [] ; `th` bound only inside check-15's try → NameError swallowed (A:808). Only Ave holders reach the intersection.
4. Entity-label −15 stacks with deployer_offset −5 and known_rugger −10 — same fact triple-counted.
5. cat_bad/cat_good sub-scores polluted: positives (ws_hot_buys, micro_cvd_pos, churn_high, holder_growth, fresh_wallet, blix_dev_flat, fill_conviction, wi_strong) counted as **bad**.

## 3. ENTITY LABELS (aegis_entity_labels.py)

- Tier precedence **metasleuth > vybe** (ael:227-240) — module docstring line 5 says the reverse (drift).
- helius_first_funder: single helius_txs limit=100 → earliest incoming native SOL (toUserAccount==wallet). deployer_rug_lookup: funder rug_count>0 → "N rugs of M launches" rug. behavioral_labels: rank≤1 ∧ n_coins≥2 → snipe bot / rank≤1 → sniper; `ts_seen` unused.
- annotate_wallet_risk: exch −10 / rug −15 / mev −5 per labeled first-buyer, **no cap** (×6).
- CLI `--scan-first-buyers` broken: selects `fb.mint` (column `coin_mint`) + `ORDER BY c.first_seen` (no such column) → OperationalError (ael:267-269).
- funding_links depth-2 skipped whenever (funder,w) already exists; conn leaked on exception (no finally).

## 4. SIGNALS / FORENSICS (signals.py) — P0 fix applied

- **P0 FIXED**: `helius_asc_txs` was called but never imported (signals.py:20, import at L4) → NameError swallowed → the documented "Helius asc fast path" NEVER ran; every fill-time estimate paid ≤20 paged RPC calls. Patched import (`from desk_common import ... helius_asc_txs`). **This was the biggest silent lie in the codebase.**
- fragility_score: `rc.get("top10_pct")` reads raw Rugcheck JSON which has no such key → the top10>70% +30 / >50% +15 branches **never fire** (P1, unfixed — needs desk_common.rugcheck to surface topHolders-derived pct or reuse aegis's computation).
- holder_churn: retention = |t0∩t1|/|t0|, 60-mint cap OK — but **no built-in 5-min throttle**; position_monitor calls it **every tick** in analyze (P1, at position_monitor.py:392; the throttle comment gates only holder_growth).
- wash_lpi_check: CV<0.3 interval-uniformity **not implemented** (comment admits; LPI approximated by shape).
- ohlcv_risk docstring says log returns, computes simple returns (A:153 vs 165).
- estimate_fill_time fallback paged ≤20 pages; fine.

## 5. TELEGRAM INTAKE (telethon_listener.py)

- CA extraction: base58 regex `\b[1-9A-HJ-NP-Za-km-z]{32,44}\b` (L28) → valid_mint (len 32-44, not SOL, not 0x, not all-digits) → believable_mint (RPC is_token_mint owner+type, cache, permissive on RPC-down). URL mints via get_entities_text + button URLs **excluding /account/** (wallet-shaped); **dominant (most-frequent) mint wins**; digest blocks split per line with a mint; **two-CA line drops the 2nd** (break at L301, P2).
- Noise filters checked first: "is up X", "x2.2 in 11m", "✅ X", premium/promo, "update for", ranked digests. call_kind: launch/whale/alert(🔔)/seekr.
- Freshness gate: ≤300s and ≥−60s only → calls.jsonl; older → ledger skip=stale.
- Dedupe: is_new_mint full-file scan, first-sighting wins. Retraction: `_emitted_by_msgid` in-memory cap 4000 — **lost on restart**; digest deletion retracts ALL cards of that msg_id (over-broad, P1-5). retractions.jsonl + `{"retracted":true}` appended to calls.jsonl.
- calls.trigger: touched ONLY on fresh emit; supervisor consumes by mtime in a background thread (coalesced).
- Edit-storm throttle: non-call edits → 1 ledger row then dropped for 120s; call edits re-run classifier. `_edit_last` never pruned (P2).
- social heat: views/forwards/fwd_from/reactions captured best-effort.
- reconnect_watcher: `client.disconnected` → connect → catch_up, 5s. FloodWait handled **only** around catch-up (live handler relies on Telethon internals).
- Catch-up is bounded: `iter_messages(limit=30, min_id=last)` — older missed calls lost (P1-6).
- Channels actually monitored (exactly 7, all with live watermarks): sol_trend, solwhaletrending, solearlytrending, PIRBViewTrending, soltrenchtrending, SeekrTrending, signalsolanaby4am. HMG excluded correctly. CONFIG.md's "t.me/s/ fallback/probe" has **no live code** (archived).

## 6. CURATOR / CONFLUENCE / NOTIFIER

- Collect window is **45s** (comment "was 90"), not 90. Bot registry hmq_bots.json, card parse generic + specialists; **"TELEGRAM_BOTS_REQUIRE_MENTION" does not exist** — functional equiv is mint substring in text; discovery branch collects any card-looking message within ±300s window (documented as harmless but the claimed gate is fiction).
- Channel attribution via daemon postbox/mint lookup; posts via **bot token** to group + DM.
- Confluence: window_s=1800, mint→channel from telegram_messages.jsonl, **calendar.timegm correct** (UTC), feeds paper_tick sizing conf≥2 → $25 else $20.
- **P1-4**: `call_notifier.save_state({"last_ts": next_ts})` **wipes `instant_sent`** dedupe (desk_supervisor merges correctly; notifier doesn't) → re-emitted calls can double-card the DM. **P1-8**: notifier's fresh-loop does NOT skip `retracted` lines → dead-mint card noise.

## 7. EXIT ENGINE + PAPER (position_monitor/paper_tick/scaleout/bot) — parent-verified

- EXIT_TIERS (position_monitor.py:1134): (0, 1.10, 1.30, 0.15), (40K, 1.15, 1.35, 0.18), (150K, 1.20, 1.45, 0.22), (1.5M, 1.30, 1.60, 0.28). CONVICTION_MULT 1.45, stop widen 1.15.
- tier_for picks tightest floor ≤ mc. Hard stop: `hard_stop_mult` default 0.50 → mult ≤ floor → EXIT_ALL, non-overridable (pos["hard_stop_triggered"]). Missing-liq: no stored entry_liq ∧ liq>0 ∧ mult≤0.75 → EMERGENCY_EXIT (emergency_triggered sticky). Liq collapse: liq < 50% entry → EMERGENCY. Liq=0 → DEAD. Volume drying <40% entry after 20min → CLIP_50 / EXIT_ALL ≤1.05x. Round stall 85-98% of ROUND_NUMS. Stale rule >3h → close.
- Scaleout (scaleout.py): stages = target-remaining ratio of ORIGINAL (R1=.5, R2=.25, R3=0); sell_delta = max(0, current−reserved−target); PAPER_FEE_PCT=3.0 → proceeds = delta×mult×(1−0.03); MIN_TRANCHE_USD=2.5 skip unless closes; MIN_EXIT_LIQ_USD=1000 + **2 consecutive low reads (or rugcheck dead) before UNSELLABLE total loss** (Oct-19 fix — single 0 no longer writes off; Doc1-4b); closed = entry_usd<0.01 (residual tracked, never pop on stage==3 alone); stage 1 → state "half"; reservation by intent_key `{sym}:{how}{stage}` (P1 isolation verified by regression); `_rugcheck_confirms_dead` fast-path allowed on first low read.
- Decision→stage mapping: SCALE_OUT/CLIP_50=frac cfrac or stage1 0.5, CLIP_100=stage1 1.0→stage3, FULL_EXIT/EXIT_ALL/EMERGENCY/DEAD + cfrac≥0.99 → stage3 full. Full-exit intents sticky.
- bot modules inspected: evidence_ttl w = conf·2^(−age/half) (classes 2s..7d), risk_gate sellable=current−reserved, stale quote >120s WAIT_RECONCILE, monitor_tiers CRITICAL/HIGH/NORMAL/BACKGROUND (moonbag ≤25% skips deep 9/10 ticks), exit_intel Jupiter sell-route quote, attribution MFE/MAE/PRR/IS_exit/DVA.
- **Regression suite now pass (NONE)** — incl. the Doc1-4 2-read + Doc1-4b tests. Earlier mid-fix/prov flake was transient. Note: suite is network-flaky by design (fake mints + live dex_pair → verdict flips with provider health); consider making fixtures hermetic.

## 8. TOOLS / PROVIDER (desk_common.py + friends) — endpoint truth

- 40+ helpers audited (table in subagent D). **Dead code**: helius_wallet/identity+funded_by (0 callers), bitquery_* (0 callers), madeonsol_kol_feed, cabalspy_wallets/connections, dexpaprika_reserves, blix_overview/token/quote/signals/perf/research/wallet, gmgn_lookup/summary, coinstats_balance first def (shadowed by 2nd).
- **Dead-live**: solscan_chaininfo 401 (JWT rejected/expired), dexscreener_metas_trending 403 from this box. Both soft-fail.
- **Budget gaps (paid calls that skip budget())**: fomo_holders/user/board (aegis fomo_overlay) + ave_risk (aegis.py:343) run after their budgets are exhausted; walletintel_leaderboard ungated; madeonsol/cabalspy ungated (also dead).
- Budget accounting undercounts (helius_wallet 2 calls records 100; fomo_devs 2 calls records 250), no refund on failure, `_day()` CST not UTC (resets 16:00 UTC).
- Rate limiter only throttles 6 hosts — **fomoapi/walletintel/cabalspy/madeonsol/sol.wales/solscan/shyft/jup/bitquery unthrottled** despite "centralized gate" claim.
- `duplicate coinstats_balance` definition (dk:377 & 557 — 2nd shadows 1st); jupiter quote_sell only v1 while buy tries v2→v1; `positions` triple-ledger (book/positions.json/DB) — DB stale by design.

## 9. SUPERVISOR / TIMERS — P2 fix applied

- **P2 FIXED**: `tick % 1440 == 500` maintenance ran **~hourly** (2s deadline-sleep, not daily); `tick % 30` followups ran **~every minute**, not 30 min. Both converted to wall-clock: next_followups = now+1800, next_maintenance = now+86400. (whale watch `tick % 3` is the intended ~every 3rd tick — left as-is.)
- send_dm: absolute hermes + HERMES_HOME env (systemd unit has it) — verified working; flock lock verified (2nd supervisor exits).
- scan_new_calls gates: FRESH_S=300, ALERT_CAP=5, wash gate (≥$5k/5m ≤3 wallets skip; ≥$8k <8 wallets whale-only skip), dead-skip on no pair+no GMGN.

---

## Consolidated fix list from this audit

### Applied now
1. signals.py: import helius_asc_txs (P0 — dead fast path resurrected).
2. desk_supervisor: followups 30-min wall-clock + maintenance 24h wall-clock (P2 timers).

### Recommended next (priority order)
3. clean_snapshot `_NEG_PREFIXES` — add the ~15 missing flag families or invert to an explicit allowlist (P1, scoring integrity).
4. remember_deployer distinct-mint guard (P1 — false clean-deployer +5s).
5. call_notifier.save_state preserve `instant_sent` (P1 — double-card DM risk).
6. holder_churn 5-min throttle inside signals or position_monitor (P1 — per-tick disk+cost).
7. fragility top10 feed — reuse aegis's computed top10 (P1 — dead branch).
8. Notifier skip retracted lines (P1 — dead-mint card noise).
9. sol.wales check 23 topHolders forwarding (P1 — dead check).
10. Budget gates on fomo_overlay + ave_risk (P1 — exhausted-provider burn).
11. Entity-label CLI column fix + docstring precedence + dedupe rug signal (P2).
# FULL LEARN REPORT — 4 Trading-Bot Repos (Oct 21 2026)

Studied in depth (cloned + read code, not README-only):
1. **Kaiser.charon** (fork of yunus-0x/charon) — Telegram pump.fun screener: dry_run/confirm/live modes, 8 signal sources, LLM decision layer, SQLite hot-reload config, learning loop, backtests, static audit. 9,426 lines JS.
2. **chainstacklabs/pumpfun-bonkfun-bot** — production Python bot: platform-aware buy/sell across pump.fun/letsbonk/StonkFun/LaunchLab, priority-fee manager, cleanup modes, event parsers. 14,130 lines + cookbook.
3. **mortdeus/solana-copy-sniper-mev-trading-bot** — Node/Rust sniper+copy+MEV: gRPC/geyser/Helius/shred variants, Jito tips, risk manager, dashboards.
4. **nirholas/pump-fun-sdk** — community TS SDK + huge multi-tool repo (swarm, dashboard, skills, vanity): offline-first instruction builders, bond-curve math, token lifecycle.

Covers every layer: signal pipeline → filters → scoring → LLM → entry → monitoring → exits → learning → execution mechanics → cleanup → config → risk → Telegram UX → backtesting → audits.

---

## 1. SIGNAL PIPELINE (what feeds decisions)

### 1.1 Multi-source, route-tagged signals (Charon)
8 sources, each tagged with a `route`: pumpportal_graduated (WS graduated stream), pumpfun_pregrad (pre-grad scanner), trenches (dip/trenches API), trending (5m/1h/6h), graduated (poll), feeClaim (fee-distribution events = token with fee-sharing activated), smartMoney (copy wallets), axiomSource, gmgnSignal, priceMonitor (dip triggers). Every candidate carries `signals.route` so filters/scoring/learning can be route-aware. **Lesson for us**: we tag channel; route-aware scoring was decisive for them (see §2.5).

### 1.2 Pre-graduation scanner (Charon) — graduation anticipation
Watches `frontend-api-v3.pump.fun/coins` (sort=last_trade_timestamp, limit=200), filters by `real_sol_reserves` (RSSR) window [min,max], age cap, ATH multiple cap, then polls GMGN mcap; fires "GRADUATED" when RSSR crosses max (curve full). This is EXACTLY our tape-proven graduation edge (GRAD-DURING 60% TP2 vs 28-31%) — they built a live watcher for the same signal. `fast_migration_0s` flag = impossible for organic activity → reject.

### 1.3 WS health + tracked-token TTL (Charon pumpportal)
Tracked-token cap (50) + TTL pruning, consecutive-error drop (MAX_CONSECUTIVE_ERRORS), poll delay between GMGN calls. Health tracking counts create/migrate as activity. **Lesson**: our PumpData feed has the stall watchdog; add per-mint consecutive-error drop + cap (we cap at 400 mints, fine).

### 1.4 Copy-trading (mortdeus)
gRPC transaction stream → parse largest-data instruction → detect target-wallet buys → mirror. Risk manager + 5s position monitor + graceful shutdown closes all positions. Simplest form of our call-driven model (we copy curated callers via Telegram instead of chain).

---

## 2. FILTERS + SCORING (how they pick)

### 2.1 The filter cascade (Charon candidateBuilder)
Hard filters → soft score → dynamic threshold → LLM. Hard: fresh-grad data sanity (no jupiterAsset / liq=$0 / holders=0 / fast_migration_0s), fee-claim min, mcap min/max (skipped for fresh grads), GMGN fee min, graduated volume, holder min (skipped fresh), top-holder max, saved-wallet holders, ATH distance (dip strategy), token age (12h default), buy/sell volume ratio ≥1.0, **liquidity ≥ $6K (raised from $2K, backtested, NOT exempting fresh grads)**, trending rug ratio/bundler rate/wash, and the FLOW filter (1h priceChange ≥ 0 + net buyer ratio 5m ≥ 0.2). **Lesson for us**: their liquidity floor + FLOW filter mirror our $10K gate + entry hygiene — independently validated.

### 2.2 FLOW filter (Charon, backtested 1,415 trades, 11 days)
`s1h_priceChange >= 0 && net_buyer_ratio_5m >= 0.2` → 945 kept (67%), WR 47.9%, +14.09 SOL (+3.45 delta), 100% daily consistency. Only rejects when Jupiter data present (never penalize missing data). This is the live-tape twin of our withdrawn push60/bs5m edge — but measured on Jupiter stats not tape-prefill.

### 2.3 Soft score (Charon v45)
0-100 (+ up to 150): liquidity (<$3K −35, <$5K −25, <$10K −10), bot holders route-weighted (pumpportal: ≥80 −40, ≥50 −30, ≥30 −15; trenches more tolerant), bot % (≥50 −25, ≥30 −15), top10 concentration with per-route "rug zones" (pumpportal [15,25) −30; trenches [25,35) −20), dev migrations (≥15 −30, ≥7 −20, ≥3 −5), holder count route-specific, ATH distance, mcap ranges per route, bundler rate, positives: smart degen count (+25/+15/+5), organic score, clean bundler, fresh-grad momentum +10. **Lesson**: our AEGIS score is similar in spirit but theirs is route-aware and empirically calibrated.

### 2.4 Dynamic threshold (Charon)
Soft-score threshold tightens when positions are full: openCount ≥ max−1 → +10, openCount == 0 → −10. **And their B-4 audit finding**: the ESM `require()` bug made this ALWAYS return the loosest branch (20) — dynamic risk controls silently dead. **Lesson**: verify dynamic thresholds actually execute (our P0-1 tier-stop was the same class of bug).

### 2.5 Route quality + trending INVERSION (Charon backtest 1,146 trades)
- Routes: pumpportal_graduated 41.2% WR (+1.87 SOL) / trenches_completed 40.5% (+4.85) / fee_trending 34.6% (+2.17) / graduated_trending 20.5% (−3.24) / dual_source 21.1% (−1.91) / plain trending 22.9% (−0.73) / pumpfun_pregrad 11.8% (−1.38).
- **`hasTrending` flag CORRELATES WITH LOSING** (27.7% vs 40.2% WR). Trending volume floor was INVERTED — higher trending volume = MORE loss (B-1: vol≥5000 → −13.87 SOL vs <5000 → −3.41). They disabled it as a floor; if used, it's a CAP.
- **Lesson for us**: our channel-level audit found the same dominance (solearly 58% vs soltrench 40%). Never use "trending-ness" as an entry booster. The Oct-19 "Confluence trap" is our twin: symbol-level confluence = noise.

### 2.6 Regime-decay methodology (Charon — the gold standard)
Split backtest at median time; a feature PASSES only if it beats its half's baseline on BOTH pnl AND win rate in FULL+H1+H2 with ≥30 trades/half. Result: **only liquidity floor + holder count survived**; mcap floors, trending-volume, smart-degen, bot-degen all FAILED (regime riders, lopsided half-coverage). Regime decayed 40.3% → 25.7% WR with zero config change. **Lesson**: this is exactly how we must re-validate Oct-10 vs Oct-18 (we already do two windows; adopt the strict both-halves gate). Their finding independently confirms our $10K liq gate (+20.6 pts).

### 2.7 LLM decision layer (Charon)
LLM gets compact candidate JSON → verdict BUY/WATCH/PASS + confidence → normalizeDecision: BUY blocked on disabled routes, BUY downgraded to WATCH if confidence < min, low-confidence BUY (<70) → position size halved. **Decision cache** (mint→verdict, 10/60min TTL, invalidate on >20% mcap or >30% holder change) cut LLM calls ~60-70% and eliminated 412/429/402 errors. Pre-LLM filter guard ran filters BEFORE the LLM call (was after — 488 wasted calls). **Lessons**: cache expensive per-mint recomputes with invalidation; run cheap filters before expensive ones; never let low-confidence decisions take full size (we do: AEGIS <30 veto + ≥70 boost).

### 2.8 Learning loop (Charon autoApply) — the ANTI-pattern
Regex-parses free-text LLM "lessons" → mutates ACTIVE strategy config directly (sl_percent, min_mcap, etc.) with no approval gate. Their own audit: C3 CRITICAL-dormant — a loaded gun. One import + cron line from silently retuning live params off fragile prose (inverted tighten_sl band [-15,-12], arbitrary mcap averaging). **Lesson**: our aegis_learn gates deltas (±6/±12) and never mutates hard rules; NEVER auto-mutate strategy/risk config from model output without human approval. This is a live-trading hazard.

---

## 3. ENTRIES + POSITION OPENING (money-spending path)

### 3.1 Charon executeLiveBuy flow (router.js)
Balance check (amount + LIVE_MIN_SOL_RESERVE) → 3 attempts w/ backoff (1.5s×attempt) → on total failure: record FAILED_ENTRY as a CLOSED position (auditable, pnl 0) + Telegram alert → dedup guards in createLivePosition (open-exists / closed<24h / past-win<7d) → position row → notify. **Audit C2 found**: swap happened BEFORE the dedup check in some paths → dedup hit = tokens bought but untracked, never sold. **Our desk is correct**: entry_call dedups BEFORE buying. Keep it; if a post-swap dedup ever fires, record orphaned tokens + alert.

### 3.2 Chainstack PlatformAwareBuyer
- Reads curve state before EVERY buy (fee_recipient/creator_vault/quote_mint/mayhem/cashback); a buy built on listener-guessed accounts reverts on-chain (NotAuthorized/ConstraintSeeds).
- `extreme_fast_mode`: zero RPC between detection and submission, but ONLY if CreateEvent carried canonical data; otherwise refresh-or-skip (curve_refresh_budget).
- Slippage applied on the OPEN side: exact-in program → amount_in fixed + token floor; exact-out (pump.fun buy_v2) → token count exact + amount_in ceiling.
- Token program corrected from mint owner (SPL vs Token-2022) — ATA re-derived.
- On success: parse actual tokens/spent from tx → actual_price; if parse fails but tx landed → **fall back to wallet balance, capped at what THIS buy asked for** (`min(balance, token_amount)`) — never liquidate an unrelated pre-existing position. Balance unreadable → hold expected amount + warn "verify tx before relying on PnL".
- `skip_preflight=True`, max_retries, priority fee from manager, CU overrides per platform.

### 3.3 Priority-fee manager (chainstack — best in class)
`getRecentPrioritizationFees(accounts)` → **70th percentile** of fees (faster than ~70% of txs) → +extra% → hard-cap enforced. Dynamic preferred, fixed fallback, both-disabled = no fee. Config validated: cannot enable both dynamic AND fixed. **Matches CONFIG.md's max ~0.001 SOL cap**; the 70th-pct method is the standard we should use if we ever build live.

### 3.4 Sniper repo (mortdeus)
Jupiter swap with `slippageBps` in URL, Jito tip via `prioritizationFeeLamports{jitoTipLamports}`, VersionedTransaction, maxRetries 1-2, confirm-time measured, **re-confirm after 1s** if first confirm timed out. Risk manager gates every trade: daily net-PnL cap, per-trade loss cap, position cap, cooldown, per-mint dedup, emergency close-all, midnight reset, risk score (loss proximity + concentration + win rate), notifications at 80% daily-loss warning.

---

## 4. MONITORING + EXITS (the money-keeping path)

### 4.1 Charon refreshPosition — the best exit engine studied
- **Quote-first pricing**: dry_run exit decisions use executable Jupiter SELL quote (live pool reserves) as primary price; datapi mark is stale by design. Mark = fallback on 429/backoff. Quote price → quote mcap = entry_mcap × (quotePrice/entryPrice).
- **Dynamic ATR stop**: chart windows → ATR% (period 14) → effective SL = base × ATR multiplier, clamped [floor −50%, ceiling −8%], min ATR 4% max 30%. SL only fires when `pnl <= effSl && pnl < 0` (Lesson 3: never SL a positive position).
- **Trailing TP** (backtest 933 trades): arm at tp; tight trail 10→5% once peak ≥40%; floor +8% once armed (kills gap-dumps between ticks); NO partial at arm (−867% backtest).
- **MAX_HOLD time exit**: tiered by entry mcap (micro <15K: 10min; high >60K: 15-30min; audit mode disabled); **sideways timeout** (open N minutes with |pnl| < 2% → exit to free capital).
- **Live overrides with realized SOL**: on live exit, final PnL = receivedSol − size (real signature), recorded with exit_price/mcap/signature.
- **Dry-run exit slippage**: exit uses slippage-adjusted mcap (`slippageAdjustedMcap(...,'exit')`) — paper PnL includes exit slippage.
- **Re-entrancy guard**: sellInProgress Set per position; positionMonitorRunning global.
- Partial TP: sell fixed % of tokens on-chain (live) with remaining tracked; partial_tp_done flag.

### 4.2 Chainstack PlatformAwareSeller
- Sell amount passed FROM BUY RESULT (no RPC balance query — latency).
- **Re-read curve before sell**: mayhem/cashback/quote_mint/fee_recipient/creator_vault can change between buy and sell; program may delegate BC.creator to PFEE PDA (stale create-time vault → ConstraintSeeds revert). Graduated check: curve takes no sells post-graduation → route to AMM market state (fee_fraction from market).
- Expected quote output = tokens × (1 − transfer_fee) × price × (1 − fee_fraction); min output = expected × (1 − slippage) — sell slippage default 25%(!), max_retries 5.
- **Exit-sell verdicts (the key live lesson)**: failed sell → SOLD / LATE_SUCCESS (landed after all — stop, do NOT resell) / RETRY (provably reverted or never submitted) / STOP (unknown — a blind resell is the worse risk; re-check signature first). Reverted ≠ submit-failed. Bounded max_exit_sell_attempts=3. **Never idempotently resell an unknown outcome.**
- Signature on failure too (caller can re-check).

### 4.3 mortdeus riskManager
Profit target / stop loss / max hold time per position; price updates every 5s (placeholder entry-price in their sample — a KNOWN weak spot; don't copy the placeholder).

### 4.4 Exit anti-patterns to avoid
- Charon audit M3: **sells have no retry and no slippage guard** (buys get 3 attempts; sells single-shot) — transient exit failure leaves position open past TP/SL.
- Charon audit H4: 429-backoff serving STALE cached price to dip triggers — never let a backoff serve >freshness data to a trade decision.

---

## 5. CLEANUP + WALLET HYGIENE (chainstack)

- Cleanup modes: `on_fail` (buy tx failed → close ATA), `after_sell` (close ATA after selling), `post_session` (batch close all traded ATAs). AccountCleanupManager closes ATA + reclaims rent (matches CONFIG.md weekly cleanup rule).
- `force_burn` option; cleanup with priority fee option. Shutdown budget 120s.
- **Lesson for us**: after every closed paper position (or live someday), close the token ATA to reclaim rent — CONFIG.md already says weekly; chainstack does it per-event.

---

## 6. CONFIG + PERSISTENCE + UX PATTERNS

### 6.1 Config (chainstack config_loader)
Validated layered config: env-var interpolation in config files, schema validation with bounds (buy_slippage 0-1, priority extra 0-1, hard_cap ≥0), cross-field validation (can't enable dynamic+fixed). Trade amounts, slippage, priority fee, CU overrides, max_retries all configurable per platform.

### 6.2 Charon SQLite hot-reload
All strategy params live in SQLite `strategies`/`settings` tables; Telegram menus toggle them live (no redeploy): trading_mode dry_run/confirm/live, max_open_positions 1/3/5, trend source jup/gmgn, thresholds. Position + trade + decision + candidate + lesson tables. **Lesson**: our CONFIG.md is static; a live build should use DB-backed hot-reload config.

### 6.3 Telegram UX (Charon)
Inline keyboards everywhere: menu:strategy/agent/filters/wallets/positions/pnl; toggle:trending_enabled; set:trading_mode:*; set:max_open_positions:*; confirm mode = Telegram approve/reject buttons per intent. Entry/exit visual cards. Daily PnL report. **Our equivalent**: rich_card DM; the confirm-mode button UX is the right go-live pattern for a human-in-the-loop desk.

### 6.4 Notifications (mortdeus)
Multi-channel (Telegram/Discord/email), structured winston logging w/ rotation, dashboards (web UI, live charts). Graceful shutdown closes all positions and reports final stats.

---

## 7. AUDIT + BACKTEST PRACTICES (the meta-lessons)

1. **Split-half regime test** (Charon §2.6): the single most important number — a strategy that looks good "overall" may be riding one half. Always report both halves.
2. **Honest exit mix**: TRAILING_TP 262 (+55.8% avg) / MAX_HOLD 334 (+1.1%) / SL 495 (−49.1%) — the book's edge came from a THIN set of trailing winners; losers outnumbered them. Losers are the base rate; winners are the alpha. (Our Oct-18: 33% TP2 / 62% STOP — same shape.)
3. **Static code audit by another model** (Charon AUDIT_OPUS): 3 CRITICAL (C1 slippage cap never sent, C2 swap-before-dedup, C3 autoApply), 4 HIGH, 6 MEDIUM, 4 LOW. Top-3-fix-first list. **Lesson**: run an independent-model audit before any live deployment (we did ClaimEvidence P0-P13; add C1/C2/H1/H2 acceptance tests).
4. **Backtest truth rules**: use real snapshot_json features joined to pnl; nothing hand-estimated; consistency = time split; reject features with <150 trades or lopsided half-coverage; "seductive, not robust" list (bot_degen, gmgnTotalFees, trendingSmartDegen, route-as-sole-filter, mcap floors, trending volume floors all FAILED the split).
5. **LLM-layer bugs** (BUGFIX_SUMMARY): 91% wasted LLM calls (no cache), 488 post-LLM filter rejections (filter-after-LLM), execution-failure logging, past-win audit trail. Fixes: decision cache w/ invalidation, pre-LLM filter guard, failure logging, win-audit trail.

---

## 8. WHAT WE SHOULD DO (prioritized, desk-specific)

**Paper engine (now):**
1. **Fill-to-fill paper pricing** (A1) — wire jupiter_estimator.quote() as the paper FILL source (verified live: $20 → out_tokens, impact 3.7%, fee $1.08); feed price stays decision mark. Closes paper-vs-live gap.
2. **MAX_HOLD + sideways timeout** — third-party proof for the +180s time-exit (C −1.6% vs A −15.2%). Wire it (pending user decision).
3. **ATR-widened stop** — optional overlay on retuned 0.75 stop: widen only, floor −50%, never stop at profit.
4. **Trail structure** — if we adopt trailing later: tight 10→5% at peak ≥40%, floor +8% after arm, NO partial at arm.

**ADDITIONS from full re-check (Oct 21 — these were missed in v1):**
5. **DAILY-LOSS BREAKER — CODE-GAP VERIFIED.** CONFIG.md says "Daily stop: $20 loss → stop for 24h" but grep across paper_tick/desk_supervisor/position_monitor/desk_common finds NO enforcement — the book bled $100→$8.32 with that rule on paper. Charon+mortdeus both hard-block trades once daily net PnL ≤ cap, warn at 80% of cap, reset at midnight. Adopt: in paper_tick (or entry_call gate), if day's realized ≤ −$20 → all entry gates closed 24h (still allow exits), with a 💣 line to DM. This is the single biggest risk-control gap.
6. **FEE-CLAIM SIGNAL — missed enrichment.** Charon's fee_trending route = +2.17 SOL, and fee-claiming tokens (creator pays traders) outperform. Our PumpData WS stream ALREADY delivers `claimCreatorFees` events (verified in probe: actions included claimCreatorFees/sweep) — we ignore them. Adopt: feed counts claimCreatorFees per mint → AEGIS positive + rich_card "💸 fee-claiming token" line.
7. **ATH-PROXIMITY CLIP (Charon topBlastRisk).** They flag current/rangeHigh ≥ 0.85 = top-blast risk → tighten/clip. We have round_stall (85-95% of $25K/$50K...) but not ATH proximity. Adopt: track per-position high_water; px within 15% of range high with fading momentum → CLIP_50 signal (complements round_stall).
8. **QUOTE-MINT AUDIT — potential 1000x pricing trap.** chainstack cookbook warns: pump.fun coins are NOT all SOL-paired — `quote_mint` all-zeros = SOL, but USDC-paired coins exist and dividing by hardcoded 1e9 prints a price 1000x too low. Our replay/tape and pumpdata feed assume SOL quote everywhere. Adopt: audit tape events + cache rows for non-SOL quote mints; add a quote-decimals guard when parsing.
9. **PAST-WIN RE-ENTRY BLOCK — evaluate, do NOT blindly adopt.** Charon blocks re-entry for 7 days after a WIN (their backtest: round-trip losses). CONFLICTS with our buy-all-curated-calls mandate — a fresh channel call is exactly when we SHOULD re-enter. Verdict: do NOT block; document why (testable later on our tape if desired).
10. **RISK-LEVEL LINE IN DM CARD (mortdeus risk score).** Daily-loss proximity + position concentration + win rate → risk level (LOW/MEDIUM/HIGH) + recommendation. Cheap: compute from book state in paper_tick summary line. Also gives the desk a visible "stop for the day" state.

**Live build (when it happens — acceptance checklist):**
- C1 slippageBps on every Jupiter order + impact-cap fail; C2 dedup BEFORE swap; H1 mutex around check→swap→insert; H2 wallet-balance reconciliation on any ambiguous outcome; exit-sell verdicts SOLD/LATE_SUCCESS/RETRY/STOP; 70th-pct priority fee + hard cap; slippage on open side; balance-capped sells; Jito tip + 1s re-confirm; atomic daily-loss/position-cap; per-event ATA cleanup; DB-backed hot-reload config; Telegram confirm-mode; independent-model pre-live audit; graceful shutdown closes ALL positions (sniper SIGINT pattern).
- NOT adopting (conflicts / not applicable): LLM lessons auto-mutating config (we gate AEGIS deltas), plain-trending as entry booster (inverted in their data), copy-trade execution on-chain (we copy via curated human calls), trade cooldown 5s (call-driven, not need), token-age cap (calls are fresh by construction).

**Research:**
5. Adopt the strict both-halves gate for all future backtests (Oct-10/Oct-18 + shrine cross-validation).
6. Re-validate our entry hygiene vs their FLOW filter (1h chg ≥0 + net buyer 5m ≥0.2) on our tape — we withdrew push60/bs5m; the Jupiter-stats version may survive where the pre-fill version didn't.

---

## 9. ONE-LINE VERDICT PER REPO

- **Kaiser.charon**: the most mature — fill-honest paper, dynamic ATR exits, regime-split backtests, and an audit that names the exact bugs our live build must avoid. Steal A1/A3/A4/B-checklist.
- **chainstack pumpfun-bonkfun**: the best execution mechanics — priority fees, platform-aware slippage, exit-sell verdicts, reconciliation caps, curve re-reads. The live-trading playbook.
- **mortdeus sniper**: execution plumbing + risk manager; confirm-time/reconfirm and Jito tips; otherwise thin.
- **pump-fun-sdk**: reference SDK + lifecycle skills; only useful as a spec for program IDs/curve math (matches our replay model); its swarm/dashboard are ops tooling we don't need.

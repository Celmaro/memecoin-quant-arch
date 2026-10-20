# LEARN: 4 Trading-Bot Repos → Desk Lessons (Oct 21 2026)

Studied (cloned + read, not just READMEs):
1. **Kaiser.charon** (fork of Charon) — Telegram pump.fun screener: dry_run/confirm/live modes, Jupiter execution, LLM filters, backtests, static audit. MOST RELEVANT to our desk.
2. **chainstacklabs/pumpfun-bonkfun-bot** — production Python bot: priority-fee manager, platform-aware buy/sell, exit-sell verdicts, cleanup. Best live-execution mechanics.
3. **mortdeus/solana-copy-sniper-mev-trading-bot** — Node/Rust sniper+copy+MEV: Jito tips, VersionedTransaction, risk manager. Execution plumbing.
4. **nirholas/pump-fun-sdk** — official-style TS SDK: offline-first instruction builders, vanity keygen, bond-curve math. Reference only (we don't send txs).

---

## A. PAPER-ENGINE UPGRADES (direct, actionable now)

### A1. Fill-to-fill paper pricing — THE upgrade (Charon)
Charon's dry-run: paper entries use an **executable Jupiter buy quote**, exits use
executable Jupiter **sell quotes** — NOT synthetic mark prices. Recorded PnL
includes the simulated entry/exit fill difference + execution fees, so dry-run
tracks live execution. We already have `jupiter_estimator.quote()` (keyed) —
wire it into `paper_tick` as the fill-price source (feed price stays the
decision/mark source). This closes the paper-vs-live gap the Oct-18 audits
complained about. (Charon went further: `fetchTokenSpotViaQuote` uses a fixed
1k-token reference quote for spot; upgrade to position-sized quotes when
size > 1 SOL.)

### A2. Dynamic ATR stop-loss (Charon, backtested)
Static SL widened/narrowed by ATR%: base sl ± atr multiplier, clamped to
[floor −50%, ceiling −8%], min ATR 4%, max 30%. Prevents -25% SL on a
high-vol coin that routinely swings -20%. We have fragility/realized-vol in
`signals.py` — candidate for an ATR-aware stop on top of the tape-retuned
0.75 stop (do NOT replace; widen only).

### A3. Trailing-TP guard specs (Charon, backtested 933 trades)
- **Never trail below a profit floor once armed** (their floor: +8% after arm
  at TP). Kills gap-dumps between ticks.
- **Tight trail once peak ≥40%**: 10% → 5%. Rescues armed winners that round-trip
  to SL (their window: 97 armed+SL trades = −6,497% pnl).
- **NO partial sell at arm** — backtest REJECTED it (−867%): capping runners
  kills the tail that carries profit.
- **SL only when PnL negative** (`slHit = pnl <= effSl && pnl < 0`) — never stop
  a position at a profit by trailing below entry.
- Their TP/SL ladder (tp 75%, sl −35%, trail 15%, hold 30min) is looser than our
  retuned TP2.0/SL0.75 — do NOT copy their numbers; copy the STRUCTURE.

### A4. MAX_HOLD time exits are THE life-preserver (Charon backtest, 1146 trades)
Exit mix: TRAILING_TP 262 (+55.8% avg) / MAX_HOLD 334 (+1.1%) / SL 495 (−49.1%).
The 30-min time exit is what keeps the book from bleeding to zero while waiting
for killers to die. **This is third-party confirmation of our +180s time-exit
recommendation** (STRATEGY_COMPARE: C −1.6% vs A −15.2%). Also: tiered max-hold
by entry MC (micro <15K: 10min; high >60K: 15–30min) + sideways timeout (open
N minutes with |pnl| < 2% → exit to free cap).

### A5. Regime-decay backtest methodology (Charon, gold standard)
1146 closed positions split at median time: HALF-1 40.3% WR +5.11 SOL vs
HALF-2 25.7% WR −3.93 SOL — **no filter change, the regime decayed**. Their
accept rule: a feature PASSES only if it beats its half's baseline on BOTH
pnl_sol AND win rate in FULL, HALF-1, HALF-2 with ≥30 trades per half. This is
exactly how we should re-validate our Oct-10/Oct-18 windows (we already
cross-validated both halves for the ladder retune — keep doing it).
Their result: only **liquidity floor (≥$13K) + holders (≥168)** survive; mcap
floors, trending-volume floors, bot/smart-degen counts all FAIL the split
(regime riders). Independently confirms our $10K liq gate (+20.6 pts finding).

### A6. Anti-patterns to never copy
- `autoApply` LLM lessons → auto-mutates strategy config (C3): our aegis_learn
  already gates deltas to ±6/±12 and is daily-reviewed.
- Decision cache w/o cap = unbounded Map → OOM (H3): keep our
  `_pumpdata_memo` capped.
- 429-backoff serving STALE data to dip triggers (H4): our feed TTL (120s) +
  dex fallback handles this; never let a backoff serve >freshness data to a
  decision.

---

## B. LIVE-EXECUTION CHECKLIST (when we ever go live — audit-driven)

From Charon's own Opus audit + chainstack's code. These are the exact bugs
their audits found; our live build must pass all of them:

1. **C1: slippage cap MUST be sent to Jupiter on every order.** Charon shipped
   live with `JUPITER_SLIPPAGE_BPS=300` defined but NEVER attached to the order
   URL — Jupiter used its own default on illiquid memecoins. (We have a keyed
   Jupiter quote path; a live builder must set slippageBps explicitly + fail if
   quote impact > cap.)
2. **C2: dedup guard BEFORE the swap, never after.** Charon swapped first, then
   checked existing/recentClosed/pastWin → dedup hit = tokens bought but
   untracked, never sold. **Our desk is already correct**: `entry_call` runs
   already-held/consumed/24h checks BEFORE buying. Keep it that way; if a
   post-swap dedup ever fires, record orphaned tokens + alert (never silently
   drop).
3. **H1: serialize live entries with a mutex.** Check→swap→insert has a TOCTOU
   race across concurrent signals → max positions exceeded with real money.
4. **H2: reconcile on ANY ambiguous swap outcome.** Timeout/malformed execute
   response ≠ failed. Query wallet token balance before recording FAILED vs
   FILLED (chainstack: `get_buy_transaction_details`, fall back to balance).
5. **Exit-sell verdicts (chainstack, best in class)**: classify a failed exit
   sell as SOLD / LATE_SUCCESS (landed after all — do NOT resell) / RETRY
   (provably reverted — safe to retry) / STOP (unknown — a blind resell is the
   worse risk; re-read price first). NEVER idempotently resell an unknown.
6. **Priority fee**: 70th-percentile of getRecentPrioritizationFees on the
   swap's accounts, + extra %, hard-cap enforced. Chainstack: dynamic default,
   fixed fallback, cap via config. Matches CONFIG.md's max ~0.001 SOL.
7. **Slippage on the OPEN side only**: pump.fun buy_v2 exact-in → token floor;
   exact-out programs → max amount_in ceiling. Chainstack handles both via
   `spends_exact_amount_in`.
8. **Never sell an unrelated position**: balance-reconciliation fallback MUST cap
   the sell at what THIS buy asked for (`min(balance, token_amount)`), else a
   read-back failure liquidates a pre-existing hold.
9. **Risk manager (sniper repo)**: daily net-PnL cap, max positions, per-trade
   cooldown, emergency-close-all, midnight reset, risk score (loss proximity +
   concentration + win rate). We have daily $20 stop + max 5 positions in
   CONFIG — a live build needs the enforcement atomic with the buy.
10. **Jito tip + reconfirm**: VersionedTransaction, `prioritizationFeeLamports
    { jitoTipLamports }`, maxRetries 1–2, confirm-time measured, and
    **re-confirm after 1s** if the first confirm times out (sniper repo).

---

## C. PIPELINE / SIGNAL LESSONS

- **Route quality varies hugely** (Charon 1146-trade split): pumpportal_graduated
  41.2% WR / trenches 40.5% / plain trending 22.9% — and `hasTrending` flag
  CORRELATES WITH LOSING (27.7% vs 40.2%). For us: channel quality dominates
  (our own Oct-19 finding: solearly 58% vs soltrench 40%). Keep channel-level
  stats in journal; never bless a "trending" overlay as an entry booster.
- **Buy-all on curated calls is NOT contradicted**: Charon's filters gate THEIR
  auto-picked candidates; our calls come from curated humans — the filter
  evidence (liq/holders) supports our entry gates, not signal-based entry.
- **Fresh-graduate fast path** (Charon): right after graduation, skip enrichment
  that's guaranteed empty/stale and use the fast data source. Our
  `_is_curve_pre_grad` / graduated flags already special-case bonding curve;
  extend to skip slow enrichment on fresh grads.
- **LLM decision cache** (Charon, −64% calls): mint→verdict with 10/60min TTL,
  invalidate on >20% mcap or >30% holder change. We don't use an LLM filter —
  not applicable, but the pattern protects any expensive per-mint recompute.

## D. REFERENCE NOTES (pump-fun-sdk, chainstack)
- pump-fun-sdk = offline-first TS instruction builders (create/buy/sell/bond
  curve, fee sharing, incentives); also a huge multi-tool repo (swarm,
  dashboard, vanity). Only useful as a spec reference for pump.fun program
  IDs/curve math if we ever build live; its bond-curve constants match our
  replay's B·x/(Q+x) model.
- chainstack's `platforms/pumpfun/curve_math.py` + `event_parser.py` decode the
  same tape our replay consumes (buy/sell/create/migrate events with reserve
  fields) — good cross-check for graduate_replay parsing.
- All repos confirm: three modes (dry_run / confirm / live) is the standard
  go-live path; dry_run must be fill-honest (A1) to be meaningful.
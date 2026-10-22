# BLIX Trading/Paper Engine — Deep REA Analysis

Citations are `file:line` in `/home/agentuser/memecoin-quant/blixtrade` (repo sha256 011f033d…, REA evidence ev_f34217f8… JS application graph; ev_42bccde1/ev_fb269ab2 analysis views). Cross-checked against source by direct read of all 12 files.

## 1. Paper fill mechanics (real Jupiter quotes + slippage model)

**Two generations of fills.**
- **Flat (legacy) model** — `server/brain.js:25-26` `PAPER.costPctPerSide: 3`; `server/paper.js:23` `COST = (1−0.03)/(1+0.03) ≈ 0.94175`. Old positions (no `tokens` column) fill/exit at `(mcapNow/mcap0) * COST` — `server/paper.js:332,370-371`. Comment says "3% cost each way" (`paper.js:10`), the effective round-trip multiplier is ×0.94175.
- **Quoted model (current)** — positions hold real token amounts (`tokens`, `fill` columns, `paper.js:34`) and fill at a REAL quote via `quote()` (`paper.js:191-212` openQuoted).

**Quote routing** (`server/quote.js:116-148`):
1. **Curve coins** (pump.fun, `progress < 0.995`, reserves fresh ≤ `CURVE_FRESH_MS` = 5 min, `quote.js:19,126`): exact constant-product curve math from live virtual reserves in the trade feed. Buy: `tokens = vTok − k/(vSol + net)` where `net = amount/(1+fee)` and fee `feeBps ?? 125` (default 125 bps = 1.25%, comment: 95 bps protocol + 30 bps creator, validated to ~1e-7 vs chain 2026-10-02) — `quote.js:21-37,133`. Impact = `avg/spot − 1`; `minOut = out·(1−slip/1e4)`; post-fill mcap computed (`mcapAfterSol`, `quote.js:30,36`).
2. **Bonded coins**: Jupiter `/swap/v1/quote` (`quote.js:70`), same route a real swap takes; decimals from Jupiter price v3 or 6 for `pump` mints (`quote.js:56-66`); `routePlan` labels kept (`quote.js:73`).
3. **PumpSwap pool estimate** (`quote.js:84-95`): when `estimate=true` and the radar hears the pool trade-by-trade (`poolStateOf`, ≤90 s old, `quote.js:131-134`), a 2-sided CPMM with 1% fee is used (`src "pool"`/`"estimate"`) instead of burning Jupiter quota. **Real trades never take the estimate path** — the wallet service (blix-vault) quotes for itself (`quote.js:83,130`); paper trades always pass `estimate:true` (`paper.js:198,263`).
4. **Fast-quote timeout**: estimate-allowed quotes wait `waitMs` (paper fills: **250 ms**, `paper.js:198`; previews 800 ms, `quote.js:99-107`) then 503 "Jupiter is slow".

**Slippage model**: `slippageBps` clamped 1–5000, default 300 (`quote.js:122`); it only shapes `minOut`/`otherAmountThreshold` (Jupiter) and `minOut` (curve/estimate). No synthetic slippage is added to the fill — the *actual* price impact of the trade size is the model, plus for exits the position's **own buy's impact is added back** because a paper buy never moved real reserves: exit = Jupiter out × (1 + ownImpactPct/100) (`paper.js:232,264`), and for curve sells the reserves are virtually shifted as if the own buy had happened (`curveQuoteNow(..., own)`, `quote.js:45-50`, `ownBuy` at `paper.js:221-224` assumes default 1.25% fee when the fill record lacks `feeSol`).

**Exit-quote economics**: open quoted positions are valued every second — curve coins exactly (`curveQuoteNow`, 300 bps slip), bonded coins from a cached Jupiter exit quote refreshed ≤ every 45 s with 1 s between positions (`paper.js:237-255`; rate-limit lesson 2026-10-07), scaled by `px.mcap/e.mcap` and valid 60 s (`paper.js:232`); fallback estimate `sol·(px/mcap0)·(1−0.0125)` (`paper.js:233`).

**Fill guards**: buy rejected if `impactPct > 50` (`paper.js:200`); buy size 0 < sol ≤ 1000 (`paper.js:194`); member desks min 0.01 SOL / max 25 open (`paper.js:109-114`); no cash → 409 (`paper.js:117`); partial sells spawn a closed row and scale the remainder (`closePart`, `paper.js:297-318`); optimistic-concurrency check on sell (`paper.js:298-301`). Wallet: start `paperStartSol` 10 SOL (`settings.js:115`), member caps 1,000 SOL start / 10,000 total (`paper.js:133,146`), epoch+max-id reset semantics (`paper.js:119-137`).

**Pricing waterfall** for the desk (`priceNow`, `paper.js:54-64`): live trade feed (fresh ≤10 s msg, stat ≤5 min, progress <0.995) → Jupiter fastMcap → DexScreener cache (20 s) → tokens table (≤3 min, not quarantined). Non-quoted values priced via this.

## 2. TP ladder / trailing / hard / time stop — two rule sets with exact defaults

### (a) Manual paper desk positions (`paper.js` tick, per position columns tp/sl/trail)
Defaults **0 = off** for each (`settings.js:130-132`: `paperTake: 0, paperStop: 0, paperTrail: 0`); per-position overrides via `open()/setRule()` (`paper.js:180-184,348-353`), positive numbers only (`rule()`, null if ≤0). Checked every 1 s in `tick()` (`paper.js:382-411`, `startPaper` interval 1000 ms `paper.js:437`) against the **realizable exit value**, not headline price (`paper.js:393-394`):
- TP: `mult ≥ p.tp` → sell 100% `take profit at {tp}x` (`paper.js:396`). Single-level, all-out — no ladder.
- Hard stop: `mult ≤ 1 − p.sl/100` → `stop at -{sl}%` (`paper.js:397`).
- Trailing: only after `peak > mcap0` (above entry); fires when `m ≤ peak·(1 − trail/100)` → `{trail}% below its high ({peak/mcap0}x)` (`paper.js:398`). Peak/low tracked per tick (`paper.js:390-391,405-407`, single transaction — 5 s lock incident fix).
- No time stop on manual positions. Fills via `sell()` = the desk's own exit value (curve exact / Jupiter cache / estimate) at the seen price (`paper.js:321-346`).

### (b) Radar picks' exit rule (simulated, every pick) — `server/brain.js`
Defaults (`settings.js:82-86`): `pickTakeProfit: 2` (2x), `pickSellPct: 50`, `pickTrailPct: 35`, `pickStopPct: 40`, `pickMaxHours: 6`.
`stepPick` (`brain.js:420-429`), all sales pay `COST` (3%/side):
1. **TP ladder (one rung)**: first time `peak ≥ 2x` → sell 50% at exactly 2.0x (`brain.js:424`); replayPicks backfills from stored snapshots so TP fills at exactly its level (`brain.js:539`).
2. **Trailing stop**: after TP taken, sell remainder when `m ≤ peak·(1−0.35)` (`brain.js:425`).
3. **Hard stop**: before any TP, sell all when `m ≤ 1 − 0.40` (`brain.js:426`).
4. **Time stop**: sell remainder after 6 h (`brain.js:427`).
5. Pool emptied → sell(1, 0) — full loss recorded (`brain.js:423`); `illiq` marks unsellable (`brain.js:372-373`).
Checked every second (`fastTick`, `brain.js:387-411`) **and on every trade** for curve coins (sub-0.4% dead-band) because curve coins fall 50% in seconds (`onPickTrade`, `brain.js:379-385`). Stops fill at the price seen on the check after crossing (documented slippage, `brain.js:418-419`). Sim state stored per pick (`sim` JSON, `brain.js:432-448`) with two-tracker idempotence guard.

## 3. Per-day risk guard (`brain.js:327-347`, defaults `settings.js:94-96`)

`riskGuard()` (5 s cache) blocks NEW picks only; it does not touch the manual paper desk or trades.
- **Loss streak**: last `guardLosses` = **5** closed pick outcomes all `exit_mult < 1` → no new picks for `guardPauseMin` = **45 min** from the last losing exit (`brain.js:334-339`).
- **Day loss**: sum of today's pick P&L at $100/pick (`(exit_mult ?? realized+left·x·COST) − 1) × 100`, illiquid picks valued 0) ≤ `−guardDayLoss` = **−$600** → no new picks until local midnight (`dayStart`/end-of-day `until`, `brain.js:340-344`).
Enforced at entry (`enterPick`, `brain.js:350-351`); surfaced in Today payload (`brain.js:566`). Separate day-budget guard exists for the Grok desk: `deskBudget` $5/day paced `(elapsed/24h)+0.1` (`desk.js:28,229-238`), and snipe Helius credits paced per day 12,000 default (`snipe.js:98-118`).

## 4. Replay backtest methodology (`server/replay.js`)

Two things called "replay":
- **Coin-page day replay** (`replayData`, `replay.js:18-55`): serves every chain trade the radar journaled for the coin in `journal.KEEP_MS` (one day), oldest first, capped at `MAX_TRADES = 80,000` (oldest dropped, `replay.js:9,21`); tuples `[ms, mcUsd, usd, buy, walletIdx]` plus dedup'd wallet table, 1-min-granularity SOL prices (`replay.js:48-49`), bondedAt/curve flags from journal venue rows (`replay.js:13-14,53`). Trades journaled before wallets were kept are re-attributed by matching pump.fun trades (same side, ±3 s, nearest size, ≤12% tolerance, each used once, `replay.js:27-46`); `walletsDone`/`walletsBack` let the page poll until background attribution finishes. The web client (web/src/lib/replay.ts) rebuilds chart/holders/mcap/paper fills from this — the backtest is data replay, not re-simulation server-side.
- **Practice peaks** (`replayPeaks`, `replay.js:65-96`): per-coin ATH within the journal window, anti-spiked: a high only counts if a second trade within ±60 s reached ≥75% of it (`replay.js:77-84`), reconciled with `tokens.peak_mcap` (>ath×1.02 wins) and live mcap; `partial` flags a coin the journal stopped hearing ≥15 min before its last token price (e.g., pool quoted in another token), 20 s per-mint cache, ≤60 mints.
- **Outcome replay/backtest of picks** is in `brain.js`: `replayPicks` (`brain.js:451-474`) re-runs `stepPick` over `snapshots` (t0..t0+7h, `mcap>0`, ok only) for picks made before the rule existed; `evidenceNow` (`brain.js:488-505`) is the rule backtest: 7 days of graded outcomes excluding the last 45 min, win = >1.15x, banded by traction/pwin/action, re-measured every 5 min, 60 s response cache.

## 5. Fomo integration — what is sent where (verified: nothing signs txs)

Three independent Fomo surfaces, all **read-only**:
1. **On-chain flow** (`server/fomo.js`): Fomo trades are identified on-chain via Fomo's fee-payer account (Fomo pays users' network fees), learned by seeing one payer on ≥2 swaps of user-added Fomo wallets (`fomo.js:44-52`); `readFlow` polls the payer's signatures (25 s interval, 8 tx/round on free RPC, 40 with `settings.rpcUrl`, even sampling `fomo.js:55-84`), parses swaps from pre/post token balances, writes `fomo_flow` (sig, wallet, mint, side, tokens, sol, usd). Downstream: `hotCoins`, `coinFlow`, `topTraders` (7-day realized-PnL ranking, `fomo.js:140-167`), `autoFollow` (adds ≥$300 realized, ≥3 closed, ≥50% win traders, cap `fomoFollowTop: 15`, `fomo.js:169-180`), and `crowdSignals` alerts: ≥3 distinct Fomo buyers in 30 min with net inflow, safety-vetted, once per 3 h per mint (`fomo.js:184-198`). Retention 7 days (`fomo.js:205`).
2. **Fomo Feed** (`server/fomofeed.js`): mirror of fomo.family's Feed tab. Auth is the **user's own Privy token** used inside a real fomo.family browser tab (Aside on the owner's PC, `fomofeed.js:127-185`), a server-side `fomoFetch` with the relayed token (`fomofeed.js:196`), or an owner-PC relay POST `/api/fomo/feed/relay` (`fomofeed.js:255-269`). Feeds polled ~0.9-2 s while a page is open (`WANTED_MS 45 s`, `fomofeed.js:31`), slimmed (`slim`, `fomofeed.js:83-121`), persisted to `data/fomofeed.json`, pushed to pages via `feedstream`. Strictly display data.
3. **Desk consumers** (`server/desk.js`): `tradersOf` pulls `fomo_coin` holder/swap JSON (refreshed ≤5 min via `wantFomoFresh`) and `fomo_flow` hourly flow to annotate narrative entries with known traders (`desk.js:289-344`). Display only.

**Signing verification**: no Fomo path builds, signs, or sends a Solana transaction. All trade *sending* in the repo is confined to the member trade API (`server/tradeapi.js`): `/api/v1/quote|trade/build|trade/send` proxy to the local wallet service blix-vault over 127.0.0.1 (config `BLIX_VAULT_CONFIG`, `tradeapi.js:255-269`); "The signer is always the caller: Blix never sees a private key" (`tradeapi.js:8`). The paper desk's own header comment: "Nothing is ever sent to the chain" (`paper.js:3`). Fomo's own trade txs are *read* (parsed from fee-payer txs), never originated; `txtrade.js` and `snipe.js` likewise only fetch/parse chain state (rate-limited RPC reads, `txtrade.js:16-36`; `snipe.js` launch reads). Signal `raise()` calls produce alerts with the phrase "Paper only: nothing is bought" (`brain.js:365`).

## Supporting modules
- **jupiter.js**: single paced transport (default 24 RPM, cap 27 keyless / 54 keyed, `jupiter.js:14`), priority lane for quotes with background capped at 40% of RPM, 12 s queue timeout, adaptive halving on 429 with 10-min quiet recovery, provider-header accounting (`x-ratelimit-*`), 200-entry path cache, routes to `lite-api.jup.ag` when keyless+priority (`jupiter.js:22-98`).
- **jup.js**: datapi.jup.ag (free): chart candles (9 timeframes, first-candle launch-spike correction, stale-serving windows), trades/holders/asset lookups, `supplyOf` — the one mcap scale for the whole app (background refresh, chain-supply fallback for majors, `jup.js:186-210`); 4-concurrency, low-priority queue (`jup.js:15-28`).
- **pumpquote.js**: decodes pump.fun TradeEvents for curves quoted in non-SOL tokens (USDC/USDT/USD1 at $1, else Jupiter price, else the radar's own live price), reading quote mint/amount/reserves from the event tail; converts to SOL-equivalents so all downstream readers are unchanged; sanity caps mc ≤ $50B, usd ≤ $50M; `{pending:true}` defers unpriced quotes (`pumpquote.js:87-105`).
- **txtrade.js**: per-coin swap parsing from pre/post token balances for Raydium/Orca/Meteora (signer = trader, pool = owner whose coin/quote moved oppositely, largest-USD quote wins), 1 req/s token bucket on public RPC, `fetchSwap` retries 6× (350ms + i·250ms) at confirmed commitment (`txtrade.js:56-100`).
- **snipe.js**: bundlers = non-dev buyers in the launch slot; snipers = first-buy in the next `SNIPE_SLOTS = 2` slots; free lane via public RPC (pump curve-account history for `pump` mints, ≤6 pages of 1000), Helius `getTransactionsForAddress` asc otherwise, credits paced `12,000/day` spread by UTC hour +3 h head start (`snipe.js:24,96-118,144-246`); holdings via batched `getMultipleAccounts` (ATAs + supply), cached on disk.
- **tradeapi.js**: member keys `blx_live_*` (SHA-256 hashed, 5/member), limits 120 quotes+builds/min, 60 sends/min, 3 streams/key, 200 subs/stream (`tradeapi.js:27`); fee 200 bps (160 bps invited, inviter earns 40%, `tradeapi.js:146`); PumpPortal-shaped WebSocket fanned out from one upstream firehose connection (`tradeapi.js:149-232`). Usage/trade ledgers in `data/tradeapi.db`.

## Notable caveats / residual risks
- Paper curve quotes depend on feed freshness (`resT ≤ 5 min`, progress <0.995); a stale curve silently falls to Jupiter/estimate pricing (`quote.js:47,126` vs `paper.js:230-233`), so exits on thin bonded coins can value off a ≤60 s-old Jupiter quote scaled by mcap ratio plus a heuristic impact add-back — a modeled, not guaranteed, fill.
- `exitSol` estimate path assumes a fixed 1.25% exit fee (`paper.js:233`) which matches the default `feeBps` but not a coin whose curve charged a different fee.
- Manual desk TP/SL/trail default to off; only radar picks have an always-on rule — practising on the desk with no rules set means positions are marked-to-market only.
- Risk guard uses $100-per-pick notional and `exit_mult` outcomes, so it gates the simulated pick engine, not the manual paper wallet or member API trading.

# 📈 UPGRADE_PLAN — Wiring & Improvements (verified 2026-10-15, IMPLEMENTED ✅)

Strategy anchor: **buy ALL curated-list calls → quick flips (minutes–2h) with conviction holds; exit = behaviour + data + forensics.**
**HARD RULE: ENTRY SIGNALS COME ONLY FROM THE CURATED TELEGRAM LIST (CONFIG.md §Channels). NO OTHER SOURCE GENERATES BUY SIGNALS.** All other tools are layers: they scan/verify/monitor calls that already came from the list, or provide exit/risk context. They never create entries. All endpoints below verified in ToolStacks.md.

Status legend: ✅ DONE · 🔥 do now · ⏳ this week · 🧊 later/needs key

---

## TIER 0 — Fix leaks in the CURRENT stack (small, high-return)

### 0.1 FomoAPI needs `?network=1399811149` for Solana token holders 🔥
- **Problem found live:** `/token/{addr}/holders` returns `available:false, reason:"unknown-chain"` without the network param. Thesis upstream is flaky (503 retryable).
- **Wire:** in `aegis.py`/crawler smart-money lookup, call `https://api.fomoapi.io/token/{addr}/holders?network=1399811149` (Solana chainId; 4663 Robinhood, 8453 Base, 56 BSC). Add retry/backoff for 503s (already in `desk_common.http_json`).
- **Gain:** smart-money holder presence actually resolves → auto post-buy strategy per call.

### 0.2 Bitquery V2 Solana schema ≠ EVM schema 🔥
- **Problem found live:** `Solana { DEXTrades { Buy/Sell/Block.Timestamp } }` rejected; only `Block.Slot` verified.
- **Wire:** use documented starter shapes from https://docs.bitquery.io/docs/blockchain/Solana/solana-dextrades/ (Trading cube for USD price/MC/volume, `Trade { Dex { ProtocolFamily } }` etc.). Add a `bitquery_realtime(mint)` helper in `signals.py`.
- **Gain:** realtime DEX buy/sell imbalance + tradable USD volume per call (Tier 2).

### 0.3 Stale-call flag: weight it with call-vs-live MC delta 🔥
- Already flags when live MC < 50% of call MC. Extend: record `mc_delta_pct` into the playbook card so journal can measure how "late" each channel is on average.
- **Gain:** per-channel latency stats → tells user which channels are worth front-running vs noise.

---

## TIER 1 — Instant-signal & smart-money wiring (fits quick-flip latency needs)

### 1.1 Stream layer: PumpDev WS + PumpAPI WS — MONITORING ONLY (NOT signal source) 🔥
- **What:** `wss://pumpdev.io/ws` (`subscribeTokenTrade`) and `wss://stream.pumpapi.io/` give sub-second tape on specific mints — keyless, free.
- **Role (per the HARD RULE):** NEVER generates buys. Two uses only:
  1. **Post-buy tape monitor** — the moment a curated-list call is received, subscribe to that mint's trades for fast exit reads (whale sells, volume stalls) inside `position_monitor`.
  2. **Call context** — when the crafted list calls a token, the stream can confirm it's actively trading / not dead. Nothing more.
- **Not used for:** discovering tokens to buy, new-launch alerts as entries, sniping. That discipline stays with the 6 curated channels.

### 1.2 Smart-money overlay at intake (FomoAPI) 🔥
- **What:** for every **curated-list call**: `holders?network=1399811149` → identified FOMO traders; then `users/{handle}` for each → track record (PnL windows, trades, wallets, verified).
- **Wire:** `behavior_index()` in `aegis.py` — auto-tag actors (smart_wallet / kol / rug_deployer by history) → `strategy_for(mint)` already exists, now fed automatically. Store in `actors` + `coins.strategy`.
- **Gain:** post-buy strategy becomes automatic per call: conviction_hold when smart wallets in, flip_fast when deployer smells, front_run_exit when KOL-called. This is the "behavior indexing → post-buy strategy" idea realized.

### 1.3 Richer exit signals in position monitor 🔥
- **Birdeye WS large trades** (`SUBSCRIBE_LARGE_TRADE_TXS`, free tier, `wss://public-api.birdeye.so/socket`): whale dump detection → new `WHALE_SELL` signal in `analyze()` → tighten action.
- **CoinStats wallet balance** (`GET /v1/wallet/balance?address=&connectionId=solana`, key works): poll the position's leading wallets (top holders from Rugcheck) → "good wallets selling" autopilot exit trigger (Yoda's rule).
- **Bitquery realtime** buy/sell imbalance → replaces/validates DexScreener 5m txns proxy (more granular, USD-weighted).
- **Gain:** exits stop waiting for the 5m candle; whale/wallet behaviour feeds action instantly.

---

## TIER 2 — Data & metrics ingestion (feeds the learning loop)

### 2.1 Per-call "playbook card" persisted to SQLite ⏳
Extend the `coins` row + a new `call_metrics` table: fill_class (slow-grad), AEGIS score+flags, Ave risk_score, top-10 pct, smart-money holder count, buy/sell imbalance at ingest (USD-weighted), mc_delta_pct, vol-quality flags (LPI/spike), fragility score.
**Gain:** PRFS + journal can *attribute outcomes to features* — that's how we learn which filters actually predict flips.

### 2.2 OHLCV ingestion for risk math ⏳
- **DexPaprika** `GET /networks/solana/pools/{pool}/ohlcv` (key verified) + **Birdeye** `defi/ohlcv` → per-position realized vol (1h/1d) + max drawdown → feed `fragility_score` in `signals.py` with real vol persistence (ME2F), not just chg proxies.
- **Gain:** stop-loss / clip sizing gets a volatility input; hour-aware stats (2606.08232) for the journal.

### 2.3 PumpAPI replay backfill 🧊 (free, for studies)
`replay.pumpapi.io/YEAR/MONTH/DAY/HOUR.jsonl.zst` → historical event reconstruction for wash-trade/LPI backtests and to give rejected tokens their true outcomes (PRFS precision at scale).

### 2.4 Ave contract risk already wired — add rank-board ingestion ⏳
`GET /v2/tokens/trending?chain=solana` + `GET /v2/ranks?topic=meme` → cross-check the 6 channels' calls against Ave's own trending to catch tokens the channels are late on.

---

## TIER 3 — Learning loop & execution readiness

### 3.1 Attribution report (journal.py) ⏳
Join POSITION_CLOSE outcomes back to the playbook card features: win-rate by fill_class, by AEGIS verdict, by smart-money presence, by mc_delta bucket, by strategy assigned.
**Gain:** the desk tells YOU which signals earned the money — the actual learning output.

### 3.2 Channel latency + quality dashboard 🧊
Per-channel: avg call-vs-live MC delta, avg fill_class of calls, post-call survival — ranks the 6 channels. Stale channels get deprioritized in alerts.

### 3.3 Execution readiness (post learning phase) 🧊
PumpDev Lightning for entry speed (0.25%), Jupiter quotes pre-trade (done in `jupiter_estimator.py`), auto bracket on entry: quote → buy → 50% take-profit at c1 → stop at stop% → monitor. NO execution until user flips the switch.

---

## What NOT to build now (budget discipline)
- **Nansen** (0 credits) and **Dune sql/execute** (402 datapoint cap): documented + ready; skip until subscription justifies.
- **Birdeye token_security** (needs paid package): Rugcheck + Ave already cover risk with free verified endpoints.
- **GMGN/DexScreener UI scrapers**: browser automation broken in this env; API equivalents cover the needs.

---

## Suggested build order
1. **0.1 + 0.2 + 0.3** (small fixes — do today)
2. **1.1 stream monitor (post-buy tape ONLY, after curated call) + 1.2 smart-money overlay** (the big win — wires the whole "behavior index → strategy" loop)
3. **1.3 whale/wallet exit triggers** (Birdeye WS + CoinStats balance)
4. **2.1 playbook card + 3.1 attribution report** (learning loop becomes self-measuring)
5. 2.2 – 3.3 as capacity allows
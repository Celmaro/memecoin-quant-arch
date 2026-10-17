# 🧰 ToolStacks — Memecoin Quant Desk Tool Inventory (ALL endpoints)

**Living reference.** Every tool/API/WS/RPC the user provides gets appended here with endpoints + summary.
Before building anything: scan this file for the right tool. Credentials live in `CONFIG.md` (same dir).
Last verified: **2026-10-15** (live probes). Docs consulted: official docs/llms.txt per tool.

Status legend:
- ✅ **WORKS** — live-tested working
- ⚠️ **LIMITED** — works but plan/key/credits restrict (setup is correct)
- ❌ **BROKEN** — key/endpoint genuinely failing
- 🔬 **UNTESTED** — endpoint known from docs, not probed

---

## 1. Solana RPC & Archival

### Helius — ✅ WORKS
Docs: https://www.helius.dev/api/llms.txt · RPC: `https://mainnet.helius-rpc.com/?api-key=<KEY>` (devnet: devnet.helius-rpc.com). Auth: `api-key` query param. Free tier ~1M credits/mo, 10 RPS.
- **JSON-RPC (standard methods):** getAccountInfo (jsonParsed), getSignaturesForAddress, getTransaction, getBlock, getHealth, sendTransaction, getTokenAccountsByOwner, getProgramAccounts
- **Enhanced Transaction API:** `GET /v0/addresses/{wallet}/transactions?api-key=<KEY>&limit=N&before=<sig>&sortOrder=asc|desc` — parsed txs; `sortOrder=asc` = genesis/first-tx (mother-wallet attribution, kills N+1)
- **Raw Enhanced API:** `GET /v0/transactions?api-key=<KEY>` (by accounts), `GET /v0/transactions/{txSig}?api-key=<KEY>` (parse one)
- **Enhanced Balances:** `GET /v0/balances/{wallet}?api-key=<KEY>`
- **DAS API (digital assets):** methods getAsset / getAssetsByOwner / getAssetBatch / searchAssets
- **Token APIs:** token metadata, token accounts, token holders
- **Webhooks v1:** create/edit/delete (accountAddresses + transactionTypes, e.g. CREATE_POOL on pump AMM for slow-grad scanner)
- **WebSockets:** `wss://mainnet.helius-rpc.com/?api-key=<KEY>` (accountSubscribe, signatureSubscribe, logsSubscribe, programSubscribe)
- **More:** LaserStream gRPC, Sender (regional `slc-|ewr-|lon-|fra-|ams-|sg-|tyo-.sender.helius-rpc.com`), historical data, MCP (`npx helius-mcp@latest`)
- **Desk use:** mint authorities, tx history, mother wallet, slow-grad fill time, deployer memory, webhook scanner

### Shyft.to — ⚠️ PARTIAL (RPC works, holder REST is blocked)
Docs: https://docs.shyft.to · **RPC:** `https://rpc.shyft.to?api_key=<KEY>` · **WS:** `wss://rpc.shyft.to?api_key=<KEY>` · Auth: `api_key` query param
- Standard JSON-RPC works (getTokenSupply/getHealth verified Oct 16 2026)
- ❌ `getTokenLargestAccounts` → **403 Forbidden** (free tier blocks it); `GET /sol/v1/token/get_owners` → 404/417 across param variants (token_address/address/mint, GET+POST, query+header auth) — NOT usable for holder snapshots on free tier
- ❌ GraphQL SuperIndexer `graphql.shyft.to` doesn't resolve from this box; real host is `programs.shyft.to/v0/graphql/?api_key=…` (program-scoped, not token holders)
- **Desk use:** RPC fallback for mint/tx checks ONLY. NOT a holder source.

### GoPlus Security API — ✅ WIRED (AEGIS check 13)
Docs: https://docs.gopluslabs.io · Base: `https://api.gopluslabs.io`
- ✅ **`GET /api/v1/solana/token_security?contract_addresses={mint}`** — verified works (keyless): mintable, freezable, closable, transfer_fee, transfer_hook, non_transferable, metadata_mutable, trusted_token
- ❌ `GET /api/v1/token_security/1399811149?…` → "main chain is not supported" (the doc's generic chain-id path does NOT work for Solana)
- **Desk use:** WIRED — `desk_common.goplus_security(mint)` → `aegis.score_coin` check 13 (transfer_hook −20, non_transferable −15, trusted_token +10). Rate-limited via RateLimiter (60/min).

### SolanaTracker Data API — ✅ WIRED (replaces FomoAPI trader leaderboard — 402-locked)
Docs: https://docs.solanatracker.io · Base: `https://data.solanatracker.io` · Auth: `x-api-key` header (NOT API-Key, NOT Bearer)
Key stored: CONFIG.md `SOLANATRACKER_KEY` (user-provided Oct 16 2026)
- ✅ **`GET /v2/pnl/leaderboard/top?limit=N&days=1|7|30|90&sort=realized&minTrades=5`** — Solana trader rankings. ⚠️ param is **`days`** (int), NOT `window` (window silently defaults to 90 — verified live). Response: traders[] → {wallet, period.realized, period.days.winRate (PERCENT, /100 for ratio), period.volume, roi, counts.trades}. winRate for 1-day window is often 100% (single profitable day artifact).
- ✅ **`GET /v2/pnl/wallets/{wallet}`** — wallet summary (path WITHOUT `/summary` — that variant 404s). Fields: summary.pnl.realized, summary.totals.winRate, summary.timing.avgHoldTimeSecs.
- ✅ `GET /v2/pnl/wallets/{wallet}/positions`, `/performance`, `/highlights` — all 200 live.
- ✅ `GET /credits` — remaining credits (731 seen Oct 16). Free tier ~13 reqs/refresh (3 windows + ~10 summaries) @ 8h = ~1,170/mo.
- ✅ `GET /price?token=`, `GET /wallet/{addr}` — 200 keyless-with-key, but 403 keyless (Cloudflare).
- ⚠️ `http_json()` got 403 on ST (its UA/retry path); use raw `urllib` + `x-api-key` + `User-Agent: Mozilla/5.0` (see cohort.py).
- ❌ `/v2/leaderboard/{window}` (FomoAPI-style) — 400/402; FomoAPI trader leaderboard itself = **402 Payment Required** (credit-locked).
- **Desk use:** WIRED — `desk.py cohort [--refresh]` → refresh() fills leaderboard_snapshots (source=solanatracker), qualifying_cohort() = 24h∩7d∩30d intersection + win-rate ≥45% + hold ≥180s (enriched via wallet summaries) + entity collapse.

### GMGN quotation rank — ✅ WIRED (free, keyless discovery feed)
Docs: (reverse-engineered, verified Oct 16 2026)
- ✅ **`GET https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{1m|5m|1h|6h|24h}?orderby={volume|swaps|marketcap|liquidity|open_timestamp|holder_count|smartmoney}&direction=desc&filters[]=not_honeypot`** — verified live: 200, 50 rows, rich fields (price/volume/market_cap/liquidity/holder_count/sniper_count/bundler_rate/top_10_holder_rate/rug_ratio/is_wash_trading/buy_tax/sell_tax/creator_close/launchpad)
- ⚠️ Cloudflare-sensitive: bare requests 403; needs browser-like headers + gzip/deflate decompression (handled in `desk_common.gmgn_rank`)
- ⚠️ Rate-limited — throttled via RateLimiter (30/min)
- ❌ Router endpoint `gmgn.ai/defi/router/v1/sol/tx/get_swap_route` RETIRED (HTTP 410) → moved to `openapi.gmgn.ai/v1/trade/quote` (needs Agent API key)
- **Desk use:** WIRED — `desk.py gmgn [timeframe] [order] [n]` prints ranked Pump.fun table (MC/vol/holders/snipers/rug). Discovery feed only, never generates entries.

### FluxRPC — ✅ WORKS
- **RPC:** `https://eu.fluxrpc.com?key=<KEY>` · **WS:** `wss://ws.eu.fluxrpc.com` · Auth: `key` query param (getHealth verified)
- **Also the auth for Rugcheck API** (see §4)
- **Desk use:** RPC fallback + Rugcheck auth

### Publicnode (free fallback) — ✅ WORKS
- **RPC:** `https://solana-rpc.publicnode.com` · **WS:** `wss://solana-rpc.publicnode.com` — no key
- **Desk use:** zero-cost fallback (getHealth/getAccountInfo verified)

---

## 2. Market / Token Data APIs

### DexScreener — ✅ WORKS (free, no key)
Docs: https://docs.dexscreener.com · Base: `https://api.dexscreener.com`
- `GET /latest/dex/tokens/{addr}` — pairs for token (price/MC/liq/vol/txns/chg)
- `GET /latest/dex/pairs/{chain}/{pairAddr}` — single pair
- `GET /latest/dex/search?q=<query>` — token/pair search
- `GET /latest/dex/pairs/{chain}/created?token=<addr>` — newly created pairs
- `GET /token-profiles/latest/v1` — new token profiles
- `GET /token-boosts/latest/v1` · `GET /token-boosts/top/v1` — boosted
- ⚠️ 429s under bursts → retry/backoff in desk_common
- **Desk use:** core market data for scanner + position monitor (verified)

### GeckoTerminal — ✅ WORKS (free, no key)
Docs: https://www.geckoterminal.com/dex-api · Base: `https://api.geckoterminal.com/api/v2`
- `GET /networks` · `GET /networks/{network}/tokens/{addr}` (verified) · `GET /networks/{network}/tokens/{addr}/pools`
- `GET /networks/{network}/pools/{pool}` · `GET /networks/{network}/pools/{pool}/ohlcv?timeframe=1h`
- `GET /search/pools?query=` · `GET /search/tokens?query=` · `GET /networks/{network}/trending_pools`
- **Desk use:** token metadata + candle fallback

### DexPaprika — ✅ WORKS (key in bare Authorization header)
Docs: https://docs.dexpaprika.com/llms.txt · Base: `https://api.dexpaprika.com` (36 chains)
- `GET /networks` · `GET /networks/{network}`
- `GET /networks/{network}/tokens/{addr}` (verified w/ key) · `GET /networks/{network}/tokens/{addr}/pools`
- `GET /networks/{network}/pools/search?token_address=&volume_min=&liquidity_min=` (pool screener)
- `GET /networks/{network}/pools/{pool}` · `GET /networks/{network}/pools/{pool}/ohlcv?start=-7d&interval=1h`
- `GET /networks/{network}/pools/{pool}/transactions` (paid)
- `GET /tokens/{addr}` · `GET /search?query=SYM` · `POST /networks/{network}/tokens/prices` (batch ≤10)
- `GET /usage` — plan/credits
- **SSE:** `https://streaming.dexpaprika.com` (prices/reserves) · `https://streaming-pro.dexpaprika.com` (txns/candles, paid)
- **Pro host:** `https://api-pro.dexpaprika.com` (Authorization: api_KEY)
- **Auth:** free key = bare `Authorization: <api_...>` (no scheme word); keyless anon 402 when monthly limit hit
- **MCP/SDKs/CLI** available
- **Desk use:** token/pool/OHLCV backup, pool screener, cross-chain search

### CoinStats — ⚠️ CREDITS DEPLETED (dead until renewal)
Docs: https://coinstats.app/api-docs · Base: `https://openapiv1.coinstats.app/v1` · Auth: `X-API-KEY: *** (32-hex)
- ⚠️ **Oct 16 2026: EVERY endpoint returns HTTP 406 "Credits limit reached. Please upgrade or wait for renewal"** — probed both hosts (`api.coinstats.app` and `openapiv1.coinstats.app`), all paths, with Accept json — key itself is valid, free-plan credits are exhausted
- `GET /coins?limit=N&page=` (verified pre-depletion) · `GET /coins/{id}` · `GET /coins?coinIds=a,b` · `GET /coins/price/{id}`
- **Wallet:** `GET /wallet/balance?address=<addr>&connectionId=solana` → dead, same 406
- **Desk use:** ✅ WORKS endpoints assumed dead until credits renew; `coinstats_balance()` wrapper is a silent no-op — do NOT rely on it (position_monitor GOOD_WALLET_SELLING silently degrades). Re-probe on renewal.

### Birdeye — ⚠️ PARTIAL (key works for market data; token_security needs paid package)
Docs: https://docs.birdeye.so · Base: `https://public-api.birdeye.so` · Auth: `X-API-KEY: <KEY>` + `x-chain: solana`
- ✅ Verified: `GET /defi/price?address=&chain=` · `GET /defi/token_overview?address=`
- **Price/OHLCV:** `GET /defi/multi_price?list_of_address=` · `GET /defi/ohlcv?address=&type=1m|1h|1d` · `GET /defi/v3/ohlcv` (1s/15s/30s) · `GET /defi/history_price` · `GET /defi/price_volume/single`
- **Token:** `GET /defi/token_creation_info` · `GET /defi/token_security?address=` ⚠️ 401 "lacks sufficient permissions" (needs paid package) · `GET /defi/v3/token/list` · `GET /defi/trending_tokens`
- **Wallet:** `GET /defi/wallet/token_list?wallet=` · `GET /defi/wallet/token_balance` · top traders · funded-by
- **Blockchain:** token metadata · token accounts · transaction detail (solana)
- **WS:** `wss://public-api.birdeye.so/socket` — SUBSCRIBE_PRICE, SUBSCRIBE_TXS, SUBSCRIBE_TOKEN_NEW_LISTING, SUBSCRIBE_NEW_PAIR, SUBSCRIBE_LARGE_TRADE_TXS, SUBSCRIBE_WALLET_TXS
- **x402 pay-per-request** (no key) available
- **Desk use:** price/overview fallback now; security needs package (Rugcheck covers it)

### Ave.ai — ✅ WORKS (correct: header `X-API-KEY`, host prod.ave-api.com/v2)
Docs: https://docs.ave.ai/llms-full.txt · Base: `https://prod.ave-api.com/v2` · Auth: **`X-API-KEY: <KEY>`** (NOT `Ave-Auth` — that returns "api key invalid")
- `GET /tokens?keyword=<symbol|CA>&chain=&limit=&orderby=` — search (verified 200)
- `POST /tokens/price` body `{token_ids:["<CA>-<chain>"]}` (verified 200)
- `GET /tokens/{token-id}` · `GET /tokens/main?chain=` · `GET /tokens/trending?chain=` · `GET /tokens/top100/{token-id}`
- `GET /ranks/topics` · `GET /ranks?topic={topic}` — rank boards (hot/meme/gainer/new/loser/ai/...)
- `GET /klines/pair/{pair-id}?interval=&size=` · `GET /klines/token/{token-id}?interval=&size=`
- `GET /txs/{pair-id}?limit=&size=&to_time=` — transactions
- `GET /contracts/{token-id}` — **Contract Risk Detection Report** (verified 200: risk_score, holders, token_holders_rank, pair_holders_rank, honeypot flags, is_mintable, anti_whale...)
- `GET /supported_chains`
- ⚠️ v1 host `openapi.avedata.org` does NOT resolve in this env — use v2
- **Desk use:** Ave risk report (verified) + trending boards + candles

---

## 3. Trading / Execution APIs

### Jupiter — ✅ WORKS (keyed route on api.jup.ag)
Docs: https://station.jup.ag/docs (llms.txt: /docs/llms.txt) · Base: `https://api.jup.ag` · Auth: `Authorization: Bearer jup_pub_...`
- ✅ **Swap quote:** `GET /swap/v1/quote?inputMint=&outputMint=&amount=&slippageBps=` (verified)
- `POST /swap/v1/swap-instructions` — build tx (quote → swap-instructions → sign)
- **Managed swap:** `POST /swap/v1/swap` — one-call swap with key
- **Price:** `GET /price?ids=` (price.jup.ag does NOT resolve here — use DexScreener for USD)
- **Tokens:** `GET /tokens` — list/metadata/search/verification
- **Perps:** order/position/prices · **LO & DCA** (limit orders, DCA) · **Lend** · **Prediction**
- Platform: https://developers.jup.ag/portal · MCP, CLI, Agent Skills
- ⚠️ `quote-api.jup.ag` subdomain does NOT resolve in this env — always `api.jup.ag/swap/v1/quote`
- **Desk use:** fee/slippage estimator, execution math validation

### PumpAPI — ⚠️ PARTIAL (data free/keyless; trade needs dashboard apiKey)
Docs: https://pumpapi.io/llms-full.txt
- **Data stream (WS, free, no key):** `wss://stream.pumpapi.io/` — all events: transfers, creations, buys/sells, migrations, pool creations, liq add/remove (Pump.fun + PumpSwap + Raydium CPMM + Meteora) — filter client-side, 1 connection
- **Replay archives (free):** `https://replay.pumpapi.io/YEAR/MONTH/DAY/HOUR.jsonl.zst` — hourly compressed events
- **Trade API (REST):** `https://api.pumpapi.io` POST — actions: buy/sell, wrapSol, priorityFee, token create, Jito bundles; auth: `apiKey` or `privateKey` in body; 0.25% trade fee; local (unsigned) tx supported
- **Desk use:** real-time tape + replay for wash-trade/LPI backtests

### PumpDev — ✅ WORKS (health/wallet verified; WS keyless)
Docs: https://pumpdev.io · Base: `https://pumpdev.io/api`
- ✅ `GET /api/health` (verified) · ✅ `POST /api/wallet/create` (returns apiKey+keypair — ⚠️ test artifact, never trade with it)
- `POST /api/trade-lightning?api-key=<KEY>` — one-call buy/sell (server signs)
- `POST /api/create-lightning?api-key=<KEY>` — one-call token launch
- `POST /api/bundle-lightning` — atomic Jito bundle
- `POST /api/trade-local` — build tx for client signing · `POST /api/trade-bundle` — multi-sell
- `POST /api/create` · `POST /api/create-bundle` · `POST /api/bundle`
- `GET|POST /api/claim-account` (creator fees) · `GET|POST /api/claim-cashback` · `POST /api/claim-distribute`
- `POST /api/transfer` · `POST /api/transfer-all` · `POST /api/wallet/import`
- **WS (free, no key):** `wss://pumpdev.io/ws` — subscribeNewToken / subscribeTokenTrade (keys: mints) / subscribeAccountTrade (keys: wallets) + unsubscribe
- **Robinhood Chain (PONS):** `https://rhc.pumpdev.io/api/health|quote|trade-local|claim-*` + `wss://rhc.pumpdev.io/ws`
- **Desk use:** real-time launch/whale tape now (WS keyless); execution later (0.25%)

---

## 4. Forensics / Intelligence APIs

### Rugcheck — ✅ WORKS (auth = FluxRPC key via query param)
Docs: https://fluxrpc.com/docs/rugcheck
- `GET https://api.rugcheck.xyz/v1/tokens/{mint}/report?api_key=<FLUX_KEY>` — FULL: creator, creatorBalance, creatorTokens, topHolders (pct), rugged, graphInsidersDetected, insiderNetworks, mintAuthority, freezeAuthority, transferFee, lockers, knownAccounts, launchpad, deployPlatform, markets, events, totalHolders (verified)
- `GET .../report/summary?api_key=<FLUX_KEY>` — score, risks, lpLockedPct, tokenProgram (verified)
- `GET .../topHolders` · `GET .../info` (may need paid plan) · `POST /v1/bulk/tokens/report|summary` (paid)
- **Auth:** `api_key=<flux>` query param. ⚠️ `X-API-KEY` header and Bearer both 401 — query param only
- **Desk use:** AEGIS six checks + deployer second brain (creator + creatorTokens), LP lock, top-10 concentration

### FomoAPI (fomo.family data) — ✅ WORKS
Docs: https://fomoapi.io/docs · Base: `https://api.fomoapi.io` · Auth: `Authorization: Bearer <fapi_...>`
- `GET /v1` (info/tier, verified) · `GET /health` (keyless)
- `GET /v2/leaderboard/{window}` — 24h|7d|30d|all (verified): handle, userId, pnlUsd, volumeUsd, trades, wallets{solana,evm}, topTokens, verified
- `GET /v2/leaderboard/tokens/trending|most-held|graduated` — token boards (live)
- `GET /v2/users/{handle}` (~35 fields; 2,500 credits) · `GET /v2/users/id/{userId}`
- `GET /v2/users/{handle}/positions|balances|trades`
- `GET /token/{address}/holders` — smart-money holders · `GET /v2/thesis/token/{mint}` (1,250 cr) · `GET /v2/thesis/user/{id}`
- `GET /v2/search?q=` · `GET /v2/tokens/search?q=`
- `GET /v2/alerts?limit=&type=&chain=&since=` — activity firehose (REST)
- `GET /v2/notifications?notificationType=LARGE_BUY|...` — push mirror
- `GET /v2/me` — your balance · `POST /pay/create` — top up USDC
- **WS:** `wss://api.fomoapi.io/ws/alerts?key=<KEY>` (app feed; free 7d realtime then 15s delay; ?trader=/?chain=) · `wss://api.fomoapi.io/ws/trades?key=<KEY>&chain=solana` (Growth+; ~3.5s before app)
- Credits: leaderboard 250 · alerts 125 · thesis 1,250 · wallet resolution 2,500; free 250K/mo
- **Desk use:** smart-money holders, trader track records → behavior index (KOL vs real), conviction signals

### Bitquery — ✅ WORKS (correct host = V2 streaming endpoint)
Docs: https://docs.bitquery.io/llms.txt
- **V2 (Solana/EVM/BSC/Base/Arb/Opt/Pol/Robinhood/Tron/HL):** `https://streaming.bitquery.io/graphql` (+ `wss://streaming.bitquery.io/graphql`) — roots `EVM`/`Solana`/`Trading`; `dataset: realtime|archive|combined`
  - ✅ Auth + realtime verified (EVM DEXTrades 200). Free plan = realtime only; archive needs add-on
- **V1 (Bitcoin/Litecoin/Cardano/etc.):** `https://graphql.bitquery.io` — lowercase roots
- **Coinpath (multi-hop fund tracing):** GraphQL `coinpath(initialAddress, depth)` — archive-class → blocked on free
- **Pump.fun API:** creation, bonding curve progress, graduation, ATH MC, top traders, first-buyer/sniper detection
- **MCP:** `https://mcp.bitquery.io` (OAuth; headless `?token=`)
- **Auth:** `Authorization: Bearer ory_at_...` (verified) or X-API-KEY
- **Desk use:** realtime DEX trades/OHLC + pump.fun graduation signals; Coinpath after upgrade

### Nansen — ⚠️ LIMITED (setup CORRECT; 0 credits on key)
Docs: https://docs.nansen.ai/api.md · Base: `https://api.nansen.ai/api/v1` · Auth: header **`apikey: nsn_...`** (X-API-KEY → 401)
- ✅ key authenticates; body field is **`wallet_address`** (not `address`); endpoint returns **403 "Insufficient credits"** → top-up needed at app.nansen.ai
- **Profiler:** `/profiler/address/labels` · `/premium-labels` · `/current-balances` · `/historical-balances` · `/dex-trades` · `/transactions` · `/counterparties` (+batch) · `/related-wallets` · `/first-funder` · `/pnl-and-trade-performance` · `/perp-positions` · `/perp-trades`
- **Smart Money:** `/smart-money/netflows|holdings|historical-holdings|dex-trades|perp-trades|jupiter-dcas|pnl-leaderboard`
- **Token God Mode:** token information · indicators · price OHLCV · token screener · holders · flows · who-bought-sold · dex-trades · token transfers · perp screener/leaderboard
- **Funds:** fund holdings · **Portfolio:** DeFi holdings · **Hyperliquid** · **Smart Alerts** (Telegram/Slack/Discord)
- **Backtesting/historical v1:** historical txs, token screener, top holders, DEX trades, OHLCV, smart-money positions
- **Desk use (intended):** wallet labels (CEX/bridge/smart money), first-funder, smart-money flows — after credits

### Dune — ✅ WORKS (key valid; free-tier datapoint cap + needs query ids)
Docs: https://docs.dune.com/api-reference (llms.txt: /llms.txt) · Base: `https://api.dune.com/api/v1` · Auth: `X-Dune-API-Key: <KEY>` (verified: auth passes — 4xx/402, not 401)
- `POST /query/{query_id}/execute` → execution_id · `GET /query/{query_id}/results`
- `GET /execution/{execution_id}/status|results`
- `POST /sql/execute` body `{sql, performance:"small"}` — arbitrary SQL (dex.trades, solana.account_activity) — ✅ auth OK; free tier 402 datapoint cap → adjust subscription or use saved queries
- **Query management** (create/update) · **Data uploads** (tables) · **Materialized views** · **Pipelines**
- **Usage:** account management endpoints (check docs) · **MCP**
- **Desk use (intended):** multi-wallet sybil clustering SQL, wash-trade SQL — after tier

---

## 5. Web Tools (manual / browser reference)

Browser automation currently BROKEN in this env (Chrome won't start) → used via web_extract or by the user manually:
- **GMGN.ai** — terminal: tx/pools/holders, copy-trading, wallet P&L (multichain)
- **Rugcheck.xyz** — web UI for Rugcheck API · **Solscan** — raw tx explorer
- **Discovery tapes:** degentape.com · fomopulse.app · rhtrenches.com
- **Wallet trackers:** 985monitor.xyz/fomo · nockterminal.com/scout · hoodwatch.io · stalkchain.com
- **Dune dashboard:** dune.com/dxrg/dx-terminal-pro (LLM-agent behavior evidence)

## 6. Telegram Call Channels (polled via t.me/s/<name>, not APIs)

| Channel | Status |
|---|---|
| sol_trend, solwhaletrending, solearlytrending, PIRBViewTrending, soltrenchtrending, SeekrTrending | ✅ parseable |
| signalsolanaby4am | ❌ preview empty (private) — paste calls manually |

---

## Quick lookup: what to use for which job

| Job | Tool |
|---|---|
| Price/MC/liq/vol | DexScreener → Birdeye price/overview → DexPaprika → GeckoTerminal → CoinStats |
| Mint authorities / raw RPC | Helius → Shyft → FluxRPC → publicnode |
| Wallet first-tx / mother wallet | Helius `v0/addresses/{w}/transactions?sortOrder=asc` |
| Contract risk report | **Ave `/v2/contracts/{CA}-solana`** (verified) + Rugcheck full report |
| Token security + deployer 2nd brain | Rugcheck full report (FluxRPC key) |
| Smart-money holders / trader records | FomoAPI holders + users |
| Real-time launch/trade tape | PumpDev WS (keyless) / PumpAPI WS / Birdeye WS |
| Historical event reconstruction | PumpAPI replay archives |
| Quote / fee / slippage | Jupiter `api.jup.ag/swap/v1/quote` (Bearer jup_pub_) |
| Realtime DEX trades / pump graduations | Bitquery V2 streaming GraphQL (realtime dataset) |
| Wallet labels / smart money | Nansen (after credit top-up) |
| Multi-wallet sybil SQL | Dune sql/execute (after tier) |
| Multi-hop fund tracing | Bitquery Coinpath (after historical add-on) |
| Portfolio / wallet balance | CoinStats wallet + portfolio endpoints |
| Trending boards / narrative | Ave ranks/topics + Birdeye trending + FomoAPI token boards |

## Quick fixes learned (setup corrections 2026-10-15)
1. **Ave.ai**: header must be `X-API-KEY` (not `Ave-Auth`), host `prod.ave-api.com/v2` — search + price + contracts verified 200.
2. **Birdeye**: key works for `defi/price` + `defi/token_overview`; `defi/token_security` needs paid package (401).
3. **Bitquery**: Solana/EVM live on **V2** `streaming.bitquery.io/graphql` with `dataset: realtime` — earlier V1 probe was wrong host. Bearer ory_at_ verified.
4. **Nansen**: header `apikey` + body `wallet_address` is correct; key has 0 credits → top up at app.nansen.ai.
5. **Dune**: auth works; `POST /sql/execute` performance="small" → 402 free-tier datapoint cap; use saved query ids or upgrade.
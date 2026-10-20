# 🧰 ToolStacks — Memecoin Quant Desk Tool Inventory (ALL endpoints)

**Living reference.** Every tool/API/WS/RPC the user provides gets appended here with endpoints + summary.
Before building anything: scan this file for the right tool. Credentials live in `CONFIG.md` (same dir).
Last verified: **2026-10-18** (full live battery across all providers, AE mint AeLonpr… as test target).

**Oct 18 live-battery results (probe script /tmp/toolstack_probe*.py):**
- ✅ **WORKS today:** Helius RPC (getAccountInfo 281ms), GeckoTerminal (305ms), Birdeye overview (218ms), Ave search + contracts-risk (217/209ms, risk 55), GMGN rank 1h (274ms), DexPaprika token + pools/search (1.0s/0.9s), Jupiter quote (29ms, impact 1.49%), Rugcheck summary (520ms), GoPlus (97ms), WalletIntel leaderboard (590ms), Sol.wales moves (540ms), Solscan chaininfo (366ms), Blix health (9ms), FomoAPI /v1 (553ms — NOT dead, just leaderboard credit-locked), PumpDev health (992ms), Shyft getTokenSupply (230ms), SolanaTracker /credits (245ms, 200)
- ⚠️ **DEGRADED/locked:** Bitquery realtime GraphQL → **HTTP 402** (realtime now paywalled), Dune → 401 (key/scope), Nansen → 401 (credits), CoinStats → 406 (credits exhausted, unchanged), PumpAPI replay archive → 403 (UA/plan)
- ⚠️ **Intermittent:** DexScreener 429'd once during battery (retry/backoff handles it)
- 🔌 **Network-blocked from this box (probe from user network):** CabalSpy api+api-id+stream, MadeOnSol api/v1, GMGN openapi (CF-403)

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
| Whale/KOL flow tape (free) | **Sol.wales `/api/v1/moves/recent`** (keyless) — all-wallet moves poll |
| Wallet PnL / strategy score (free, tiny) | **WalletIntel `/wallet/{addr}/pnl|summary`** (10 req/day) |
| KOL/smart-money roster + per-token holdings tape | **CabalSpy** `/v1/wallets` + `/v1/tokens/transactions` (10k credits free/mo) |
| KOL coordination / first-touch / entry order | **MadeOnSol** `/kol/coordination`, `/kol/first-touches`, `/kol/tokens/{mint}/entry-order` |
| Deployer reputation (no-lookahead) | **MadeOnSol** `/deployer-hunter/{wallet}` + `/{wallet}/history` (PRO) |
| Buyer-quality / dump-cluster / bundle residual | **MadeOnSol** `/tokens/{mint}/buyer-quality`, `/tokens/{mint}/bundle` |
| Rug-risk w/ dev-exit read | **MadeOnSol** `/tokens/{mint}/risk` (PRO) — auditable factors + wallet_empty |
| Real pool depth / price impact | **MadeOnSol** `/tokens/{mint}/depth` (PRO) — SOL to move 1/5/10% |
| Block/chain info (free) | **Solscan** `/chaininfo` (public, token JWT) |
| Market snapshot / event stream | **Blixvip** `/api/overview` + `/api/stream` (login-gated) |

### Blixvip — ⚠️ SELF-HOSTED LOCALHOST APP (not a hosted public API!)
Repo: https://github.com/blixvip/blixtrade · Docs: `docs/API.md` (130 lines, complete) · Base: **`http://localhost:4420`** (RADAR_PORT) · **LIVE + VERIFIED Oct 18** (372 tracked, 48 launches/hr, pump connected)
- **Hosted blixvip.com = sign-in + account permissions (NOT anonymous)**; execution spends real SOL through authenticated workflows. BUT the whole app is open-source and self-hostable — run it locally and ALL endpoints are free/instant.
- ✅ Full API surface (self-hosted): `GET /api/overview` (KPIs: launches/hr, tracking, pass-safety, signals 24h, graduations) · `/api/pulse` (4 columns: new/final-stretch/migrated/running w/ per-coin stats + AI verdicts) · `/api/tokens` (coins table filters) · `/api/token/{mint}` (full detail: price history, safety report, holders summary, socials, research, signals) · `/api/token/{mint}/explain` · `/api/holders/{mint}?top=25` (**live holder ledger: top holders tagged dev/sniper/bundle + top-10 share**) · `/api/candles/{mint}?tf=5s|15s|1m|5m|15m|1h` (MC candles from real trades) · `/api/quote?mint=&side=buy|sell&amount=&slip=` (real Jupiter quote)
- Signals/narratives: `/api/signals` (type, safety, group) · `/api/narratives` (heat, lift, emerging words, lead coins) · `/api/clusters` (narrative bursts + Grok calls) · `/api/briefs`
- Research/picks: `/api/research` + `/api/research/{mint}` (first read, deep read, grade, thesis, plan) · `/api/triage` · `/api/picks` + `/api/picks/all` (entry/exit/peak/rule result = backtest feed) · `/api/perf` (track record per signal type: checked, up 20%+, down 50%+, median, worst, reached 2x — 60s cache)
- Wallets/Fomo: `/api/wallets` · `/api/wallet/{addr}` (swaps, realized/open/sellable PnL, positions, **linked wallets**, alert rule) · `/api/fomo` (learned fee payer, what Fomo is buying, top Fomo traders) · `/api/fomo/bars/{mint}` (Fomo's chart bars)
- Paper desk: `/api/paper` + `/quoted`/`/sellq` (real quote fills) — nothing is ever bought.
- Stream events: `tick`, `launch` (sub-second pump.fun), `live`, `signal` (type, mint, symbol, text, safety, score), `mig` (graduation), `pic`, `paper`, `brief` — SSE stateless.
- ⚠️ From THIS box: hosted `blixvip.com/api/overview` → "Sign in at blixvip.com" (needs account creds); the localhost app would be free if self-hosted here.
- **AEGIS mapping (if self-hosted locally):** `/api/holders/{mint}` top-holders dev/sniper/bundle tags = direct AEGIS holder-quality factor; `/api/perf` + `/api/picks/all` = empirical signal-efficiency backtests; `/api/token/{mint}/explain` + research grade = AI narrative layer; `/api/fomo` = FomoAPi-style smart-money tracker; `/api/signals?group=coin` = confluence enrichment. Execution stays paper.

### BLIX COMPLETE CAPABILITY INVENTORY (probed LIVE Oct 18, localhost:4420)
**Stream (SSE `/api/stream`)**: `tick` (KPIs every few s), `launch` (sub-second pump.fun: mint/name/symbol/dev/uri), `live` (price/mcap/flow for Pulse coins), `signal` (type/mint/symbol/text/safety/score), `mig` (graduation), `pic`, `paper`, `brief` — stateless, no Last-Event-ID needed. Verified: research signals + migrations live.
**Market**: `/overview` (tracked, launchesPerHour, graduations, signals24h) · `/pulse` (newPairs/finalStretch/migrated/running w/ per-coin stats) · `/tokens?sort=&safety=&stage=` (filterable table: mint/symbol/mcap/vol/buys/sells/safety) · `/token/{mint}` (price history + safety report + holders summary + socials + research + signals; 404 if never tracked) · `/token/{mint}/explain` (AI explanation — **needs Groq/Claude/Grok key**, else 500) · `/candles/{mint}?tf=5s|15s|1m|5m|15m|1h` (MC candles from REAL trades w/ volume) · `/quote?mint=&side=&amount=&slip=` (**real Jupiter quote**: inSol/outTokens/impactPct/feeSol/route/minOut — verified 17.9% impact on a curve coin)
**Holders**: `/holders/{mint}?top=25` — ⚠️ response `holders`=count INT, actual list under **`top`**; each: wallet, pct (2.71 = 2.71%, NOT fraction), sol, trades, tag (dev/sniper/bundle); plus top10, dev, snipers, bundle. Verified: 67 holders, T10 18.1%.
**Signals/narratives**: `/signals?type=&safety=passed|unscreened&group=coin&limit=` (graduated/momentum w/ score + mcap — verified $MINTER $4.5M, momentum 73/100) · `/narratives` (themes: name, launchShare, launchLift, heat, momentum, volume, top coins — verified live) · `/clusters` (narrative bursts w/ keys + Grok calls) · `/briefs` (last 20 — currently [] until AI key) · `/events` (last 80 system events — **research signals like "Early signal: $X at 120s, 24% chance of a 2x (top 5% of launches)"** + migrations + rate-limit notices — verified live)
**Research/picks**: `/research` (desk status: near-stage coins w/ mcap/vol/safety/progress) · `/research/{mint}` (first read/deep read/grade/thesis/plan/sources; "expired" if coin died before read) · `/triage` (first-read gate record — [] w/o AI gate) · `/picks` + `/picks/all` (scorecard by stage/action/day + every pick w/ entry/exit/peak/rule result) · POST `/picks/review` (Grok self-review), `/picks/scout` (narrative scout run)
**Paper desk**: `/paper` (open/closed positions, sol, sizes, wallet w/ start/cash/equity/realized/pnl) · POST `/paper/quoted` + `/paper` (real-quote / last-price fill) · `/paper/{id}/preview|sellq|sell|rule|DELETE` (real-quote exits, TP/SL/trail rules). Blix's OWN paper ledger — separate from our paper_book, don't mix.
**Wallets/Fomo**: `/wallets` (followed wallets + smart ranking + activity + tracking stats) · POST `/wallets` (follow) · `/wallet/{address}` (swaps, realized/open/sellable PnL, positions, **linked wallets**, alert rule) · POST `/wallet/{address}` (scan/follow/update) · `/fomo` (connected state, learned fee payer, hot, traders) · POST `/fomo/trader` · `/fomo/bars/{mint}?res=&since=` (⚠️ needs a browser session for Fomo — 500 "No browser" on this box)
**Alerts/ops**: `/alerts` (rules: scope coin/wallet/type, mute/quiet/cooldown, destinations) · `/perf` (track record per signal type: up20/down50/2x/medianPeak — 60s cache) · `/health` (every dependency w/ last success/failure/what to do — 4s cache) · `/settings` (public) + POST (⚠️ secrets never returned but CAN be set: heliusProjectId, jupiterKey, telegramToken/Chat, groqKey, rpcUrl, thresholds, notifyKinds) · `/export?secrets=1` / `/import` / `/backup` / `/repair` / `/test-notify|grok|fast`
**Config knobs visible in /api/settings** (to unlock more): `heliusProjectId` + `rpcUrl`/`rpcWsUrl` (we HAVE a Helius key → faster + deeper wallet tracking), `jupiterKey` (we have one), `telegramToken`+`telegramChat` (HMQ bot → DM alerts; sendMessage-only so NO gateway getUpdates conflict), `groqKey`/`fastProvider`/`deepProvider` (unlocks explain/research/narratives/picks — currently 500 without), `notifyKinds` (launch/graduated/momentum/volume/dump/dev-sold/rugged/liq-pulled/research/fomo/pick/narrative), `maxTracked`, `minSafety`, thresholds.
- **AEGIS integration map (ALL verified endpoints)**: (1) `/holders` top[] dev/sniper/bundle tags → new AEGIS holder-quality factor (complements Rugcheck top-10); (2) `/candles` 5s-1h real-trade MC candles → replace/augment DexPaprika 1h OHLCV for realized-vol + fragility; (3) `/quote` real Jupiter → independent paper entry/exit validation vs our jupiter_estimator; (4) `/signals` + `/events` research signals → confluence enrichment for curated calls (never entries); (5) `/narratives` heat/lift → thesis-engine social_attention + momentum dims; (6) `/research/{mint}` grade/thesis → narrative layer for conviction holds; (7) `/picks/all` + `/perf` → empirical signal-efficacy backtests; (8) `/wallet/{address}` linked wallets + PnL → wallet-intel enhancement (parallel to WalletIntel); (9) Stream SSE → sub-second monitor for held positions; (10) `/tokens` table w/ safety filter → cross-check scan-group consensus.
- **Needs extra setup to unlock**: explain/deep research/narratives/picks (AI key: groqKey or claude/grok CLI), fomo bars (browser), alerts delivery (telegramToken/Chat). All "never-entry" enrichment.

### GMGN Agent API — ⚠️ KEY VALID, openapi.gmgn.ai Cloudflare-blocked from this box
Docs: https://docs.gmgn.ai/index/gmgn-agent-api.md · Base: `https://openapi.gmgn.ai/api/v1` · Auth: `X-API-KEY: gmgn_…` (user key Oct 17: stored CONFIG.md `GMGN_AGENT_KEY`)
- Two API families: **Agent API** (this key, REST: token search/detail/security/holders/traders, market candles, trending, wallet PnL/holdings/history, agent_score, swap) and **Callout OpenAPI** (`https://papi.gmgn.ai/callout/openapi/v1` — POST-only, AK/SK HMAC-SHA256 signature, X-Ak/X-Timestamp/X-Signature headers; endpoints /global, /token, /get_record, /create; 1 QPS on /global).
- Agent API endpoints (documented surface): `GET /api/v1/token/search?query=` · `GET /api/v1/token/sol/{address}` · `.../token/sol/{address}/security` · `.../top_holders` · `.../top_traders` · `GET /api/v1/market/sol/candles?token=&period=&amount=` · `GET /api/v1/tokens/sol/trending` · `GET /api/v1/wallet/sol/{wallet}/pnl|holdings|history` · `GET /api/v1/agent_score/sol/token?address=&refresh=` · trade: `/api/v1/trade/swap` (hosted wallet, needs private key — NOT configured).
- ⚠️ Connectivity: `openapi.gmgn.ai` → 403 Cloudflare "Just a moment..." from this datacenter IP (curl + browser-style headers both); `api.gmgn.ai` doesn't resolve. The free `gmgn.ai/defi/quotation/v1/rank` endpoint still works keyless.
- **AEGIS mapping (when reachable):** agent_score (bot-confidence / sniper/insider/bundle flags), token security + top_holders + top_traders = direct AEGIS checks, wallet PnL/holdings for deployer/insider vett, trending = discovery feed (never entries).

### CabalSpy api-id host — ⚠️ also network-blocked from this box
- `https://api-id.cabalspy.xyz/v1/health` → **connect timeout** (148.230.111.181:443, 35s no connect). Same for `stream.cabalspy.xyz`. Docs host (`docs.cabalspy.xyz`) resolves fine — API infrastructure is blocked at network level from this datacenter IP, not a key/auth issue.
- Key `cQuH…` + endpoint map already in §7 above. Probe from user's home/VPN network.

## Quick fixes learned (setup corrections 2026-10-15)
1. **Ave.ai**: header must be `X-API-KEY` (not `Ave-Auth`), host `prod.ave-api.com/v2` — search + price + contracts verified 200.
2. **Birdeye**: key works for `defi/price` + `defi/token_overview`; `defi/token_security` needs paid package (401).
3. **Bitquery**: Solana/EVM live on **V2** `streaming.bitquery.io/graphql` with `dataset: realtime` — earlier V1 probe was wrong host. Bearer ory_at_ verified.
4. **Nansen**: header `apikey` + body `wallet_address` is correct; key has 0 credits → top up at app.nansen.ai.
5. **Dune**: auth works; `POST /sql/execute` performance="small" → 402 free-tier datapoint cap; use saved query ids or upgrade.
---

## 7. NEW PROVIDERS (audited + mapped Oct 17 2026 — six tools)

### Sol.wales — ✅ WORKS (keyed whale-flow tracker, FREE, live-verified)
Docs: https://sol.wales/api · Base: `https://sol.wales/api/v1` · Auth: `x-api-key: sw_…` (60/min per IP no key, 600/min with key; 429 + Retry-After)
- ✅ **`GET /api/v1/traders?source=discovered|verified&limit=&cursor=`** — tracked whales (promoted at ≥1000 SOL held or ≥250 SOL single move): address, label (hand-curated), tags, source, active, solLamports (STRING BigInt), sol, tokenCount, moves24h, moves7d, lastMoveAt. Verified live.
- ✅ **`GET /api/v1/traders/{address}`** — trader + 60 balance snapshots + last 20 moves (404 if untracked).
- ✅ **`GET /api/v1/traders/{address}/moves?since=&limit=`** — moves newest-first (default 50, max 200). kinds: sol_in|sol_out|token_in|token_out|swap|other; each: signature, at, mint|null, amount, counterparty, detail{pre/post deltas}.
- ✅ **`GET /api/v1/moves/recent?limit=`** — NEWEST moves across ALL active wallets (default 50, max 200) — "follow everyone" poll. Verified live.
- ✅ `GET /api/v1/health` → `{"ok":true}`. CORS open; Cache-Control 30s; sync every few min; window = 25 latest sigs per wallet (gaps for hyperactive).
- **AEGIS mapping:** whale move feed → GOOD_WALLET_SELLING early warning (tracked whale dumping a held mint), whale accumulation signal, moves24h/7d = wallet activity delta. Poll `/moves/recent` = all-wallet tape at zero cost (replaces bits of FomoAPI/WalletIntel). KEY: solLamports is a STRING (BigInt), amounts are UI-unit strings.

### CabalSpy — ⚠️ API HOST TIMEOUT from this box (docs + key VALID; connectivity blocked)
Docs: https://docs.cabalspy.xyz (llms.txt index) · Base: `https://api.cabalspy.xyz/v1` · Auth: `Authorization: Bearer cQu…` OR `api_key=` query · Demo: `demo-api.cabalspy.xyz` (20 req/IP/day, 15-min delay, ≤5 rows)
- **Free tier: 10,000 credits/mo (REST 10/req, WS event 1/credit), 5 req/s, 1 key, historical since Feb 2025.**
- `GET /v1/wallets?blockchain=solana&type=kol|smart|whale&limit=&cursor=` — 3000+ labeled wallets (name, twitter, telegram, copytrade link).
- `GET /v1/transactions/latest?blockchain=&type=` — latest tracked-wallet trades.
- `GET /v1/tokens/transactions?blockchain=solana&mint=&type=&limit=` — per-token tape; each tx has profile + **holdings_after** (token_amount, supply_pct, bag_pct, still_holding) → replay position build/unwind.
- `GET /v1/wallet/leaderboard?period=6h|1d|7d|30d&limit=` — volume-ranked: realized_pnl, win rate, avg hold min, buy/sell counts.
- `GET /v1/wallets/tracker?address=&period=` — full wallet profile + win-rate buckets + realized PnL chart + recent trades.
- `GET /v1/wallets/holdings?address=` — live on-chain balances (Moralis, no cost basis). `GET /v1/wallet/pnl_calendar` — day PnL + streaks.
- `GET /v1/tokens/{mint}/bundles` + WSS bundle stream — KOL + side-wallet coordinated buys w/ confidence + proof.
- `GET /v1/signals` + WSS signal stream — cluster entry/exit alerts (own thresholds).
- WSS `wss://stream.cabalspy.xyz?apiKey=KEY` — tx/bundle/signal streams (1 credit/event).
- 403 = invalid key/credits; 429 = rate limit. Chains: solana, bnb, base, eth, rh.
- **AEGIS mapping:** labeled KOL/SMART/WHALE roster → behavior index; per-token holdings_after tape → GOOD_WALLET_SELLING / whale-dump detection; coordination signals → confluence boost; bundle detection → insider/bundle flag (complements Rugcheck graph_insiders). Credential stored: CONFIG.md `CABALSPY_KEY`.

### MadeOnSol — ⚠️ CF-BLOCKED from this box (docs fully mapped; 95 endpoints; free tier real)
Docs: https://madeonsol.com/api-docs · Base: `https://madeonsol.com/api/v1` · Auth: `Authorization: Bearer msk_…` (x402: USDC per call $0.005–0.02)
- **Free: 200 calls/day, 10/min, full response shape, real wallets+tx. Pro €43/mo 10k/day; Ultra €131/mo 100k/day (DEX firehose, linked-wallet clustering). Demo key: `msk_demo_try_the_solana_api_2026` (3 read-only).**
- **KOL:** `/kol/feed` (real-time trades w/ mc_at_trade) · `/kol/leaderboard` (PnL/winrate/ROI/early-entry; today-180d) · `/kol/{wallet}` (+pnl_by_token) · `/kol/coordination` (3+ KOLs same token = convergence) · `/kol/tokens/{mint}` (KOL net flow per token) · `/kol/pairs` (co-trade affinity) · `/kol/{wallet}/timing` (hold dist 1h/6h/24h, avg sizes, hour-of-day) · `/kol/tokens/hot` (acceleration) · `/kol/tokens/trending` · `/kol/{wallet}/pnl` (equity curve + closed) · `/kol/tokens/{mint}/entry-order` (first buyers, seconds_after_first, percentile_pnl_7d) · `/kol/compare` · `/kol/alerts/recent` · `/kol/first-touches` (S/A/B/C scouts; top scouts → ≥3 follow-on KOLs in 4h ~50%) · `/kol/scouts/leaderboard`
- **Deployer Hunter:** `/deployer-hunter/stats|leaderboard|{wallet}|{wallet}/tokens|alerts|alert-stats|best-tokens|recent-bonds|{wallet}/trajectory|{wallet}/history` (PRO history = daily reputation time-series, NO lookahead bias; tiers elite/good/rising; bond rates; recent_outcomes string).
- **Alpha:** `/alpha/leaderboard` (1.2M+ first-20 buyers scored) · `/alpha/{wallet}` (ULTRA + bot_signals) · `/alpha/{wallet}/linked` (ULTRA same-actor co-buy clusters).
- **Token Intel:** `/tokens/{mint}/kol-consensus` (accumulating/distributing + conviction) · `/tokens/{mint}/peak-history` (ATH, drawdown) · `/tokens/{mint}/cap-table` (first 10-20 buyers enriched: hist win rate, PnL, bundle flags, realized on mint, still_holding) · `/tokens/{mint}/top-traders` (ranked by realized PnL + reputation) · `/tokens/{mint}/bundle` (same-slot cohort + **held_pct_of_supply** = insider residual) · `/tokens/{mint}/buyer-quality` (0-100: avg WR of non-bot early buyers, KOL bonus, bundle penalty, bot dominance; **dump_cluster_count** — 3+ → sub-15min peak 94% of time) · `/tokens/batch/buyer-quality` (POST 50) · `/tokens/{mint}/risk` (PRO: auditable rug-risk, factors[] w/ supply_inflation, sniper_footprint, **dev wallet_empty = dev exited**) · `/tokens/{mint}/candles` (PRO: OHLCV + net-flow + liq) · `/tokens/{mint}/flow` (PRO: unique wallets, trades_per_wallet = wash proxy) · `/tokens/{mint}/trades` (PRO tape) · `/tokens/{mint}/pools` (PRO liquidity map + top_pool_share_pct) · `/tokens/{mint}/depth` (PRO: per-pool impact, SOL to move 1/5/10%)
- `GET /signals/{name}/performance` — out-of-sample efficacy backtest per signal (lift over base) — free, empirical signal weighting.
- **AEGIS mapping (biggest new source):** `/tokens/{mint}/risk` = auditable rug-risk + DEV-EXIT read; `/buyer-quality` = new factor (dump_cluster_count); `/bundle` held_pct = insider residual; `/deployer-hunter/*` = deployer memory upgrade (bond rates/tiers/no-lookahead); `/kol/coordination` + `/kol-consensus` = confluence/smart-money conviction; `/alpha/{wallet}/linked` = sybil clustering; `/depth` = the liq-quality gate. Free covers /kol/* + /deployer-hunter/alerts + /alpha/leaderboard + buyer-quality; PRO+ unlocks risk/candles/flow/trades/pools/depth.
- ⚠️ Connectivity: `api.madeonsol.com` DOESN'T RESOLVE; `madeonsol.com/api/v1` Cloudflare-403 from this datacenter IP (curl AND browser). Docs + demo confirm the API; live probes must come from user's network or x402/MCP path. Credential: CONFIG.md `MADEONSOL_KEY`.

### WalletIntel — ✅ WORKS (free, keyless wallet PnL)
Docs: https://walletintel.dev/docs + github.com/ace8ecar-source/walletintel-v2 · Base: `https://api.walletintel.dev` · No key. Free: 10 req/day, max_tx 1000→5000, 24h cache, 3 concurrent scans.
- ✅ **`GET /wallet/{address}/pnl`** — FIFO PnL per token: total_realized_pnl_sol, win_rate, strategy (sniper/scalper/smart_money/diamond_hands/degen), score 0-100, unique_tokens, tokens[] per-mint pnl.
- ✅ **`GET /wallet/{address}/summary`** — quick; server-busy when >3 concurrent (observed) → retry.
- ✅ **`GET /leaderboard?sort=score|pnl|win_rate|popular&limit=`** — verified live (score 95-96, win_rate 92-100, pnl_sol, strategy, trades, unique_tokens).
- ✅ `GET /health` (verified: 2 healthy providers, cache 90.9% hit), `GET /stats` (verified: alchemy-free + solana-public RPC pool).
- **AEGIS mapping:** strategy tag + score for deployer/insider/first-buyer wallets → behavior index; per-token PnL of cap-table buyers = quality filter; leaderboard = cohort discovery. 10 req/day → cache 24h, use only for high-value wallets. PnL in SOL.

### Solscan — ⚠️ FREE=chaininfo only; PRO needs higher key tier (JWT valid, paid-gated)
Docs: https://pro-api.solscan.io/pro-api-docs/v2.0 · Public: `https://public-api.solscan.io` · Pro: `https://pro-api.solscan.io/v2.0` · Auth: `token: <JWT>` header (NOT Bearer)
- ✅ **`GET /chaininfo`** (public, FREE, verified live): blockHeight, currentEpoch, absoluteSlot, transactionCount.
- ❌ Pro v2.0 (token/holders, token/meta, monitor/usage, account/*, market/*) → **401 "Please upgrade your api key level"** — JWT valid but free key tier; paid upgrade required.
- Pro surface (documented): Account detail/transfer/defi/balance/transactions/portfolio/token-accounts/stake/metadata/funded-by/leaderboard/funding · Token transfer/defi/markets/meta/price/holders/list/trending/latest/search/statistic/OHLCV · NFT · Transaction last/detail/decoded/fees · Block · Market list/info/volume/positions/OHLCV · Program · Monitor usage.
- **AEGIS mapping (after upgrade):** token holders (T10 concentration backup), token markets, token price OHLCV, account portfolio/funded-by (mother-wallet/funder), transaction decoded (behavior labels). For now: chaininfo only. Credential: CONFIG.md `SOLSCAN_JWT`.

### Blixvip — ⚠️ LOGIN-GATED (no public key; endpoints known)
Docs: https://blixvip.com/app#/api · Base: `https://blixvip.com/api`
- `GET /api/overview` — market snapshot (verified: "Sign in at blixvip.com to use Blix" without auth).
- `GET /api/stream` — every event as it happens (SSE/NDJSON market stream).
- App is Next.js SPA; `/app` → `/start` login wall. No anonymous API key endpoint; needs Blix account/credentials.
- **AEGIS mapping:** market-regime context once auth exists; not wired until credentials supplied.

## PUMPDATA (sol.shrine.trade) — LIVE PROBED Oct 20 2026, FREE keyless
Docs: https://pumpdatamonorepository.pages.dev/pump/llms-full.txt (full 3,360-line spec)
Base: https://sol.shrine.trade  · Replay: https://replay.shrine.trade/pump/{YYYY/MM/DD/HH}.jsonl.zst (hourly zstd, index.json lists hours; 848 hours Sep14→Oct20 present, incl. 2026/10/10 and 2026/10/18-19)
- GET /sol-price → {priceUSD, time} — live SOL/USD (verified $104.24)
- GET /metadata?mint=<mint> — name/symbol/decimals/supply/launchpad/quote, active pool after migrations, graduated_at, mint_authority/freeze_authority, pumpfun curve, creator (verified: Odyssey full metadata)
- WS socket.io at https://sol.shrine.trade (python-socketio installed in desk venv):
  * subscribe {pool} → price ticks {pool, price, priceUSD, mcap, time} (verified)
  * subscribe_stream {protocols:[PUMPFUN,PUMPSWAP,...], sinceSlot} → full trades {signature, block, timestamp, action, protocol, mint, pool, price, tokenAmount, quoteAmount, tokensInPool, quoteInPool, tradersInvolved, breakdown[]} (verified live; carries pool reserves for constant-product sizing)
  * subscribe_trades {pool}, subscribe_new_tokens, subscribe_migrations, subscribe_sol_price — free keyless
  * subscribe_ohlcv / ohlcv_history — 1s candles, need FREE key from Telegram group t.me/+nEqAowTK8BZhZjFk
- REST cap 3 req/s/IP/endpoint; WS 50 subs/conn, one open stream per type per IP; sinceSlot resume (24h replay); dedupe on signature+pool
- Trading (NOT needed for paper): POST /api/lightspeed (0.25%, needs ls_ key), /api/local-trade, /api/send — real execution, never used by paper desk
- Verdict: replaces DexScreener/Gecko fragility for paper mark-to-market (reserves in stream = rule-3 constant-product sizing), independent backtest tape (cross-validate pumpapi.io numbers), free. NOT a signal source (curated Telegram only).

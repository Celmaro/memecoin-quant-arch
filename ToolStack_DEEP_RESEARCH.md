# 🔬 TOOLSTACK DEEP RESEARCH — FINAL CONSOLIDATED (Oct 18 2026)
> Same methodology as the Blix dive: docs fetched per provider, full endpoint surfaces enumerated,
> "already used" vs "new edge" per provider, integration combos, priorities. 9 clusters.
> Sources: 4 subagent doc-fetches (RPC, forensics, wallet-intel, KOL) + 5 in-parent fetches (market, ave/gmgn, jupiter, pump, locked). All live-probed except where marked.

---

## ⭐ TOP 15 NEW CAPABILITIES (ranked by edge-per-effort for a quick-flip desk)

| # | Capability | Provider | Effort | Why it matters |
|---|---|---|---|---|
| 1 | **GoPlus address_security (deployer/first-buyer vetting)** | GoPlus (free) | Low | honeypot_related_address + number_of_malicious_contracts_created + phishing/mixer/sanctioned flags → AEGIS deployer memory gets a security tier Rugcheck doesn't have |
| 2 | **GoPlus pre_execution simulate (sell-capability gate)** | GoPlus (free) | Low | simulate the EXACT exit swap before sending → catches dynamic honeypots/transfer-hook sell-blocks; a paper desk that can never be trapped |
| 3 | **Helius Wallet API funded_by + batch identity** | Helius (100cr/call) | Low | who funded the deployer; 32,500+ tagged accounts; first-buyer bot/CEX detection |
| 4 | **Shyft failed-tx tape (sell-block detector)** | Shyft (free REST) | Low | getTransactionsForAddress status=failed → proves a token is currently unsellable mid-hold; unique signal no other check produces |
| 5 | **FomoAPI /v2/token/{address}/devs + stats** | FomoAPI (250cr) | Low | dev realized-PnL trend (closing bag = exit bias) + top10HoldersPercent + 5m/1h/4h/24h buy/sell flow + unique buyers/sellers — replaces dead CoinStats, complements Rugcheck |
| 6 | **SolanaTracker /trades/whales + /trades/kol** | SolanaTracker (credits) | Low | real-time smart-money radar with KOL identity names; whale-minVolume filters |
| 7 | **Ave /v2/tokens/top100/{id} (per-holder PnL + funding attribution)** | Ave (wired key) | Medium | realized/unrealized PnL per top-100 holder + token/sol_first_transfer_in_from → funding-wallet attribution, GOOD_WALLET_SELLING at holder level |
| 8 | **GMGN Callout OpenAPI (/global KOL callout stream)** | GMGN (needs partner AK/SK) | Med | KOL callout firehose: call_wallet/call_token/call_mc/multiplier/holding_percentage/is_kol → confluence + coordination; apply via Google Form |
| 9 | **Rugcheck /v1/tokens/{id}/insiders/graph + lockers + stats/new_tokens** | Rugcheck (free) | Low | net_id persists across tokens → serial insider networks; locker unlockDate (unlock-dump timing); keyless new-token feed w/ authorities |
| 10 | **PumpDev WS subscribeAccountTrade (per-wallet ms tape)** | PumpDev (free WS) | Low | watch deployer/whale wallets at ms latency → whale-dump exit signal better than polling |
| 11 | **DexScreener /metas/trending (narrative baskets)** | DexScreener (free) | Low | free narrative-level trending (m5–h24 MC deltas) → cross-check Blix narratives |
| 12 | **DexPaprika SSE reserve stream (liq-pull push)** | DexPaprika (free) | Med | real-time pool reserve updates → liq-pull detection that isn't a snapshot |
| 13 | **CabalSpy /v1/bundle + /v1/signals + /v1/wallets/connections** | CabalSpy (10k cr/mo) | Med | KOL+side-wallet same-block bundle proof, cluster signals w/ backtest history, shared-token de-anonymization graph |
| 14 | **MadeOnSol buyer-quality batch + /signals/{name}/performance + depth** | MadeOnSol (200/day free) | Med | dump_cluster_count (94% OOS dump predictor), empirical signal lift, per-pool to_move_price |
| 15 | **Jupiter Swap V2 /order (managed execution)** | Jupiter (free key) | Med | the go-live building block: quote→order→sign→confirm→reconcile; simpler than swap-instructions |

---

## CLUSTER 1 — RPC & SECURITY (Helius / GoPlus / Shyft / FluxRPC) [subagent report complete]
### Helius — already: RPC authorities, DAS token accounts, v0 transactions, holder churn
- **NEW:** Wallet API (`api.helius.xyz/v1/wallet/{w}/funded_by|balance_at|history|identity|transfers` — 100cr/call, 32,500+ tagged); Parsed Events REST (`/v1/parsed-events/transactions`) IDL-decoded first-buy parsing; DAS getAssetsByCreator (repeat-deployer inventory); Webhooks (500+ tx types, 200-500ms); LaserStream WSS transactionSubscribe accountInclude=mint.
### GoPlus — already: token_security flags (check 13)
- **NEW:** `address_security/{address}?chain_id=solana` (honeypot_related_address, malicious-contracts count, phishing, mixer, sanctioned); `pre_execution` POST (simulate tx → asset_changes, risky_address, sell-capability proof); token_security dex[] burn_percent/lp/tvl + holders[].is_locked/Burn tags.
### Shyft — already: getTokenSupply only (rest 403)
- **NEW (REST free-tier, zero index cost):** wallet/all_tokens + token_balance (Token-2022 aware, isFrozen); transaction/parse_selected (human-readable batch parsing); **getTransactionsForAddress status=failed** (sell-block tape); defi.shyft.to pools/get_by_token (all pools per mint, fee rates).
### FluxRPC — already: Rugcheck auth, RPC fallback
- **NEW:** Yellowstone gRPC (account/transaction/entry streams w/ memcmp filters) on the same key; **binary getProgramAccounts (base64)** — near-zero-cost full holder-set polling; predictable $0.06/GB pricing vs credit math.

## CLUSTER 2 — MARKET DATA (DexScreener / GeckoTerminal / DexPaprika / Birdeye / CoinStats) [in-parent]
- **DexScreener NEW:** `/metas/trending` (narrative baskets w/ 5m-24h deltas — free); token-profiles/latest + recent-updates (attention proxy); community-takeovers (risk event); ads/latest (hype proxy).
- **GeckoTerminal NEW:** trending_pools + new_pools (discovery context); per-token pools list (liquidity fragmentation).
- **DexPaprika NEW:** **SSE reserve stream (free, ≤25 subs)** — push liq-pull; MCP (free no-key); batched prices ≤10.
- **Birdeye NEW:** v3/ohlcv 1s-30s (redundant w/ Blix 5s); WS large-trade/wallet subs (if keyless works).
- **CoinStats:** DROP — redundant (Blix + DexScreener + Helius cover everything).

## CLUSTER 3 — AVE.AI + GMGN [in-parent]
- **Ave NEW:** `/v2/tokens/top100/{id}` (per-holder realized/unrealized PnL, funding attribution via token/sol_first_transfer_in_from — goldmine); `/v2/tokens/{id}` (5m/1h/4h buy/sell volume split — imbalance w/o Bitquery); `/v2/klines/token` (1-min candles); `/v2/ranks/topics` boards; `/v2/txs/{pair}` tape.
- **GMGN NEW:** **Callout OpenAPI** (papi.gmgn.ai, AK/SK HMAC, needs partner approval): /global KOL callout stream w/ call_mc, multiplier, holding_percentage, is_kol → confluence/coordination. Agent API (openapi) still CF-blocked; agent_score, top_holders/traders, wallet pnl/holdings after unblock.

## CLUSTER 4 — JUPITER [in-parent + live probe]
- **KEY: Swap API V2 is recommended** — `/swap/v2/quote` verified live; **`/swap/v2/order` = managed execution** (quote→order→sign→confirm→reconcile) = the go-live building block.
- price/v2 + tokens = 404 from this env (host blocked); perps/LO/DCA/lend endpoints documented for future.
- Follow-up: swap jupiter_estimator to v2/quote (drop-in); keep exit_intel fraction handling.

## CLUSTER 5 — PUMP.FUN (PumpAPI / PumpDev) [in-parent]
- PumpAPI: WS free (redundant w/ Blix firehose); replay 403 today; trade API 0.25% lightning (future executor alt).
- **PumpDev NEW: subscribeAccountTrade WS (watch specific deployer/whale wallets at ms)** — the highest-value wiring; subscribeTokenTrade; bundle-lightning (future atomic exits).

## CLUSTER 6 — FORENSICS (Rugcheck / FomoAPI / Bitquery) [subagent report complete]
- **Rugcheck NEW:** `/v1/tokens/{id}/insiders/graph` (net_id persists → serial insider networks, network_type trade|transfer); `/v1/tokens/{id}/lockers` (per-locker unlockDate → unlock-dump timing; totalUSDC/2 = true lock value); `/v1/stats/new_tokens|recent|trending` (keyless launch feed + crowd attention); bulk report (≤255, paid).
- **FomoAPI NEW (the 402 = credits_exhausted, NOT endpoint gated):** `/v2/token/{address}/devs` (dev realized PnL + thesis → dev-exit rug signal); `/v2/token/{address}/stats` (holders, top10HoldersPercent, 5m-24h buy/sell/unique); thesis endpoint (threshold filter); `/ws/alerts?trader=` (GOOD_WALLET_SELLING early warning, accepts 15s free delay); graduated board.
- **Bitquery (402 = plan-wide points exhaustion, token must regenerate after top-up):** Trading cube Trades (MEV-filtered, PriceInUsd + Supply.MarketCap per row) — when active; pump.fun cube (graduation, bonding-curve); realtime windows: DEXTrades ~12h, DEXTradeByTokens ~7d, Trading ~30d; archive needs add-on. 402 discipline: never auto-retry (billing state).

## CLUSTER 7 — WALLET/WHALE INTEL (SolanaTracker / WalletIntel / Solscan / Sol.wales) [subagent report complete]
- **SolanaTracker NEW (free 10k req/mo):** `/trades/whales` (minVolume 1000-10000 + KOL identity) + `/trades/kol`; `/tokens/{token}/holders?enrich=walletPnl,identity`; `/tokens/{token}/bundlers`; PnL V2 `/v2/pnl/wallets/{w}/risk|highlights|performance|calendar|first-buyers`; batch wallet-summaries (100/req); KOL leaderboard + calendar; Lighthouse memecoin market stats; top-performers/trending/graduating.
- **WalletIntel NEW:** per-token avg_buy/sell_price + hold_time_seconds + is_closed (fill-level); strategy_details.pumpfun_ratio (serial-flipper classifier); score_breakdown.risk; force_refresh. 10/day budget = weekly batch job.
- **Solscan (paid tiers Lite $49+):** account/leaderboard (holdings whales by value), account/portfolio, token/holders amount+value filters, token/trending+latest, **transaction/actions (decoded, 50+ protocols)**, market/info (pool creator!), **account/funded_by (L2+)**, MCP at mcp.solscan.io. Free = chaininfo only.
- **Sol.wales NEW:** /moves/recent (global feed — strictly better than paging); 60-point snapshots (accumulation/distribution reconstruction); /**moves?since=** ISO/unix (retrospective windows); detail deltas (exact pre/post SOL+token per tx); retirement signals; auto:balance vs auto:move tags.

## CLUSTER 8 — KOL INTELLIGENCE (CabalSpy / MadeOnSol) [subagent report complete]
- **CabalSpy (10k cr/mo, REST 10cr, WS 1cr/event):** /v1/wallets list+lookup+leaderboard+tracker+holdings+pnl_calendar+history+**connections**; /v1/tokens/holders+transactions (holdings_after)+stats; **/v1/bundle** (same-block KOL+side wallets, proof fields, per-wallet live position w/ entry_mc); **/v1/signals** (+ /signals/history would_have_fired backtest); WSS stream.cabalspy.xyz (transactions/count/signal/bundle/holder/balance, 1cr/event). CabalSpy ID (api-id): theses suite (position-verified bull/bear w/ live PnL) — needs product toggle.
- **MadeOnSol (200 calls/day free, 5-min delay on live feeds; PRO €43):** /kol/feed (rich: kol winrate/percentile/deployer block), /kol/first-touches (**median 12s lead to 2nd KOL; S scouts ~50% follow-on in 4h**), /kol/coordination (3+ KOLs, time_to_consensus, MC change), /kol/tokens/{mint}/entry-order (seconds_after_first), deployer-hunter suite (**/alerts free w/ deployer_sol_balance + KOL cross-ref; /history PRO = no-lookahead daily reputation**), alpha leaderboard + linked (sybil), token buyer-quality (**dump_cluster_count**), bundle (held_pct_of_supply), risk (PRO factors[] + dev wallet_empty), depth (PRO to_move_price), **/signals/{name}/performance (OOS lift, any tier)**.
- **Blix complement analysis:** MadeOnSol supersets Blix on deployer/early-buyer/risk/depth; CabalSpy supersets on labeled wallet breadth + streams + SocialFi theses; modest overlap (KOL clusters) = cross-validation.
- **Home-network unblock probe list** (9 curl/WS commands) saved in subagent output.

## CLUSTER 9 — LOCKED (Nansen / Dune / CoinStats) [in-parent]
- **Nansen** (credits: smart-money 5, labels 100-500, agent 200-750): Smart Money netflows/holdings/dex-trades = the whale-coordination data we lack; labels expensive (cache); Trade API (spot/perp quote→prepare→execute) exists.
- **Dune** (API requires Plus plan — free is view-only; 70/200 rpm): arbitrary SQL on solana tables = sybil/wash SQL — the only scale custom-SQL provider.
- **CoinStats:** redundant, drop.
- **Verdict: pay for NONE today.** If live later: Dune Plus is the best learning compound; Nansen only if smart-money netflows prove predictive in our paper record.

---

## RECOMMENDED WIRING QUEUE (after this audit)
1. **GoPlus address_security + pre_execution** → AEGIS deployer tier + exit sell-capability gate (free, both).
2. **Shyft failed-tx tape** → position_monitor sell-block detector (free REST).
3. **Ave top100 holder PnL + funding attribution** → wallet_vettor + AEGIS smart-holder factor (key wired).
4. **PumpDev subscribeAccountTrade WS** → real-time deployer/whale watch (free WS).
5. **DexScreener metas/trending** → narrative cross-check vs Blix (free).
6. **SolanaTracker /trades/kol + whales** → KOL behavior feed within existing credits.
7. **FomoAPI devs+stats** → dev-exit + flow divergence (when credits renew).
8. **Rugcheck insiders/graph + lockers** → serial-insider + unlock-dump checks (free).
9. **Jupiter v2/quote** → jupiter_estimator upgrade (drop-in).
10. **DexPaprika SSE reserve** → liq-pull push monitor (free).
11. **GMGN Callout /global** → KOL confluence feed (user applies for AK/SK + home-network unblock).
12. **CabalSpy + MadeOnSol wire** → after home-network probes confirm reachability.
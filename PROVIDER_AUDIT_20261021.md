# FULL PROVIDER AUDIT + SIMPLIFICATION (Oct 21 2026)
# Objective: budgetize, prioritize stable providers, free/cheap first, paid only
# for what free can't do, simplify. Every provider LIVE-PROBED from this box with
# real keys + real code paths. Also fixed a latent http_json(retries=0) bug found
# during the audit (it crashed with TypeError on EVERY retries=0 call — masked
# provider status; returns (None,{}) now and retries=0 still makes 1 attempt).

## TIER 1 — STABLE + FREE (keep, these are the spine)
| Provider | Use | Probe | Latency | Cost |
|---|---|---|---|---|
| **mainnet-beta public RPC** | rpc() primary (getTokenSupply/AccountInfo/Sigs/Tx) | ✅ OK | 19-543ms | FREE |
| **PumpData WS (sol.shrine.trade)** | feed-first market source (px/liq/mc/b5/s5/vol) | ✅ alive 11K ev/min | ~0ms cache | FREE keyless |
| **GeckoTerminal** | OHLCV (now PRIMARY), token pools fallback | ✅ OK | 50-645ms | FREE 30/min |
| **DexScreener** | dex_pair market fallback, SOL price fallback | ✅ OK | 28ms-2s | FREE 300/min |
| **Jupiter quote** | fill-to-fill paper pricing | ✅ OK | 26ms-1.8s | FREE |
| **GoPlus** | token security (mintable/freeze/tax) | ✅ OK | 78-737ms | FREE keyless |
| **rugcheck** | AEGIS contract report | ✅ OK | 655ms-2s | FREE (flux key) |
| **sol.wales** | whale/trader tape (whale_watch) | ✅ OK (health/traders/moves) | 539-1711ms | FREE (sw_ key) |
| **Blix (localhost:4420)** | local radar (holders/candles/narratives) | ✅ OK but 15 issues | 5ms | FREE self-host |
| **Fomo-API (api.fomo-api.com, NEW)** | cohort leaderboard source (realized PnL board) | ✅ OK | 1.1-3s | FREE 1,500/mo |
| **Telegram MTProto (telethon)** | curated call intake | ✅ OK | push | FREE |
| **replay.pumpapi.io / replay.shrine.trade** | tape archives (backtest) | ✅ (zstd) | bulk | FREE |

## TIER 2 — FREE BUT DEGRADED (keep only if it adds signal; all fail-closed)
| Provider | Use | Probe | Verdict |
|---|---|---|---|
| **Helius (mainnet.helius-rpc.com)** | DAS getTokenAccounts, enhanced txs, wallet identity | ❌ **429 quota EXHAUSTED** | keep ONLY for DAS/enhanced (public lacks them); must stay breaker-gated |
| **walletintel** | deployer summaries | ✅ OK 1.9s | slow; budgeted 10/day — keep as enrichment |
| **solanatracker** | leaderboard/wallet summaries (cohort fallback) | ✅ OK 731 cr | keep only as fallback (fomo-live is primary now) |
| **CoinStats** | wallet balance | ❌ 406 credit-locked | REST-only free 20K cr if new account; not WS — skip |
| **Bitquery** | USD buy/sell imbalance | ❌ 402 credit-locked | WS possible with fresh free key — needs user signup |
| **solscan** | chaininfo | ✅ OK 2.5s (dead 404 on public-api) | replace with public RPC getHealth — drop |
| **shyft** | failed-tx tape | ✅ 300ms (WS 500 on free) | REST ok, WS not free — keep REST only |

## TIER 3 — DEAD/BLOCKED FROM THIS BOX (do NOT spend time; fail-closed proven)
| Provider | Status | Reason |
|---|---|---|
| **DexPaprika** | ❌ 402 paywalled (OHLCV) / SSE 403 keyless | REST now paid; SSE keyless = 35 showcase only. FREE key exists — only wire if user signs up |
| **FomoAPI legacy (api.fomoapi.io)** | ❌ 402 + 401 | dead product; NEW fomo-api.com replaces it |
| **ave-api** | ❌ 400/404 | wrong endpoint guess; keyed but not critical (GoPlus+rugcheck cover security) |
| **gmgn (gmgn.ai)** | ❌ 403 Cloudflare | network-block; WS host DNS-blocked. Skip |
| **cabalspy** | ❌ network unreachable (14s) | has free WS but block is network-level from this VM |
| **madeonsol** | ❌ 403 | browser-blocked; 200/day free IF reachable from user network |
| **Birdeye** | ❌ 401 (key dead?) | WS paid $199+/mo anyway — skip |
| **publicnode** | HTTP 403, WSS handshake OK but stream flaky | not feed-grade; keep out of rpc() |

## TIER 4 — RESEARCH FINDINGS (free alternatives, needs user signup; from FREE_WS_RESEARCH_20261021.md)
| Provider | Free path | What it replaces |
|---|---|---|
| **DexPaprika SSE + free key** (console.dexpaprika.com) | prices + pool_reserves live stream | second live tape + live liq-collapse detection |
| **Codex.io ($1 one-time)** | GraphQL WS price/trade/chart subs | GeckoTerminal polling |
| **Chainstack free** | 3M reqs/mo + WS, load-balanced | mainnet-beta handy but flaky |
| **Nansen free** (100 cr + 10/day) | smart-money REST overlay | wallet smart-money signal |
| **Bitquery fresh key** | GraphQL WS subscriptions | 3-min imbalance polling |

## BUDGET DECISIONS (paid/free tradeoffs — free-first)
1. **rpc() = public-first** (done): mainnet-beta primary, Helius fallback → Helius credits spent ONLY on DAS/enhanced-tx that public lacks. Keeps 1M cr/mo Helius budget for the ~6 DAS + wallet calls per deep scan.
2. **cohort = fomo-live primary** (done): SolanaTracker (731 cr) now fallback-only → saves its credits.
3. **OHLCV = GeckoTerminal first** (done): DexPaprika 402-paywalled no longer on the hot path.
4. **Verdict: NOTHING ELSE to budget** — all remaining paid/gated providers in Tier 2/3 are either fail-closed enrichment (cheap) or dead (zero spend). The desk currently spends effectively $0/mo beyond Helius free quota + keys the user already owns.

## SIMPLIFICATION PLAN (less code — the real ask)
| Action | What | Why |
|---|---|---|
| 1. **Remove solscan** (solscan_chaininfo + /chaininfo callers) | ~20 lines | public RPC getHealth does it; public-api is 404 anyway |
| 2. **Remove legacy FomoAPI dead code** (fomo_holders/fomo_user/fomo_board + FOMO_KEY) | ~60 lines + breaker | none of the callers can succeed (402/401); new fomo-live covers cohort |
| 3. **Remove madeonsol** (3 sites) | ~30 lines | 403 from box, browser-block family; enrichment dust |
| 4. **Remove CoinStats balance** (1 site) | ~15 lines | 406 credit-locked since OCT 16, breaker no-op; not used in hot path |
| 5. **Remove shyft failed-tx** (if callers tolerate None)** | ~20 lines | free REST works but adds no signal (probe: always []); can keep as enrichment |
| 6. **Remove publicnode from docs/notes** | trivial | 403 HTTP + flaky WSS — dead weight, confuses future audits |
| 7. Keep: Helius (DAS-only), gmgn/cabalspy madeonsol dead refs documented in ToolStacks as "needs user-network probe" | doc | don't delete research value |
Estimated: **~145 lines removed** from desk_common (1,598 → ~1,450) + associated imports. The remaining providers then form a tight free-first spine (Tier 1 = 11 providers, all free).

## STABILITY ORDER (which provider to trust for what)
1. Feed/market: PumpData WS cache (0s, 11K ev/min) → DexScreener fallback (28ms) → GeckoTerminal (50ms)
2. RPC: mainnet-beta (19ms) → Helius (DAS-only, breaker-gated)
3. OHLCV: GeckoTerminal (50ms) → DexPaprika (only if user gets key)
4. Leaderboard/cohort: Fomo-API (1.1s) → SolanaTracker fallback (731 cr)
5. Security: GoPlus (78ms) + rugcheck (655ms) + Ave IF key valid
6. Whale tape: sol.wales (539ms) — keep
Recommendation: do NOT add any Tier-4 provider until Tier 1 proves unstable or a specific gap appears.

## LIVE VERIFIED POST-AUDIT
- http_json(retries=0) bug FIXED (was crashing with TypeError on every call; now makes 1 attempt + returns (None,{}) on failure)
- Regression: run post-fix to confirm (audit_regression_test.py)
- Desk currently: supervisor 2125551 + telethon + pumpdata (feed 11K ev/min, book consistent)
- All compile-verified files: desk_common.py, signals.py, jupiter_estimator.py, bot/position_manager/scaleout.py, bot/intelligence/cohort.py, paper_tick.py, bot/audit/audit_regression_test.py
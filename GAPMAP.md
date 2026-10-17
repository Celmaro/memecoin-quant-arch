# 🗺 GAPMAP — Metric/Data Gap → Tool-endpoint Mappings (verified 2026-10-15)

Goal: close the desk's metric gaps using endpoints we ALREADY hold keys for.
Status: ✅ READY (verified live) · ⚠️ BLOCKED (quota/credits/plan) · 🔬 NEEDS TEST

> ⚠️ QUOTA STATUS (as of audit): **Bitquery + FomoAPI free tiers are 402-exhausted** (burned during testing).
> Degradations: imbalance → DexScreener txns proxy; smart-money → FomoAPI board fallback + leaderboard (works on warm cache); wallet provenance → Helius asc (unaffected).
> Top-up at account.bitquery.io / fomoapi.io/pricing, or wait for monthly reset.

---

## A. Holder structure & concentration — the big win ✅

| Metric | Endpoint (verified) | Compute | Status |
|---|---|---|---|
| Top-1 / Top-10 / Top-20 share | Rugcheck full `/report?api_key=<flux>` → `topHolders[].pct` | sort desc, cumsum | ✅ top1=28.9, top10=79.6, top20=90.6 on CONDOM |
| HHI (Herfindahl) | same topHolders | Σ pct² | ✅ HHI=1524 (top-20; >2500 = whale-y) |
| Pseudo-Gini | same topHolders (top-N only → lower bound) | Gini on partial distribution | ✅ implementable, top-20 bound |
| Total holders | Rugcheck `totalHolders` | snapshot | ✅ 6922 |
| LP lock / burn | Rugcheck `lockers[]`, `lpLockedPct` + Ave `/v2/contracts` lock_percent | join | ✅ lockers=0 → unlocked LP (flag) |
| Bundled snipers % | Rugcheck `risks[]` (bundle/sniper) + `insiderNetworks` + our `first_buyers` | flag count | ✅ wired in AEGIS |

## B. Momentum & flow

| Metric | Endpoint | Status | Notes |
|---|---|---|---|
| Buy/sell imbalance (USD-weighted) | Bitquery V2 `Solana.DEXTradeByTokens` `Trade.Side.Type + AmountInUSD` | ⚠️ quota (was ✅) | **degrade:** DexScreener `txns.m5.buys/sells` proxy (already in monitor) |
| Smart-money net flow | FomoAPI `/v2/alerts` (whale buy/sell/thesis), `/v2/notifications` (LARGE_BUY/LARGE_SELL) | ⚠️ quota (was ✅) | **degrade:** `/v2/leaderboard/tokens/trending` + token boards (still warm-cached) |
| New wallet growth (holder momentum) | Bitquery DEXTradeByTokens bucketed `Block.Time` + distinct `Trade.Buy.Account.Address` | ⚠️ needs quota | when quota returns: count distinct buyers per 15m → growth rate |
| New-wallet buying power | Bitquery same, sum `AmountInUSD` per distinct new buyer | ⚠️ needs quota | |
| Wallet provenance / mother wallet | Helius `v0/addresses/{w}/transactions?sortOrder=asc` | ✅ unaffected | already built (`mother_wallet`) |
| Whale tracker (post-buy) | PumpDev WS `subscribeAccountTrade` + Birdeye WS `SUBSCRIBE_LARGE_TRADE_TXS` | ✅ wired (25-sub cap) | |
| DEX→CEX migration | CoinStats `/v1/markets?coinId=` — **406 with these params**; Bitquery Trading cube (quota) | 🔬 needs fixing / quota | CEX presence rarely exists for $15K memes; low priority |
| Wash-trade interval stats (CV<0.3, autocorr) | PumpAPI replay archives → trade timestamps | ✅ tool exists (`replay_backfill.py` + zstd venv) | compute per-token: interval CV, autocorrelation ρ1, zero-risk position ~96% detect |

## C. Social & sentiment

| Metric | Endpoint | Status | Notes |
|---|---|---|---|
| Mention volume (X/TG) | ❌ NOT in stack (no social mention API among our keys) | gap | could add free X/TG search or FomoAPI theses as narrative proxy |
| KOL co-occurrence | FomoAPI `/v2/users/{handle}` verified (pnl/followers/verified) + leaderboard topTokens | ⚠️ quota for fresh resolves; cached actors reuse | auto-tags kol/smart_wallet → strategy (already built) |
| Narrative proxy | FomoAPI thesis (1,250cr), Ave `/v2/ranks/topics` (hot/meme/ai...) | ⚠️/✅ | Ave ranks free + working → narrative board |

## D. Risk & execution math

| Metric | Endpoint | Status |
|---|---|---|
| Realized vol (1h/1d) | DexPaprika pool `/ohlcv?interval=1h` (close field) | ✅ built → fragility |
| Max drawdown | same candles | ✅ built |
| Slippage at $20 | Jupiter `api.jup.ag/swap/v1/quote` (Bearer) | ✅ built |
| Liquidity-to-MC ratio | DexScreener | ✅ built |

## E. Channel / call quality (already built)

| Metric | Endpoint | Status |
|---|---|---|
| Call-vs-live MC delta | crawler mc_delta_pct | ✅ per-channel stats live |
| Slow-grad fill time | Helius asc + pairCreatedAt | ✅ |
| Stale-call flag | live MC < 50% of call MC | ✅ |

---

## Recommended quick wins (cheap, no new keys)
1. **Holder concentration suite** — add `holder_concentration()` to signals.py (top1/10/20, HHI, pseudo-Gini) and surface in `card`/`intel`. Rugcheck is FREE via flux key. ✅ adding now.
2. **Wash-trade interval detector** — replay_backfill → per-hour CV/autocorr on trade timestamps (research: CV<0.3 + ρ1>0.9 = self-trading). zstd in venv.
3. **Fragility upgrade** — feed HHI + top1 into `fragility_score` (ME2F whale-dominance pillar was missing).
4. **Quota discipline** — FomoAPI `fomo_user` resolves (2,500cr) already cached in actors; add same guard for Bitquery calls (cap N per crawl) so free tier lasts.

## Not covered by our stack (need new tool if user wants)
- X/TG/TikTok mention volume + sentiment NLP (no social API key)
- True social→price lead-time stats (needs the above)
- DEX→CEX volume ratio at scale (needs exchange-wallet data; CoinStats 406 / Bitquery quota)
- Nansen smart-money labels (0 credits), Dune sybil SQL (datapoint cap) — documented, gated
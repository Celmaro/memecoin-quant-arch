# Nought (Solana memecoin terminal) — source audit vs AEGIS desk stack

Analysis method: direct source inspection of clone at `/home/agentuser/memecoin-quant/nought` (subagent's REA MCP access was unavailable in its session — findings are static-source-verified, not REA-evidence-tagged).

## What nought is
Browser-based (client-side) Solana memecoin trading terminal. Pure JavaScript, no build step, no backend: `index.html` + `src/` modules (~3.4MB). Talks directly to public/free data planes; trades via user-supplied wallets (local keypair or Jupiter swap API). No API keys embedded.

## Data planes (free/public only)
- **Jupiter** — lite-api/api.jup.ag: token list, price v3 (batch ≤50 ids), swap, ultra, trigger orders. **datapi.jup.ag** (auth'd data API): gems, txs (30/page), **top-100 holders**, charts v2; rate-limited queue 4 rps / 80ms gap (src/core/jup.js:53,65-77).
- **Dexscreener** api — token info, paid/boosts status, /orders (paid-check).
- **PumpPortal wss** — free tiers only: `subscribeNewToken` + `subscribeMigration` (reconnect backoff 1s→30s) (src/core/stream.js).
- **Raydium** launch list; **GeckoTerminal** locked-LP lane (fallback); **Hyperliquid** (perp side).
- **3 public Solana RPCs** (publicnode primary, solanatracker, solanavibestation), ws logSubscribe for wallet feeds.

## Audit / security features (the part that matters vs AEGIS)
- **Audit tile thresholds are user-editable**, persisted in localStorage `audit.thresholds` (src/pages/token/audit.js:15-23): top10 amber>20%/red>35%; dev 3%/10%; snipers 5%/15%; insiders 5%/15%; clusters 5%/15%; holders good>300/amber≤300/red≤100; organic good>50/amber≤50/red≤25. `grade()` audit.js:32.
- **Dev memory**: pump.fun dev = `PP traderPublicKey`; `devSold` flag on stream; `devBuySol` tracked (src/core/stream.js:49,52,110). Dev Tokens tab uses `audit.devMints`/`devMigrations` + relay `createdBy()` (src/core/kols.js:67-74, newest 50 launches). Blacklist by dev wallet/mint/keyword/X-handle (src/ui/blacklist.js).
- **Holder forensics**: Jupiter holder data gives `fundingAddress`/`fundingAmount`/`fundingTx` per top-100 holder (src/core/jup.js:475); tags `sniper|insider|bundler|bot` (jup.js:436,473). Cluster detection = top-100 holders sharing a funding address, ≥2 holders (src/pages/token/audit.js:62-73). **Bundles** = ≥4 distinct wallets buying within the same second (audit.js:74-81). `zeroIfAudited` (src/ui/pulse-filters.js:20): sniper/insider treated as 0 (not missing) when devPct is set.
- **KOL tracking**: holders matched against `data/kol-wallets.json` (2000-entry cap, 300s cache, src/core/kols.js:21-35); KOL attribution never guessed, pump.fun profile fallback via metadata relay with URL allowlist (kols.js:2,38-40).
- **Authority checks**: mint/freeze authority revoked=good/active=bad (audit.js:122-128); renders Jupiter Shield warnings with severity mapping (audit.js:129-140).
- **LP locking**: GeckoTerminal `locked_liquidity_percentage`, good≥95%, warn≥50%, bad<50%; 10-min per-pool cache; skipped while on-curve and <300s after migration (audit.js:53-60,95-98,177-179).
- **Dex Paid**: Dexscreener `/orders/v1/solana/{mint}` — paid = any order status 'approved' (audit.js:36,99,111 + src/core/jup.js:527-531), 1 rps.

## Trade safety (pre-sign gating — strong point)
`src/core/txcheck.js`: fail-closed simulation gate on every local-wallet signature before submit. Owner must be fee payer; no SOL loss beyond spend+fee allowance (fee default 0.02 SOL, cap 0.1 SOL); no other token movement; no authority/delegate changes; swap must deliver `minReceiveRaw`; top-level program allowlist per purpose (swap/limit/transfer). BUDGET_MS=8000, PARALLEL=4.

## Liquidity/impact guards
`src/core/trade.js`: LOW_LIQ_USD=5000, WARN_IMPACT=5%, DANGER_IMPACT=15%, MAX_QUICK_SOL=1000 SOL.

## Copy-trading (with hard limits)
`src/pages/track.js:155-229`: min 0.001 / max 50 SOL per trade, 500 SOL daily per wallet, 500 SOL daily global, min source-trade 1000 SOL floor, 3 copies/min, 120s per-wallet+coin cooldown, max source-trade age 90s, buy impact 15% / sell impact 30%. Reserve booked under Web Lock 'nought-copy-state'. **HMAC consent stamp** prevents settings-file injection of a copy config. Sells copied only out of copy-bought tally (99.5%+ = full exit).

## Discovery / behavioural
- **Surge detector** (src/pages/discover.js): 5m vol ≥ ratio×(1h vol/12) AND net buyers>0 AND price change>minPc AND vol≥minVol; editable ratio 1-12; polled every 10s from Jupiter trending/organicscore; surge lingers 10 min dimmed.
- **Trader behaviour** (src/pages/token/tabs.js:249-290): accumulating (net buyer, no sells), distributing (sold >50% of bought), scalping (≥3 round trips, median hold <5 min). 20-page history max scan.
- **Curve math** (src/core/store.js:26-30): 1,073,000,000 virtual tokens, 793,100,000 sellable, 30 virtual SOL.

## Gaps vs desk expectations
1. **Top-100 holders only** — no full-supply holder forensics; bundle %/cluster % can undercount.
2. **Cluster rule is fundingAddress-only** — no AEGIS-style deployer-memory graph; only one hop of funding inheritance.
3. **Bundles** = same-second heuristic (≥4 wallets) — no shared-funder correlation.
4. Free-tier data planes only; datapi.jup.ag auth assumed via user config.
5. **No on-chain deployer-history archaeology** — dev memory is pump.fun stream-based, not cross-program.
6. txcheck allowlist static — new programs hand-added (src/core/txcheck.js:36-44).

## Verdict
Solid browser-side terminal with unusually strong **pre-sign transaction gating** (fail-closed txcheck) and **editable audit thresholds**. Holder forensics covers RESEARCH.md rules at top-100/fundingAddress level but is shallower than AEGIS-style deployer-graph work. Good complementary screening tool; not a replacement for deep holder forensics.

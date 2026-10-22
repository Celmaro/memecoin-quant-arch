# AEGIS Desk — Ingestion Audit, Recommendations & Data-Flow Map
Date: 2026-10-19. Method: static cross-reference of all imports/calls in `*.py` + `bot/**` (blixtrade/nought clones excluded), plus table-level writer/reader mapping of aegis.db.

## 1. What the desk ingests TODAY (live, verified consumers)

| Source | Feeds | Consumed by | Status |
|---|---|---|---|
| **Telegram channels (Telethon, treizodemayo)** | `telegram_messages.jsonl` → `calls.jsonl` (CA, symbol, MC, channel, ts) | desk_supervisor `scan_new_calls()` → aegis.score_coin → alerts; paper_tick auto_buy | ✅ PRIMARY |
| **DexScreener** (dex_pair) | pair/marketCap/liquidity/vol | aegis, desk, desk_supervisor, exit_intel, journal, jupiter_estimator, position_monitor, paper_tick | ✅ heavy use |
| **Helius Enhanced tx API** (helius_txs) | wallet tx history (transfers, signers) | aegis (record_first_buyers, resolve_deployer), aegis_entity_labels (first-funder), wallet_vettor | ✅ core forensics |
| **Helius asc tx** (helius_asc_txs) | chronological mint txs | signals.estimate_fill_time | ✅ |
| **Solana RPC** (getAccountInfo/getSignatures) | mint authorities, supply, sig pages | aegis.check_authorities, record_first_buyers | ✅ |
| **RugCheck** (score, risks, topHolders, insiders, lockers) | safety score, rug flags, insider networks, holder list | aegis (checks 1-9,16), position_monitor (dev-sell watch) | ✅ core safety |
| **Ave.ai** (ave_risk, ave_top100_holders) | honeypot/mint flags, per-holder PnL/age | aegis (checks 7,15) | ✅ |
| **GoPlus** (goplus_security) | transfer_hook, non_transferable, trusted_token | aegis (check 13) | ✅ |
| **FomoAPI** (holders, user, board) | smart-money positions | aegis.fomo_overlay (check 11), desk | ✅ |
| **BLIX local radar** (trades, holders, narratives, market, alive) | live tape, dev/sniper tags, top-10, themes | aegis (checks 8,14), position_monitor (exit context), signals.wash_lpi_check | ✅ local, free |
| **GMGN** (resolve_token_meta) | symbol/MC fallback | desk_supervisor, desk, scan_curator, aegis | ✅ fallback only |
| **sol.wales** (sol_wales_traders) | whale wallet intersection | aegis (check 23), desk | ✅ |
| **CoinStats** (coinstats_balance) | wallet SOL balances | position_monitor (dev-wallet exit watch) | ⚠️ credits were exhausted per ToolStacks.md |
| **Bitquery** (bitquery_imbalance) | buy/sell imbalance | position_monitor (exit signal) | ⚠️ HTTP 402 paywalled per ToolStacks.md — likely dead in practice |
| **Jupiter** (quote_impact via jupiter_estimator) | slippage at desk size | aegis (check 12), exit_intel | ✅ |

**Dead/latent ingestion:**
- **CoinGecko** — referenced only as a rate-limit entry in desk_common; ZERO consumers. Not actually ingested.
- **Vybe / Metasleuth** — defined in aegis_entity_labels but **no API keys** → fetchers return {} (self-tier covers this).
- **Nansen** — key present, 0 credits (401). Ingestion configured but dead.
- **Bitquery** — paywalled (402); position_monitor still calls it → silently failing every cycle.
- **call_metrics** — its only writer (telegram_crawler `_record_call_metrics`) is in `archive/retired/`. **No live writer**; 647 rows are historical only.

## 2. What I recommend you ingest (gaps, ranked by desk impact)

1. **Pump.fun bonding-curve graduation events (fill-time)** — RESEARCH.md §21: slow-fill graduations = 2.4× more likely to 5×. You already have `fill_time_min`/`fill_class` in coins + `estimate_fill_time` in signals.py, but graduation events themselves come only from BLIX's Pulse column. A Helius webhook on PUMP_AMM (the §21 build note) would make this first-class. **Impact: filter quality at intake.**
2. **Wash-trade LPI detector on live tape** — the signals.py `wash_lpi_check` exists but only runs inside aegis scoring when BLIX tape is alive. Per §5, buy-size-vs-price-move anomaly + interval CV<0.3 + ρ1>0.9 per wallet is THE manipulation tell (17% of all pump.fun volume). Needs: per-wallet trade interval history (Helius has it; you fetch it for first-buyers anyway — reuse it). **Impact: directly implements consolidated rule #1.**
3. **Volume-per-unique-trader on every alert** — BLIX's own volume-spike signal lacks wallet diversity; your §5 rule (spike 10-17× with FEW wallets = wash) is stricter. Compute `vol_m5 / distinct_traders_5m` from the BLIX tape you already ingest. One line in scan_new_calls quality gates.
4. **Dev funding-graph depth 2** — funding_links now has 13 edges, but only 1 hop (first-buyer → funder). The AEGIS rule (§19: "same funding wallet, same 3 bot wallets in first block") wants depth 2: funder → funder. Helius probe already built; just recurse once on new funders not in `deployers`.
5. **Deployer rug outcome feed** — deployers.rug_count is 0 because nothing writes rug outcomes back. position_monitor already detects liq-pulled/rugged (exit events); wire those to `UPDATE deployers SET rug_count = rug_count + 1`. This is what makes the funder→rug check (−15) actually fire. **Impact: makes check 18 live.**
6. **Drop or fix dead feeds**: remove `bitquery_imbalance` call from position_monitor (saves a failing HTTP call every exit-check cycle) or gate it behind a health check; archive CoinGecko rate-limit entry; leave Nansen/Vybe/Metasleuth dormant until keys.
7. **Solscan funded-by** (ToolStacks.md §385 already maps it) — free-tier chaininfo works; a cheap secondary for first-funder cross-validation when Helius is rate-limited.

## 3. Data-flow map: ingestion → storage → consumers

```
TELETHON (7 channels) ──▶ telegram_messages.jsonl ──▶ calls.jsonl ──┐
                                                                    ▼
DEXSCREENER ──┐                                     desk_supervisor.scan_new_calls()
GMGN fallback ─┼── resolve symbol/MC ─────────────▶ (freshness<5m, dedupe, cap 5)
JUPITER ───────┘                                             │
                                                             ▼
                                              aegis.score_coin(mint, deep=True)
   ┌──────────────────────────────────────────────────────────┤
   │ RUGCHECK ──▶ checks 1-9,16 (score, risks, insiders)      │
   │ AVE.AI ────▶ checks 7,15 (honeypot, holder PnL)          │
   │ GOPLUS ─────▶ check 13 (transfer_hook, non-transferable) │
   │ SOL RPC ────▶ check 0 (mint/freeze authority)            │
   │ HELIUS ─────▶ record_first_buyers() ─▶ first_buyers ─────┼──▶ aegis_entity_labels
   │               resolve_deployer() ────▶ deployers ────────┼──▶ (funder→rug, bot rank)
   │ BLIX TAPE ──▶ checks 8,14 (momentum, dev/sniper tags)    │        │
   │ FOMO ───────▶ check 11 (smart money)                     │        ▼
   │ SOL.WALES ──▶ check 23 (whale intersection)              │   wallet_labels (score adj −15/−10/−5)
   │ JUPITER ────▶ check 12 (slippage @ $20)                  │
   └──────────────────────────────────────────────────────────┘
                                                             │
                     scan_log ◀── verdict/score ──────────────┘
                         │
                         ▼
              desk.py / journal.py / signals.py / position_monitor.py
                         │                          │
     ┌───────────────────┘                          └──────────────┐
     ▼                                                             ▼
alert → Telegram DM/HMQ                              position_monitor (open positions)
paper_tick.auto_buy() ◀── score/strategy                 │ DEXSCREENER + RUGCHECK + COINSTATS + BLIX
     │                                                   │ + BITQUERY(dead) exits: tier ladder
     ▼                                                   ▼
paper_book.json / positions.json                    decision_audit, evidence_events,
                                                    position_attribution, wallet_history

MEMORY LAYER (compounds):            READERS
  deployers          ◀── aegis       ◀── aegis (deployer_offset ±25), aegis_entity_labels (funder→rug)
  first_buyers       ◀── aegis       ◀── aegis (known-rugger check), aegis_entity_labels (bot rank)
  funding_links      ◀── (none live) ◀── bot/intelligence/cohort.py
  wallet_labels      ◀── aegis_entity_labels ◀── aegis check 18
  actors             ◀── aegis       ◀── aegis (post-buy strategy per actor class)
  evidence_events    ◀── position_monitor ◀── evidence_ttl (weighted signals for exits)
  wallet_history     ◀── wallet_intel ◀───── cohort.py (distribution intensity)
  wallet_entity_clusters ◀── cohort.py ◀── cohort.py (entity collapse)
```

**Broken pipes found:**
- `funding_links` has **no writer** (my manual inserts were one-off; cohort.py reads but nothing produces). → recommendation #4 fixes this.
- `call_metrics` has **no live writer** (writer retired with telegram_crawler). Either retire the table or port `_record_call_metrics()` into desk_supervisor.
- `positions` has **no reader** in the grep (position_monitor keeps its own positions.json) — the aegis.db positions table is written but never read → dead storage.
- `deployers.rug_count` never incremented (recommendation #5).
- Bitquery call in position_monitor fails silently every cycle (paywalled).

## 4. Priority order
1. Wire deployer rug outcomes (5) — makes the memory layer compound (30 min)
2. Volume-per-unique-trader gate in scan_new_calls (3) — 10 lines, closes the wash gap BLIX has
3. Depth-2 funder recursion (4) — activates the funding graph
4. Remove dead Bitquery call + decide call_metrics fate (6)
5. Graduation-event webhook (1) + LPI detector promotion (2) — bigger builds, schedule when you want

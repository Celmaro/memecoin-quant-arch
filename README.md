# Hermes Memecoin Quant Desk — Architecture

Solana memecoin **quick-flip research desk** running as a Hermes Agent, $100 bankroll,
learning phase (no live execution — the agent researches/scans/monitors, the user pulls
every trigger).

> **ENTRY SIGNALS COME ONLY FROM THE USER'S CURATED TELEGRAM LIST. WE BUY ALL CALLS.**
> Every other tool (DexScreener, GMGN, FomoAPI, Bitquery, Birdeye, Ave, WS streams,
> trending boards, scan-group bots) is analysis/monitoring/enrichment of calls already
> received — **never an entry source**.

---

## The pipeline at a glance

```
Curated Telegram channels (7)          HMQ scan group (output + enrichment)
      │  MTProto/Telethon listener                ▲
      │  real-time, incl. private channels        │  ONE curated card per CA
      ▼                                           │  (scan_curator.py)
calls.jsonl  (every call, deduped by mint)        │
      │                                           │  scan bots (11+, auto-discovery)
      ▼                                           │  post verdict cards on human CA posts
desk.py scan / intel / card / fomo / vet          │
      │                                           │
      ▼                                           │
aegis.py  (SQLite memory: deployer history,       │
   six checks, behavior index, AEGIS score)       │
      │                                           │
      ▼                                           │
position_monitor.py  (paper brackets, MC-adaptive exits, holder growth 5m)
      │
      ▼
journal.py  (attribution, channel quality, follow-ups)
```

## Entry control (hard rule)

- **Only** the curated Telegram channels produce calls.
- The MTProto listener (`telethon_listener.py`) watches them in real time, extracts
  contract addresses (text + href/embedded URLs — several channels hide the CA in the
  link), dedupes, and appends to `calls.jsonl`.
- All other APIs are **post-call** layers: verification, exit timing, risk context,
  second opinions.

## Core components

| File | Job |
|---|---|
| `telethon_listener.py` | MTProto real-time intake of 7 curated channels (incl. private ones t.me/s can't read); writes `telegram_messages.jsonl` + fresh mints to `calls.jsonl`; catch-up via `telethon_state.json` |
| `scan_curator.py` | HMQ group curator: on a human-posted CA, waits 90s, collects ALL scanner cards (auto-discovers new bots via card heuristics), parses every field, merges AEGIS, posts ONE curated card |
| `aegis.py` | Six on-chain checks (mint/freeze/top-10/bundle/LP/metadata) + deployer memory ("second brain"), behavior index (smart money / KOL / rug-deployer → post-buy strategy) |
| `signals.py` | Slow-graduation fill-time, wash/LPI flags, ME2F fragility (real OHLCV vol + max drawdown), holder concentration suite (top1/10/20, HHI, pseudo-Gini), holder growth via Helius |
| `position_monitor.py` | Paper brackets, MC-adaptive exits (lower MC = faster scale-out), 5-min holder-growth refresh, wallet-selling detection |
| `journal.py` | Trade/rejection/follow-up attribution, channel-quality dashboards (latency + survival per channel) |
| `desk.py` | Command orchestrator: crawl / scan / intel / buy / status / watch / close / quote / vet / fomo / holders / scan-ask / scan-watch / stream / card / journal / followups |
| `telegram_crawler.py` | t.me/s preview fallback poller (legacy; MTProto replaced it) |
| `desk_tick.py` | Old 1m/2m cron watchdog (retired — MTProto push replaced polling) |
| `stream_listener.py` | PumpDev WS monitor — **monitoring only**, capped at 25 mints |
| `replay_backfill.py` | PumpAPI replay archives → wash/LPI backtests |
| `wallet_vettor.py`, `jupiter_estimator.py` | Wallet history/behavior; Jupiter quote/slippage |

## API/tool stack (verified endpoints)

Full living inventory with per-endpoint status: **`ToolStacks.md`**.
Credentials: **NOT in the repo** — see `CONFIG.example.md` for the shape, real values
live in local `CONFIG.md` (gitignored).

- **Helius** (RPC + enhanced tx) — authorities, tx history, mother wallet, slow-grad fill
- **Rugcheck** (FluxRPC key) — full report: creator, rugged, insiders, topHolders, lockers
- **DexScreener** — market data (free; 429s → retry/backoff)
- **GMGN** — rank endpoints (sniper_count, bundler_rate, top10, wash flag)
- **Ave.ai** — contract risk (X-API-KEY, v2 host)
- **Bitquery** — USD-weighted buy/sell imbalance (V2 realtime GraphQL)
- **FomoAPI** — smart-money overlay (holders → traders → track records); quota-gated
- **DexPaprika** — pool OHLCV → realized vol + max drawdown
- **GeckoTerminal** — trades, fresh-wallet buying power, fallback OHLCV
- **Jupiter** — swap quotes / slippage at position size
- **CoinStats**, **Birdeye**, **Nansen**, **Dune**, **PumpAPI/PumpDev** — auxiliary/limited

## Daemons & durability

| Process | How it stays alive |
|---|---|
| Telethon listener (intake) | `telethon_watchdog.py` cron (5m): pidfile + `kill(pid,0)`, stop-flag `telethon_listener.off` |
| HMQ scan curator | `scan_curator_watchdog.py` cron (5m): pidfile + stop-flag `scan_curator.off` |
| Hermes gateway (Telegram bot) | systemd user service; `TELEGRAM_BOTS_REQUIRE_MENTION=true` stops bot-to-bot reply loops in the scan group |

Both watchdogs: silent when healthy; restart via `start_new_session=True`; verify alive
after 8s; notify only on event.

## Research foundation

18-paper notes distilled into operating rules in **`RESEARCH.md`**:
wash-trading prevalence (82.8% of >100% pumps show manipulation), first-5-min tape as rug
predictor, mechanical brackets > discretionary exits, holder concentration >70% fragile,
slow-graduation fill-time edge, copy-trade friction.

## Setup notes

- Python 3.12, stdlib-only for desk core; Telethon 1.45.0 in a dual-interpreter venv
  (`uv pip install --python .venv/bin/python`).
- Real credentials: `CONFIG.md` (gitignored), `.tg_credentials.json` (600, gitignored),
  `tg_session` (600, gitignored).
- Cron jobs are Hermes-managed (cronjob tool) — `no_agent` scripts for polling,
  LLM jobs for reasoning only.

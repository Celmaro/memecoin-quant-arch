# CONFIG.example.md — credential/strategy shape (NO real secrets here)

Copy to `CONFIG.md` (gitignored) and fill real values. This file documents the shape
only; real keys never enter the repo.

## Strategy: QUICK PROFITS + CONVICTION HOLDS (Yoda-style)
- **HARD RULE: ENTRY SIGNALS COME ONLY FROM THE CURATED TELEGRAM LIST.** No other
  tool/stream/board generates buys. Everything else = analysis/monitoring/exit context.
- Buy all calls early; default = quick flips (minutes-2h); HOLD LONGER when conviction
  high (fresh narrative, tech works, volume pouring in).
- Skip farms: early buyers already dumping at low MC (visible via holder flow).
- Autopilot exits: narrative dead / volume drying / good wallets selling → exit.
- FOMO inflow + rising volume → scale out SLOWER, hold longer.

## Bankroll & risk
- $100 bankroll, Solana. Learning phase: NO live execution (research/scan/monitor only;
  user pulls every trigger).
- MC-adaptive exits: <$40K → 50%@+30% / rest@+60% / stop -22%; higher MC tiers scale out
  slower, stops wider (see position_monitor.py).

## Telegram Call Channels (curated — WE BUY ALL CALLS)
- sol_trend, solwhaletrending, solearlytrending, PIRBViewTrending, soltrenchtrending,
  SeekrTrending, signalsolanaby4am (private) — real-time via MTProto listener.
- Parse via MTProto user session (telethon_listener.py) — t.me/s preview is fallback only.

## Scan/verification group (OUTPUT — not a signal source)
- HMQcheckergroup: desk posts fresh calls / curated review cards here so scan bots react.
  NEVER an entry source; do not add to listener allowlist (feedback loop).

## Credentials (placeholders — fill real values in local CONFIG.md)
- HELIUS_KEY: <helius-api-key>
- FLUXRPC_KEY: <fluxrpc-key>           # Rugcheck API key
- AVE_KEY: <ave-api-key>               # X-API-KEY, v2 host
- BITQUERY_KEY: <bitquery-api-key>
- FOMO_KEY: <fomoapi-key>
- BIRDEYE_KEY: <birdeye-key>           # market endpoints OK; security endpoints paid
- NANSEN_KEY: <nansen-key>             # 0 credits currently
- DUNE_API_KEY: <dune-key>             # free tier view-only (credits_included 0)
- DEXPAPRIKA_KEY: <dexpaprika-key>
- COINSTATS_KEY: <coinstats-key>       # currently 406 (credits)
- SOLANATRACKER_KEY: <solanatracker-key>
- HMQ_BOT_TOKEN: <hmq-bot-token>       # same bot as gateway token (sendMessage only)
- SHYFT_KEY: <shyft-key>
- PUMPDEV_KEY: <pumpdev-ws-key>
- PUBLIC RPC (free fallback): https://solana-rpc.publicnode.com + wss

## Metrics framework (from user's original quant spec)
- Liquidity-to-MC ratio · Pool liquidity depth · Vol-to-liq ratio · Slippage tolerance
- Gini / top-10 concentration · Bundled snipers % · Unique wallet growth · Holder churn
- Smart money net flow · Buy/sell imbalance · DEX→CEX ratio · New wallet purchasing power
- Honeypot/tax · LP burn/lock · Social volume · KOL co-occurrence · Social→price lead
- Realized vol · Max drawdown (see GAPMAP.md for endpoint mappings per metric)

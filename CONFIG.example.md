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
- MC-adaptive exits (single source: position_monitor.EXIT_TIERS): <$40K → clip 50%@+10% / rest@+30% / stop -15%;
  higher MC tiers scale out slower, stops wider (see position_monitor.py).

## Telegram Call Channels (curated — WE BUY ALL CALLS)
- sol_trend, solwhaletrending, solearlytrending, PIRBViewTrending, soltrenchtrending,
  SeekrTrending, signalsolanaby4am (private) — real-time via MTProto listener.
- Parse via MTProto user session (telethon_listener.py) — t.me/s preview is fallback only.

## Scan/verification group (OUTPUT — not a signal source)
- HMQcheckergroup: desk posts fresh calls / curated review cards here so scan bots react.
  NEVER an entry source; do not add to listener allowlist (feedback loop).

## Credentials (placeholders — fill real values in local CONFIG.md)
# NOTE: labels/values below MUST match desk_common.load_config() regexes — copy
# these lines verbatim into CONFIG.md and replace the placeholder hex values.
# Placeholder values are 32-hex dummy strings (replace with real keys).
- Helius (Solana RPC/DAS): 1111111111111111111111111111111111111111111111111111111111111111
- Helius: 1111111111111111111111111111111111111111111111111111111111111111
- FluxRPC API key: 1111111111111111111111111111111111111111111111111111111111111111
- Ave.ai: 1111111111111111111111111111111111111111111111111111111111111111
- Jupiter (swap/quote API): jup_pub_11111111111111111111111111
- Bitquery: ory_at_111111111111111111111111111111
- FomoAPI: fapi_11111111111111111111111111
- Birdeye: 1111111111111111111111111111111111111111111111111111111111111111
- CoinStats: 1111111111111111111111111111111111111111111111111111111111111111
- DexPaprika: api_111111111111111111111111
- Nansen: 1111111111111111111111111111111111111111111111111111111111111111
- Dune: 1111111111111111111111111111111111111111111111111111111111111111
- SolanaTracker: 1111111111111111111111111111111111111111111111111111111111111111
- Shyft.to: 11111111111111111111111111111111
- PumpDev WS key: 11111111111111111111111111111111
- HMQ_BOT_TOKEN: 1111111111111111111111111111111111111111111111111111111111111111
- PUBLIC RPC (free fallback): https://solana-rpc.publicnode.com + wss

## Metrics framework (from user's original quant spec)
- Liquidity-to-MC ratio · Pool liquidity depth · Vol-to-liq ratio · Slippage tolerance
- Gini / top-10 concentration · Bundled snipers % · Unique wallet growth · Holder churn
- Smart money net flow · Buy/sell imbalance · DEX→CEX ratio · New wallet purchasing power
- Honeypot/tax · LP burn/lock · Social volume · KOL co-occurrence · Social→price lead
- Realized vol · Max drawdown (see GAPMAP.md for endpoint mappings per metric)

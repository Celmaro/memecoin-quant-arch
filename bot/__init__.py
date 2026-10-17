"""Memecoin Quant Desk — doc-1/doc-2 architecture package.

Map (doc 1 "correct operating model"):
  signal_ingestion/  - Telegram signal intake (allowlist, parser, dedupe)
  execution/         - auto entry, swap executor, fill confirmation, safety
  position_manager/  - lifecycle, ledger, cost basis, scale-out, add, full exit
  intelligence/      - market, wallets, leaderboard, forensics, holders, liquidity, signal context
  decision_engine/   - evidence aggregator, thesis tracker, risk engine, policies
  data_sources/      - gmgn, dexscreener, pumpfun, custom onchain adapters
  persistence/       - events, positions, wallet history, decision audit
  orchestration/     - event bus, position workers, scheduler, recovery

The live runtime (position_monitor.py, paper_tick.py, desk_tick.py) imports
these modules; each module is a REAL implementation (not a stub).
"""

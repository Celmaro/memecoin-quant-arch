# GRADUATE SLOT REPLAY — Oct 10 2026 graduates (14:00–16:00 UTC), 105 coins
Data: graduates-2026-10-10-1400-1600utc.csv (signals) + per-graduate slot tape
rebuilt from pumpapi replay 14/15/16h (validated vs one-coin-odyssey LaunchTape
reference: 3001/3001 rows matched; only mismatch = the documented +178.7s whale
dump row). Engine: bot/audit/graduate_replay.py (user 6 rules).

## FIRST 10 MINUTES (graduates CSV columns, n=105)
  touched_minus80_10m=1 → 23.8% (25/105)
  touched_2x_10m=1      →  6.7% (7/105)

## REALISTIC (rule-1 slot-end fills, 1.5% cost/side, size moves price, $20 slot)
  coins traded: 105 (0 never reached entry, 0 tape-ended-open)
  win rate: 40.0%
  median return/trade: -29.8%   mean: -19.5%
  total PnL: -2.7342 SOL (-19.5% of 14.00 SOL deployed ≈ -$410)
  exit reasons: STOP 49, SELL_180S 47, TP2 6, TP1 3
  worst 3: BTRUyo -97.6% (STOP), 3vGkm3 -97.4% (TP2), nVsqkQ -95.2% (STOP)
            (micro pools: $20 order is the pool — constant-product impact)

## IDEALIZED (signal's own price = last price seen, NO costs, NO impact)
  coins traded: 105
  win rate: 49.5%
  median return/trade: -4.2%   mean: +1.5%
  total PnL: +0.2101 SOL (+1.5% ≈ +$32)
  exit reasons: STOP 51, SELL_180S 48, TP2 6
  worst 3: Ff41mq -82.2% (STOP), 5W24wM -81.7% (STOP), BTRUyo -72.5% (STOP)
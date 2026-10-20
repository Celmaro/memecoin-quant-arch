# STRATEGY COMPARISON — Oct 10 graduates (14:00–16:00 UTC, n=105)
Same coins, same slot tape (pumpapi replay, validated vs odyssey LaunchTape
3001/3001), same $20 slot, same +15s entry (first slot end after signal, 1%
slip). Differences = exit strategy + cost model + gate. REAL executor
(scaleout.execute_stage: 3% fee/side, stages, UNSELLABLE, MIN_TRANCHE) with
book I/O monkeypatched to temp — live paper_book.json untouched.

## RESULTS (mean return/trade, win rate, total on deployed)
| strategy | traded | win% | median | mean | total |
|---|---|---|---|---|---|
| A. memequant engine as-is (tier ladder, $10K gate, 3% fee) | 78 | 28.2% | −30.5% | **−15.2%** | −$236.83 |
| B. engine WITHOUT $10K liq gate | 105 | 21.0% | −34.7% | **−35.8%** | −$750.81 |
| C. engine + **+180s time exit** (gate ON, ladder kept) | 78 | 52.6% | +2.5% | **−1.6%** | −$24.24 |
| D. engine +180s ONLY, no ladder/stops (pure time exit) | 78 | 57.7% | +3.1% | **−1.7%** | −$26.23 |
| E. user replay rules (1.5% cost, size impact, no gate) | 105 | 40.0% | −29.8% | **−19.5%** | −2.73 SOL |
| F. idealized (signal price, no cost, no impact) | 105 | 49.5% | −4.2% | **+1.5%** | +0.21 SOL |

## WHAT THE COMPARISON PROVES
1. **The tier ladder's 0.75 stop is the engine's leak.** As-is the engine books
   53 STOPs at median −30% — the same stop-then-run pattern from the earlier
   tape (65% of coins dip below the stop BEFORE the run). With a **+180s time
   exit added, win rate jumps 28.2% → 52.6% and mean −15.2% → −1.6%** on the
   exact same engine, fees, and gate. The pure time exit (D) is essentially
   identical to (C) — the ladder adds nothing once the time exit exists.
2. **The $10K liq gate is worth +20.6 points** (B vs A: −35.8% → −15.2%). It
   filters 27 dead-micro graduates (pools $2–$422) that are pure poison.
3. **The user's replay rules (E, −19.5%) are WORSE than the engine with a time
   exit (C, −1.6%)** — the 1.5% cost + size impact model is honest, but
   without a liq gate and with only the +180s exit it still rides the
   shakeout. The engine's gate + time exit nearly matches the idealized
   fantasy (F, +1.5%).

## IMPROVEMENTS FOR THE MEMEQUANT ENGINE (by leverage)
1. **ADD A +180s TIME EXIT** (rule: position closes at first slot end after
   +180s unless a stop already fired). Biggest single lever: −15.2% → −1.6%.
   The quick-flip thesis is 2–3 min, not "hold until the ladder stops you".
2. **KEEP (and maybe RAISE) the $10K entry liq gate** — it already filters the
   worst 27/105. Consider $20K for stricter dead-micro exclusion.
3. **LOOSEN THE STOP to ~0.70 or trail to breakeven after first clip** — 53/78
   exits were 0.75-stops at median −30%; the time exit cuts the damage but 27
   STILL trip. The stop should catch real deaths, not shakeouts.
4. **Keep the 3% fee honest** — the engine's real-cost model is the point; do
   not "improve" by hiding fees (F is the illusion, C is reality).

Caveat: one day, 105 coins, Oct 10 only — validate on more days before
retuning the live ladder/adding the time exit.

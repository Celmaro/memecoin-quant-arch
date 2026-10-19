# DEEP BACKTEST + ANALYSIS + AUDIT — Memecoin Quant Desk
**12h tape window: Oct 18 21:00 → Oct 19 09:00 UTC (PumpAPI replay, cached)**
**Generated 2026-10-20, from tape only (no paper book self-report)**

---

## 1. Deep Backtest — the 12h tape replay

**171 unique calls, 170 with tape** (52 TP2, 107 STOP, 11 OPEN, 1 NO_FILL).

- **TP2 win rate: 31% | STOP rate: 63%** — break-even needs ~55% TP2 at this ladder (TP2 +18.4% avg vs STOP −22.5% avg). The tape confirms: buying every call at face value is a **structural loser**.
- Median winner: **push60 = 1.33x** (pumps within 60s of the call), median loser: **1.04x** — the tape separates winners from losers in the first minute.
- Median winner buy:sell 5m = **1.38** vs loser 1.09; median call lag 336s (win) vs 410s (lose) — fresher calls win.

### Channel ranking (12h, n per channel)
| channel | n | TP2 | stop | verdict |
|---|---|---|---|---|
| signalsolanaby4am | 10 | **50%** | 50% | best alpha |
| soltrenchtrending | 56 | **39%** | 48% | solid |
| seekrtrending | 20 | 30% | 70% | weak |
| solearlytrending | 62 | 27% | 69% | stop factory |
| pirbviewtrending | 11 | 18% | 73% | weak |
| **solwhaletrending** | 11 | **0%** | **91%** | **poison — 10/11 stops** |

## 2. Anomalies (from tape)

- **40% of stopped calls recovered to ≥1.0x within 2h** (32/80 on the 11h run): MUSKITO 4.98x, SPACEXSITESLAB 3.84x, GODCHUD 3.41x, INCOME 2.98x, GAR0U 2.14x all recovered after our −22% stop — **the stop ladder catches shakeouts**.
- 23 of those recovered to ≥1.30x = would-be TP2 wins if held.
- Artifacts guarded: NET +23,342%, SIRIUS −102.8% (bonding micro-price / 0.00 mult).
- 2 dead-tape calls (no tape events) — should be filtered pre-buy.

## 3. AEGIS behavior audit — SCORES DO NOT PREDICT COINS

Joined AEGIS scores to actual tape outcomes (n=144 scored):

| band | n | TP2 | stop |
|---|---|---|---|
| 0–29 RISK | 11 | 27% | 73% |
| 30–49 | 22 | 32% | 64% |
| 50–69 | 85 | 31% | 65% |
| 70+ LIVE | 26 | 35% | 54% |
| verdict LIVE/CAUTION/RISK | 69/58/17 | 32/31/29% | 59/66/71% |

**corr(aegis_score, tape_pnl) ≈ −0.065 → zero predictive power.**
Verdict bands are flat: LIVE 32% ≈ CAUTION 31% ≈ RISK 29% ≈ baseline 31%.
**Only the edges of the score scale carry information** (RISK<30 as veto; 70+ slight lift), and the 50–69 "good" band is the *worst* large bucket. Channel choice dwarfs AEGIS.

## 4. Exit audit — why so many emergency exits, so few scaleouts

Paper books (304 trades): **118 emergency-class (39%) = −$712.86** vs 175 progressive scaleouts (58%) = **+$393.70**.
- The book-killer is the emergency path, not the ladder.
- Root causes (now fixed in code): liq<$1K single-read → instant total-loss write-off (phantom −$51.75 across 5 UP coins); emergency exits booked at sellable liq 18×; the 0.50 hard stop fires on shakeouts the tape shows recover.
- **exit-verify (167 exits, tape-truth check) still streaming — pending final count.**

## 5. The Edge — combined tape rules (simulated, $20 slot, 3% fee, ladder)

| rule | n | TP2% | $100-book PnL |
|---|---|---|---|
| BUY ALL (baseline) | 170 | 31% | **+$148.60** |
| push60 ≥ 1.30 | 39 | 69% | **+$263.06** |
| **bs5m≥1.3 AND push60≥1.2** | 22 | **86%** | **+$208.36** |
| SKIP bs<0.7, buy rest | 134 | 37% | −$87.69 |
| skip bs<0.7 AND push<1.3 | 116 | 22% | −$67.51 |

**The tape edge is real and sharp:** bs5m≥1.3 ∧ push60≥1.2 → **86% TP2, 14% STOP**. Channel × edge: signalsolanaby4am 4/4 (100%), soltrenchtrending 10/10 (100%), solearlytrending 3/4 (75%).
Buying the leftovers (bs<0.7 / push<1.2) is the money-burn zone (−$67 to −$88).

## 6. Recommendations (tape-backed)

1. **Tape pre-filter before buy:** require push60≥1.2 AND bs5m≥1.3 (86% TP2, 14% STOP) — cuts 87% of calls, keeps the winners. This is the single biggest edge found.
2. **Channel weighting:** signalsolanaby4am + soltrenchtrending stay; solwhaletrending (0% TP2, 91% stop) and solearlytrending (69% stop) need downgrade/removal or strict gate.
3. **Stop ladder:** −22% micro-cap stop is too tight — 40% of stops recovered ≥1.0x, 23 would've been TP2 wins. Consider wider stop or time-based re-entry logic.
4. **AEGIS:** stop using the mid-range score as confidence; use only <30 as veto and 70+ as mild boost. Rerank by tape features (push60/bs5m) instead.
5. **Dead-tape filter:** skip calls with no tape events in first seconds.
6. Keep emergency-exit fixes live (confirmation streak, on-chain cross-check) — they address the −$712.86 emergency drain.

---
*Data: deep_audit_rows.json, replay_12h_run.log, aegis.db, cached PumpAPI replay. Replay engine: replay_backtest.py (--reuse-any), anomaly_scan.py.*

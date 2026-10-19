# COINS + ENTRY + POSITION-MONITOR + SCORE-EVOLUTION AUDIT (from downloaded tape)
**12h window: Oct 18 21:00 → Oct 19 09:00 UTC · 171 unique calls · 170 with tape · cached PumpAPI replay only**
**Full desk-audit companion to DEEP_AUDIT_20261020.md — this report is ENTIRELY from the tape, not the desk's self-reported books**

---

## 1. Graduated vs Non-Graduated — THE entry edge

Graduation (pump.fun curve-fill at **$69K MC**) measured on tape (max marketCapQuote×150 ≥ $69K within 2h of call):

| bucket | n | TP2% | STOP% | call-MC med | max-MC med |
|---|---|---|---|---|---|
| **GRAD-DURING** (crossed curve-fill AFTER our entry) | 33 | **61%** | 39% | $37.3K | $145.6K |
| ALREADY-GRAD (already ≥$69K at fill) | 73 | 26% | 70% | $70K | $314.5K |
| NEVER-GRAD (never reached $69K in window) | 64 | 20% | 69% | $19.1K | $32.3K |

**The single strongest tape signal: coins that cross the $69K graduation threshold *after* our entry win 3× more.** Median time-to-graduation for that bucket: **142 seconds** after call (21/33 grad within 5 min). Buying already-graduated coins is a stop factory (70%), and never-grad microcaps are dead money (69% stops).

## 2. Market-Cap Entry bands (fill MC from tape)

| tier band | n | TP2% | STOP% |
|---|---|---|---|
| < $40K (t1) | 66 | 33% | 56% |
| $40–150K (t2) | 72 | 33% | 67% |
| $150K–1.5M (t3) | 14 | 29% | 71% |
| **≥ $1.5M (t4)** | 18 | **11%** | **72%** |

**Big caps are poison**: entries ≥$1.5M won 2/18 (11%). The tape says our edge is small/mid caps on the way to graduation, never post-pump mega-caps.

## 3. $10K LIQUIDITY GATE = GATE BLINDNESS (blocked our best trades)

The desk's hard gate (liq ≥ $10K or skip) — measured against `coins.liq_at_call`:

- **GRAD-DURING: 22/33 (67%) had liq_at_call = $0 → the gate blocks 2 of every 3 of our BEST cohort (64% TP2 among blocked)**
- NEVER-GRAD: 48/64 liq 0, gate blocked 51 — correctly filters the fade zone
- Root cause: **pre-graduation bonding-curve coins have no pool liquidity to report ($0), so the liq gate rejects exactly the coins that are about to pump through the curve.** Liq is the wrong gate; curve-proximity is the right one.

## 4. Entry quality (fill vs call, from tape)

- Median **entry gap: +38.7%** above call MC (mean +29% over 152 sane rows; 24 entries >100% above; 29 below) — we systematically buy ~40% higher than the channel's printed MC. The channel MC is stale the second we enter.
- Fill latency median ~2s (t1 in the 1.5s Jito window).
- Wasted-winner count from tape: 32/80 stops recovered to ≥1.0x within 2h, 23 to ≥1.30x (see anomaly report).

## 5. Position-monitor audit (ladder as-designed, per tier, tape sim)

| tier (EXIT_TIERS) | n | TP2% | sim PnL ($20 slot) |
|---|---|---|---|
| t1 <$40K (1.30/1.60/−22%) | 66 | 33% | +$147.83 |
| t2 $40–150K (1.35/1.75/−25%) | 72 | 33% | +$407.40 |
| t3 $150K–1.5M (1.50/2.00/−30%) | 14 | 29% | +$69.84 |
| t4 ≥$1.5M (1.75/2.50/−35%) | 18 | 11% | +$21.34 |

Tape says the ladder itself is fine — it's the **entry selection** that loses. Position-monitor self-reported attribution is broken anyway: position_attribution shows median MFE 0.14, is_exit 0/212 (the desk records almost no favorable entry excursion — a data bug on its own).

## 6. AEGIS score evolution — AGAIN flat / anti-predictive

- corr(score, tape win): **+0.003** on 144 scored (coins-table score)
- Score distribution by bucket: ALREADY-GRAD med 56, GRAD-DURING med 60, NEVER-GRAD med 60 — **the score cannot tell which coin will graduate**
- Scan evolution vs outcome: score UP >+2 → 23% TP2; score DOWN <−2 → **45% TP2**; winners avg Δ **−0.8**, losers avg Δ **+0.7** — **scores move backward on winners.** AEGIS rewards contract cleanliness; the tape rewards imminent graduation.
- Only <30 veto and 70+ mild boost remain usable (same conclusion as the 12h backtest join).

## 7. Full Recommendations (tape-backed)

1. **Graduation-anticipation filter (top priority):** prefer coins with call-MC **below $69K and rising**, entering to ride the curve-fill ($30–65K band). Already-grad ≥$69K: skip or halve size (26% TP2). This single rule triples expected TP2 rate.
2. **Replace the liq gate** with a graduation-proximity + tape-health gate: keep liq read as *context*, but never block a sub-$69K call on liq=$0 (bonding-curve = no pool liq = not dead). Block on NEVER-GRAD evidence instead (dead tape, vol <$20K, bs5m < 0.7).
3. **Kill ≥$1.5M entries** (11% TP2) — taper size by entry-MC (inverse ladder: smallest size at biggest MC).
4. **Entry slippage awareness:** assume −40% vs channel MC; size accordingly (a $20 slot is a $28 commitment at tape prices).
5. **AEGIS: keep <30 veto + 70+ boost only**; add "score trending DOWN within 2h of a win" as a confirm (currently mislabeled negative).
6. **Stop-ladder on grad-during coins:** after graduation (curve-fill), the post-grad pump is short (median TP2 58s) — keep quick-clip on grad-during, but hold past the first stop shakeout if the coin was still sub-$69K at entry (40% stop-recovery class).
7. **Fix position_attribution data quality** (MFE/MAE/is_exit) before trusting any desk-side entry scoring.

---
*Data: tape_coins_audit.py → tape_coins_rows.json · caches: _replay_cache/*.mints.jsonl · aegis.db coins/scan_log · calls.jsonl. Companion: DEEP_AUDIT_20261020.md (strategy/exit audit).*
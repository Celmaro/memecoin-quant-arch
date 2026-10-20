# LIVE-MONEY FEASIBILITY STUDY (Oct 20 2026)

Question: can the desk go live with real money? Answer: **NOT YET — four hard blockers,
each verified, each with a testable gate.**

## 1. There is NO execution stack today (hardest blocker)

Audited `~/memecoin-quant/`: `jupiter_estimator.py` is a **quote-only** module
(api.jup.ag/swap/v1|v2/quote → fee/slip estimation for the paper book). There is:
- NO wallet keypair (no `solders`/`solana`/`anchor`/`base58` packages in the venv)
- NO tx signing, NO `sendTransaction`, NO blockhash handling, NO priority-fee bidding
- NO MEV/sandwich protection, NO tx retry/failure handling

Going live means building an entire execution layer (wallet custody, sign, submit,
priority fees, retries, fallback RPC) — weeks of work with $100 at stake. This is the
single biggest reason the answer is "not yet", independent of all strategy questions.

## 2. The engine LOSES on the tape (real-engine replay, Oct 10 — n=105 graduates)

Ran the REAL production engine (`scaleout.execute_stage`, live EXIT_TIERS, $10K gate,
3% fee/side, book I/O monkeypatched — STRATEGY_COMPARE_20261010.md, archived ba8c143):

| Config | Win% | Mean/trade | Total |
|---|---|---|---|
| A. Engine as-is (gate+ladder) | 28.2% | **−15.2%** | −$237 |
| B. No $10K gate | 21.0% | −35.8% | −$751 |
| **C. +180s time exit (gate ON)** | **52.6%** | **−1.6%** | **−$24** |
| D. +180s only | 57.7% | −1.7% | −$26 |

Findings: (a) current engine **bleeds money live** (−15.2%/trade); (b) the $10K liq
gate is worth +20.6 pts — keep/raise; (c) the ONLY config that approaches breakeven
is the **+180s time exit, which is still NOT WIRED** (user decision pending from
STRATEGY_COMPARE). Even C is −1.6% — not profitable yet.

## 3. The tape distribution says stops dominate

Oct 18 12h window (171 calls, P10 proxy): **33% TP2 / 62% STOP** — two of three calls
die at the 0.75 stop before ever reaching 2.0x. Median peak is 1.73x but 65% dip below
0.85 first (stop-then-run). The ladder retune (TP2.0/SL0.75) fixed the WORST leak
(old ladder −3.9%/call → +5.2% on that window) but the real-engine Oct-10 replay still
shows −15.2% mean — the ladder alone does not make the engine profitable.

## 4. Real-money frictions the paper does NOT model

- **Thin-pool fills**: a $20 order in a $2–$9 pool books near-total loss by honest
  constant-product math (verified: 3vGkm3 −97% on a 0.012 SOL pool; BTRUyo −97.6%).
  Live, that's REAL money gone — paper just records it.
- **Fee drag**: 3%/side paper = $1.20 round-trip on $20 (6%). Even at best-case
  1% PumpSwap + 0.5% slip/priority = $0.60 round-trip. At −15.2%/trade mean, fees are
  not the problem — but they compound every losing trade.
- **MEV/sandwich + priority bidding**: not modeled anywhere in the desk.
- **Live paper record agrees**: $100 → $8.32 (−92%), 14 trades, 36% win, avg win +4.05
  vs avg loss −7.99. The desk has NEVER shown a sustained profitable run in paper.

## Path to live (testable gates, in order)

1. **Wire the +180s time exit into paper_tick** (pending user decision) — it is the
   only config near breakeven (−1.6% vs −15.2%). Re-run the Oct-10 real-engine replay
   with it wired; gate = mean ≥ +1%/trade (still not enough to go live, but the first
   positive sign).
2. **Validate on ≥2 independent tape windows**: Oct 10 (14–16 UTC) AND Oct 18 (21–09
   UTC) AND the new PumpData shrine archive (cross-validation tool already built).
   Gate = mean ≥ +5%/trade on BOTH windows with the same config. One day is a small
   sample; two windows agreeing is the minimum.
3. **Raise the liq gate for live** (worth +20.6 pts already): a $20 order must never be
   >2% of pool depth. Gate = min pool liq ≥ $1K for a $20 slot; consider $2.5K.
4. **Build the execution layer incrementally**: quote → dry-run sign → $10 total tiny
   live with strict daily stop ($3), MEV protection ON, priority ≤ 0.001 SOL. Gate =
   fills within 15% of quote on 10 consecutive test swaps.
5. **Only then** scale to $15–20 slots, keep the daily $20 stop, and review weekly.

## Bottom line

Going live TODAY with real money = repeating the tape's −15.2%/trade (≈ −$150 on the
Oct-10 sample at $20/slot) plus unmodeled MEV/thin-pool risk, on an execution stack
that does not exist. The desk is a RESEARCH/PAPER desk until: (a) +180s time exit is
wired and proves positive on two tape windows, and (b) the execution layer is built
and dry-run-verified. Estimated minimum work: 1 strategy change (hours, pending user
go) + 2 tape validations (hours) + execution stack (days). Recommend wiring +180s and
validating first — everything else is downstream of that decision.

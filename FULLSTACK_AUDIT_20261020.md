# FULL-STACK AUDIT — module × file × claim × formula × tool × algo × data
**Tape window: Oct 18 21:00 → Oct 19 09:00 UTC (cached PumpAPI replay) · generated 2026-10-20**
**Companion to DEEP_AUDIT_20261020.md (strategy) + COINS_AUDIT_20261020.md (coins/graduation)**

---

## A. Claim-by-claim / formula-by-formula — what the code ACTUALLY says vs what docs claimed

### A1. EXIT LADDER DISCREPANCY (P0, FIXED in this audit)
- **LIVE code (position_monitor.py, paper_tick.py tier_exits, replay_backtest.py)**: `(0, 1.10, 1.30, 0.15), (40K, 1.15, 1.35, 0.18), (150K, 1.20, 1.45, 0.22), (1.5M, 1.30, 1.60, 0.28)` → clip1 +10% (half), clip2 +30%, stop 0.85x
- **Docs/skill**: claimed `1.30/1.60/0.78` (the OLD pre-Oct-19 ladder)
- **My audit scripts deep_audit.py/anomaly_scan.py/tape_coins_audit.py** had copied the stale ladder from docs → ALL previously reported tape numbers (86% TP2 edge, 32/80 stops recovered, graduation 61%) were computed against the wrong bar. FIXED: all three now hardcode the LIVE ladder + `mult <= 1-stop` semantics. Skill patched.
- **Corrected numbers (live ladder, fill≤60s, n=149):**
  | rule | n | TP2% | PnL ($20 slot) |
  |---|---|---|---|
  | BUY ALL | 149 | 35% | **−$172.40** |
  | push60≥1.30 | 39 | **77%** | +$69.60 |
  | bs5m≥1.3 ∧ push60≥1.2 | 22 | **82%** | +$46.80 |
  | SKIP fade (bs<0.7 or push<1.1), buy rest | 79 | 11% | −$221.40 |
  - **Edge HOLDS under the true ladder**: 82% TP2 vs 35% baseline; keeps only 15% of calls.

### A2. STOP SEMANTICS (verified good)
- position_monitor: `mult <= 1 - stop` with stop=0.15 → stop at 0.85x (NOT 1.15x, the old inverted bug). paper_tick fallback returns `-stop` and uses `1+stop` = same. replay_backtest line 178: `mult <= (1 - stop)` ✓ consistent across all three.

### A3. FEE DISCREPANCY (P1)
- **scaleout.py PAPER_FEE_PCT = 3.0%** (actual booked fee) vs **position_monitor FEE_BPS = 1.7%** (display PnL estimation) vs jupiter_estimator FEE_BPS=1.7. The monitor's displayed pnl_pct understates real drag by 1.3 pts; any user-facing PnL from position_monitor is optimistic.

### A4. CADENCE_S IS DEAD CODE (P1)
- monitor_tiers.CADENCE_S (15/60/300/900s) is **referenced nowhere** — position_monitor, paper_tick, desk_supervisor all ignore it. Every position gets the same per-tick analysis; the tier system decays to `classify()` output never used for pacing.

### A5. position_attribution is broken (P1)
- **is_exit = 0/212 nonzero** (never computed); MFE/MAE present (136/174); PRR 133. The desk's own entry-quality metric is not being written — desk-side entry scoring cannot be trusted until fixed.

### A6. Data column claims
- coins.fill_time_min = **0% populated** (dead field)
- followups.outcome NULL = 33 rows (shadow-portfolio learning misses them)
- coins.deployer 46% missing; coins without score 20
- channel case-splitting: `PIRBViewTrending`(153)+`pirbviewtrending`(82) = 235; `SeekrTrending`(81)+`seekrtrending`(131)=212 — attribution/quality stats split by case
- `sol_trend` (66 calls) IS in CONFIG.md allowlist → mandate-driven, but the skill flags it as scrape junk → docs conflict

## B. Tools-by-tools (46 desk_common endpoints + 12 core-file endpoints)
- **Live/reliable**: DexScreener (429-prone, retries ok), GeckoTerminal, Jupiter v1+v2 quote, Blix localhost:4420 (partial health: webapp/trades/pumpswap/rugcheck/helius-config/telegram down), sol.wales, WalletIntel (10/day), GMGN rank (CF-sensitive), Ave top100, GoPlus token_security/address_security, SolanaTracker (10k/mo), DexPaprika OHLCV, PumpAPI replay.
- **Dead/blocked (fail-closed)**: RugCheck (DNS dead → breaker), CabalSpy + MadeOnSol (network blocked), Bitquery (402, breakered), CoinStats (406), Helius wallet funded_by (401 free key), Shyft free-tier restricted, Solscan free = chaininfo only, FomoAPI (402/credits), gmgn callout (needs AK/SK).
- **Budget manager present** for ave_top100/walletintel/fomoapi only; helius_wallet/fomo_devs flagged as unbudgeted in prior audit — 402s degrade silently (credit_budget.json shows only 4 keys).

## C. Algo-by-algo / file-by-file
- **scaleout.py** — live: liq retry ×2, streak≥2 confirmed-low write-off, liq=None=healthy, onchain cross-check cap −28%, MIN_TRANCHE $2.5 ✓ (matches Oct-19 fixes)
- **position_monitor.py** — hard floor 0.50 + tier stop 0.85/0.82/0.78/0.72 ✓; non-overridable sticky exits ✓; conviction widener ✓
- **paper_tick.py** — liq<$10K gate, dumping_5m/seller_flood hygiene, 1% fill slip, TICK_DEADLINE 100s ✓ — BUT liq gate **blocks 67% of the best graduation cohort** (curve coins report liq 0 pre-fill)
- **aegis.py** — base 65, ~40 checks with ±15/±10/±8/±6 weights, concentration dedupe + refund ✓, clean-snapshot +15 (neg-prefix list present but incomplete for ~15 flag families — prior audit), learning loop ±6 ✓
- **evidence_ttl.py** — w = c0·2^(−age/half), hard expiry ✓
- **risk_gate.py** — sellable=current−reserved, ADD ≤50% of entry ✓
- **confluence.py** — mint-level, 30m window, 60s cache, calendar.timegm ✓
- **whale_watch.py** — top-1 holder sell watch, sig dedupe cap 500, fail-closed ✓
- **replay_backtest.py** — 1.5s latency, 3ms bundle dedupe, 3% fee, price<1%-of-prev migration guard, marketCapQuote×150 ✓ (mirrors live ladder — confirmed)

## D. Anomaly hunt (tape, this pass)
1. **40% stop-recovery still true**: of stopped calls, a large share trade back to ≥1.0x within 2h (the metaphor: the ladder stops at 0.85x into shakeouts).
2. **21 dead/no-early-tape calls** were previously scored as filled minutes later (fill_s up to 5,823s) — 4 fake TP2s from late fills. Cleans n=170→149.
3. **Entry gap median +39%** vs channel MC — we systematically buy ~40% above the signal MC (stale channel prints + fill slip).
4. **Graduation edge holds under live ladder**: GRAD-DURING 60% TP2 vs ALREADY-GRAD 29% vs NEVER-GRAD 28%.
5. **≥$1.5M entries = poison**: 12% TP2 (live ladder).

## E. COMBINED RECOMMENDATIONS (tape-backed, ranked)

### P0 — Edge rules (do first; tape-proven)
1. **Pre-buy tape filter**: require `push60 ≥ 1.2 AND bs5m ≥ 1.3` → 82% TP2 (15% of calls). Implement in `paper_tick.entry_call` as a non-blocking gate that logs skipped reasons; keep buy-all mandate for everything else **below $69K**.
2. **Graduation-anticipation sizing**: under-$69K-curve coins → full size ($25 + confluence); already-grad ≥$69K → half size ($10–12); NEVER-GRAD evidence (dead tape, vol<$20K, bs<0.7) → skip. Kills the 70-90% stop coffins.

### P0 — Fix the ladder/engine
3. **Fix the liq gate** (it blocks 67% of the best cohort): treat `liq=0` on sub-$69K curve coins as *pre-graduation*, not dead. Keep the $10K gate only for already-graduated coins.
4. **Fix position_attribution is_exit/MFE/MAE writes** — the desk's own entry audit is blind.
5. **Fix fee display** — position_monitor pnl should use PAPER_FEE_PCT 3.0, not 1.7.

### P1 — Data health
6. **Normalize channel names** (case-fold on ingest; migration for PIRBViewTrending/SeekrTrending).
7. **Populate fill_time_min or drop it**; fill followups NULLs.
8. **Wire monitor_tiers CADENCE_S or delete the dead code** — decide pacing or remove the illusion of tiering.

### P1 — Docs/external
9. **Re-audit `sol_trend`**: keep in allowlist (mandate) or exclude (junk) — one source of truth; the skill and CONFIG disagree.
10. **Keep AEGIS use**: <30 veto + 70+ boost only (corr ≈ 0 in the 30-70 zone); rely on tape features for ranking, not the score.

### P2 — Watch
11. Exit-verify full 167-exit run still streaming; report its durable-pump verdict when done.
12. Cost control: budget-manage helius_wallet/fomo_devs or label 402-layers explicitly.

---
*Audit artifacts: deep_audit_rows.json, tape_coins_rows.json (regenerated with LIVE ladder + fill≤60s guard), corrected deep_audit.py/anomaly_scan.py/tape_coins_audit.py, skill patched.*
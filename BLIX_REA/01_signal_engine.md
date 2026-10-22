# BLIX Signal/Alert Engine — Deep REA Analysis (blixtrade server/)

REA Evidence: `ev_09a5a29577980294351371f193fb7a542ee0eca51ecb02609d52ba8066d01a84` (scoped analyze_javascript_application: brain/rules/triage/early/research/quality/settings/engine.js — 8 parsed, 40,534 AST nodes, 328 findings); full-repo parent `ev_f34217f8fe8a858272fabb362cfb1071b616051067d6ec34686b0500b0eea2c4`. All file:line cites verified against source. Repo digest sha256 `011f033d…5ed8f`.

## 0. Architecture in one paragraph
Alert-only, paper-trade-only (brain.js:8). Pipeline: pump.fun launch feed → **triage.js** instant rules + fast LLM labels → **early.js** launch-feature logistic-regression models at 30/60/120/300s checkpoints → **research.js** fast AI read + deep AI read (narrative grade A+..F, trade call) → **brain.js** pick gate → 1-second entry watcher → **exit rule** (brain.js stepPick / rules.js simExit) → scorecard → **self-review** rewrites the playbook. Every graded coin, pick and call is tracked 24h via DexScreener (outcomes table, brain.js:40-47).

## 1. Signal generation

### 1a. Triage (server/triage.js) — slop filter
- **Instant rules score**, every launch, re-run at 1s and after metadata loads (rulesPass triage.js:163-172): base 40; dev ≥10 launches/6h −30 (≥4 → −15); same-ticker ≥5 in the hour −18 (≥2 −6); X link +14 (specific tweet) / +9; website +5; TG +3; no socials −8; description>30 chars +4 else −4; no image −10; vowelless/`ab123` gibberish ticker −8; dev buy 0.5–4 SOL +4, >15 SOL −6. Clamped 0–100 (triage.js:41-58).
- **Labels** (labelFor triage.js:60): ≥70 promising, ≥50 maybe, ≥30 meh, else slop. Fast LLM (Groq→Grok→Haiku) batches every 2s, 25 coins Groq / 8 Grok, waits 5s post-creation for metadata (triage.js:73-121).
- **Escalation to research desk** (escalate triage.js:124-142): LLM label ∈ {maybe, promising} **and** score ≥ `triageEscalate`=55 (settings.js:60) — the rules score alone never escalates; **or** traction override: ≥20 distinct traders & buys>sells & not-slop (slop needs ≥40 traders). Escalated via `adoptLaunch` → enqueue priority `2e11 + score*1e6`.
- **Self-check** (checkOutcomes triage.js:145-154, triageRecord 156-160): each label scored against live ATH at 1h; buckets hit30k/hit100k.

### 1b. Early radar (server/early.js) — launch model
- Records every launch from first trade; **checkpoints at 30s, 60s, 2m, 5m** (`CPS` early.js:36) snapshot 21 features (early.js:73-86: log-scaled traders/buys/sells/vol, buyShare, mult, offHigh, big buys ≥1 SOL, maxBuy, devSol, devCount, triage rules/triScore, socials flags, newTraders, tradesPerSec, recentBuyShare).
- **The question: 2x (WIN) before −40% (STOP 0.6)** within a 30-minute horizon (early.js:37-38); TP fills at its level, stop fills at the dump price (early.js:66-68). Requires ≥5 traders to even record (early.js:117).
- **Logistic regression** per checkpoint, refit every 10 min on a worker thread (early.js:364-367); needs MIN_SAMPLES=300 rows & ≥15 wins else status "learning" (early.js:39,198). Trained on 3 days of resolved rows, excluding last 36 min (early.js:192); **walk-forward test: fit oldest 70%, score newest 30%** (early.js:200). Valid iff top-25% of unseen launches win ≥1.5× base rate and avg return beats base, n≥20 (early.js:209). `thr10`/`thr5` = score thresholds at the model's own top-10%/top-5% (early.js:212-214). `autoOk` = valid AND top-10% of unseen set avg > 1x after 3% costs (early.js:216) — auto-entry only when the top slice actually made money.
- **Acting** (act early.js:253-263): `strong` (p ≥ thr10) → front of AI queue (priority `5e11 + p*1e6`); `auto` (p ≥ thr5 && autoOk && settings.earlyAuto=true settings.js:61) → `earlyPick()` puts it on the entry watch **before any AI read** (brain.js:220-231) with verdict string "Early signal {cp}s after launch: the launch model gives it a {p}% chance of doubling before it falls 40%." Also a general take-off watch: any launch ≤3h old with ≥20 traders & buys>sells, or ≥10 traders & ≥2.5× off base, is enqueued (early.js:95-106).
- **Winner ledger** (scanWinners early.js:266-278): "real winner" = ATH ≥ $30k (`WINNER_USD` early.js:40) **and** ≥4× start (early.js:283); dashboard measures noticedSecs/flaggedSecs/pickedSecs so you can see whether the radar caught what ran.

### 1c. Research reads (server/research.js) — grading A+..F
- Grade is explicitly **narrative-only** and does not decide the trade: `grade:"A+|A|A-|B+|B|B-|C+|C|C-|D|F"`, score 0-100, "Most coins are C or worse… do not decide the trade" (research.js:282-287, 309-312). Off-scale grades folded (D+→D, E/F±→F) and rejected otherwise (parse research.js:331-348).
- **Two tiers**: quick call (Grok-no-reasoning ~1s or Haiku 3-4s, research.js:375-393) returns `trade.action buy|watch|avoid`, `pWin` 0-100; deep read adds `conviction`, `entry`, `maxEntryMcap`, `takeProfits[]`, `trailingStopPct`, `stopLossPct`, `timeStopHours` (DEEP_EXTRA research.js:321-326). Deep prompt hard-codes the desk rule: "half at 2x, the rest on a 35% trailing stop, everything out at -40%" (research.js:262, 297). Base-rate calibration: "Most coins belong between 5 and 35 [pWin]; above 50 only with strong demand AND a real edge" (research.js:279,307).
- **Traction** (quality.js:173-186) is the real ranker: start 35; vol5m ≥$20k +18 (≥$2.6k +6, else −14); traders ≥40 +10 (≥9 +4, else −8); chg1m >0 +7 / <0 −8; chg5m >0 +4 / <0 −5; chg1h >+130% −5, ≥−2% +10, else −9; mcap $60-250k +10 ($17-60k +2, >$250k −16, <$17k −10); stage near +5 / new −4; holders 60-400 +5; >40% below ATH −8. Fitted 2026-10-01 on 251 reads: <40 wins 7-9%, 65+ wins 50% (quality.js:166-169). Narrative grade showed no such link.

## 2. Alert gating & the pick gate

### Narrative alerts (alertIfStrong research.js:529-539)
Fires a `research` signal only for **grade A+/A/A-**, 6h cooldown per mint (`last.t < 6*60*MIN` research.js:532). Coins failing `verdict()` safety are raised with `safe:false` ("failed safety") and not pushed by default (research.js:534-538; raise engine.js:583-592). Deep read alerts only if the fast read wasn't already A-band (research.js:613).

### Safety verdict (engine.js)
`verdict(t)` = `safetyVerdict` with `minSafety:60` (RugCheck 0-100, no danger risks, settings.js:15) and `minExitLiq:1000` (settings.js:19); `vet()` refreshes price & safety first (engine.js:566-576). This is the single gate every actionable signal passes (engine.js:561-562). Global scorer (scoreToken engine.js:543-558): weighted 0.22·activity+0.16·buyPressure+0.14·accel+0.12·trend+0.12·liq+0.08·mcap+0.10·social+0.06·boost, ×safety factor 0.35-1.0, bonded <$3k liq ×0.5.

### The pick gate (pickGate brain.js:188-217) — the core buy-signal rule
With `aiInGate=false` (default, settings.js:100 — measured: AI "buy" picks won 6%/0.55x, "avoid" picks won 44%/1.45x over 126 picks, brain.js:190-192):
1. `early.strong` (launch model top-10%) → **pick at any size** (brain.js:194).
2. traction ≥ `pickMinTraction`=**60** (settings.js:74).
3. mcap ∈ [`pickMinMcap`=**$15k**, `pickMaxMcap`=**$250k**] (settings.js:76-77).

With aiInGate=true, additionally: not "avoid"; new-coin early rule (stage new or mcap<$15k): ≥`earlyMinTraders`=**25** wallets & buys>sells & (buy or watch with pWin≥`earlyMinPwin`=**20**); fallback score≥`pickMinScore`=**40** when no trade/traction; else buy or pWin≥`pickMinPwin`=**25** (brain.js:202-216).

`considerCall` (deep buy calls, brain.js:94-130): action=buy, conviction ≥ `buyConviction`=**75** (settings.js:101, tunable 55-90), passes `verdict()`, live mcap ≤ `maxEntryMcap`×**1.25**, no duplicate within 24h; plan TPs filtered/clamped (stop 10-90%, trail 10-80%, time stop 1-48h; default ladder 50% @2x + 50% @4x, brain.js:113-117).

### Entry timing (entryBlock brain.js:294-303, checked every 1s via fastTick brain.js:387-411 + per-trade onPickTrade brain.js:379-385)
A rating is not an entry. Block when: >`entryChasePct`=**50%** above rated price; >`entryOffHighPct`=**60%** below ATH; ≤−`entryFallPct`=**5%** in last 15s; chg ≤−15% (3×fallPct) over recent minutes; **≥6 sells in 15s and sells > 2× buys**; watched <3s. Cancel at −`entryCancelPct`=**40%** below rated (brain.js:315); give up after `entryWaitMin`=**10min**. Re-checks safety at the moment of entry (brain.js:353-354) and applies **riskGuard** (brain.js:330-347): `guardLosses`=**5** losing exits in a row → no new picks for `guardPauseMin`=**45min**; day PnL ≤ −`guardDayLoss`=**$600** (at $100/pick) → stop until midnight.

## 3. Exit rules

### The fixed pick rule (stepPick brain.js:420-429; settings.js:82-86)
1. **Take profit**: sell `pickSellPct`=**50%** at `pickTakeProfit`=**2x** (fills at the level if peak reached it between polls).
2. **Trailing stop**: after TP, sell rest at **35%** off the running high (`pickTrailPct`).
3. **Stop loss**: before TP, full exit at −**40%** (`pickStopPct`); stops fill at the price of the check *after* crossing, not the level (brain.js:419).
4. **Time limit**: rest out after `pickMaxHours`=**6h**. Pool emptied → 0 (total loss, illiq flag, brain.js:423, 646-651).
- **Paper cost 3%/side**: `PAPER.costPctPerSide=3` (brain.js:25-26), COST≈0.943 applied to every sale.
- Bonding-curve picks also exit on **every trade as it lands**, not per-second — these coins fall 50% in seconds (brain.js:377-385).
- Graded-but-unpicked coins are run through the **same rule silently** — this is how each score/ranking is scored (brain.js:442).
- Offline **replay** in rules.js: pure-arithmetic `simExit` over price tapes, `BASE_RULE = {tp:2, sellPct:50, trailPct:35, stopPct:40, flatMin:0, maxMin:360, costPct:3}` (rules.js:12) plus a rule grid tp∈{1.3,1.5,2,3}×sellPct{50,100}×trail{20,35}×stop{0,25,40}×flat{0,3,5m}×maxMin{10,30} and 70/30 **walk-forward** rule selection (rules.js:45-73). replayPicks replays pre-rule picks from snapshots (brain.js:451-474).
- AI **call** plans paper-simulated separately (simulate brain.js:609-627): ladder TPs fire at levels, ladder-done→sell unless trailingStopPct, stop, trail after first TP, time stop; 48h expiry closes at market (brain.js:675).

## 4. Scorecard & self-review (brain.js:690-810)
- **scorecard(days)** (brain.js:694-736): graded outcomes ≥6h old bucketed by grade-letter (D/F collapsed), theme, stage, tier, action, organic; per bucket: n, tokens, medianPeak, hit2x/hit5x, dumped (low≤0.5), unsellable, median24h. Closed calls: winRate, avgExit, bankroll, **byPlaybook version** (so rule versions are compared). Narratives scored: ran = best_mult≥3 or best_peak≥$1M, with honest metrics — median coin multiple and lead-coin multiple, not just best (brain.js:982-988).
- **evidence()** (brain.js:488-505): 7-day rolling, 5-min cache; win% (>1.15x) and avg rule-result per traction band [0-40,40-55,55-65,65+], pwin bands, and action buy/watch/avoid — the measured basis for demoting the AI from the gate.
- **Self-review** (REVIEW_SYSTEM brain.js:739-748, review brain.js:771-810): hourly (`startBrain` timers brain.js:1063-1069); requires `reviewMinNew`=**25** graded results ≥6h since last playbook (freshResults brain.js:751). Input: scorecard(14d), best 12 runners, worst 10 dumps, highGradesThatFailed (score≥65 & peak<1.3x), **missedRunners** (peak≥3x & score<60), last 40 calls, narrative results. Output JSON: up to 14 rules, changes, and tuning of exactly two knobs — `buyConviction` clamp **55-90**, `deepMinScore` clamp **35-80** — inserted as new playbook version; rules injected into every read prompt via playbookPrompt (brain.js:77-81). Status surfaced so "waiting for evidence" ≠ "broken" (reviewStatus brain.js:755-768).
- Deep-read escalation `deepMin()` (research.js:17, 585): fast score ≥ `deepMinScore`=**45** (settings.js:67, ~top 10%), or any non-avoid trade call with traction ≥50, or manual — so the expensive lane is spent on would-be-trades.

## 5. Desk-relevant observations
1. **Grade letters are cosmetic**: the code's own measurement (126 picks; brain.js:190-192; quality.js:166-169) demoted the AI and made live traction + the launch model the actual decision inputs. If your desk weights an LLM's A/B/C grade, note BLIX measured that it was anti-predictive on 30-min horizons.
2. **Entry hygiene is a first-class rule** (chase/fall/off-high/seller-flood blocks at 1s resolution, per-trade for pre-bond) — a rating alone never triggers an alert entry; the worst trades it models are exactly "buy after it ran" and "buy while dumping".
3. **The exit rule is deliberately fixed and replayed identically over non-picked coins**, so all thresholds are scored apples-to-apples and the self-review can only touch two scalars — a strong anti-overfit pattern worth copying (walk-forward grid in rules.js:63-73).
4. **Model validation gate**: the early model switches itself off unless it beats the base rate 1.5× on held-out newest 30% and its top-10% made money after 3% costs before being allowed to pre-empt the AI (early.js:209-216). Also honest metric choice on narratives (median & lead multiple, not best; base ≥$5k to avoid div-noise, brain.js:978-988).
5. **Circuit breakers**: 5 straight losing exits → 45-min pause; −$600 on the day → stop until midnight; minExitLiq $1000 ⇒ anything under is "unsellable" and scored as total loss.
6. Caveat: thresholds are fitted on small in-house samples (251 reads for traction, 126 picks for the gate) and re-measured live; the paper sim assumes full exit at quoted mcap (no depth), which flatters the rule on illiquid coins — only partially mitigated by the minExitLiq/illiq handling.

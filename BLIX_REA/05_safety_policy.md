# BLIX Safety/Quality Policy — Deep REA Analysis (server/quality.js & gate chain)

Scope: `server/quality.js`, `price-guard.js`, `price-sanity.js`, `coin-screen.js`, `coin-screen-runtime.js`, `netguard.js`, `request-guard.js`, `metric-budget.js`, `helius.js`, plus the emission site `engine.js` they feed. All paths relative to `/home/agentuser/memecoin-quant/blixtrade`. Evidence: parent bundle `ev_f34217f8fe8a858272fabb362cfb1071b616051067d6ec34686b0500b0eea2c4`; every claim cross-checked against source by direct file read (all line numbers verified).

## 1. The one policy: "one policy gates every positive signal"

The stated policy comment is `engine.js:561` — *"The one safety policy every actionable signal goes through (RugCheck, liquidity, data quality, dev history)"*, implemented as:

- `engine.js:562` — `export const verdict = (t) => safetyVerdict(t, { minSafety: settings.minSafety, minExitLiq: settings.minExitLiq, devCount: t?.creator ? devLaunches.get(t.creator) || 0 : 0 });`
- `engine.js:563` — `const isSafe = (t) => verdict(t).ok;`
- `quality.js:70-88` — `safetyVerdict(t, { minSafety = 60, minExitLiq = 1000, devCount = 0 })` is the single gate function, pure, no I/O.

### 1.1 Every check inside the gate (`quality.js:71-88`), in order

| # | Check | File:line | Trigger / failure reason string | Data source |
|---|-------|-----------|--------------------------------|-------------|
| 1 | Unknown coin | quality.js:73 | `unknown coin` | — |
| 2 | Dead / no recent trading data | quality.js:75 (`t.status === "dead"`) | "recent trading data is unavailable; activity needs rechecking" | tokens.status |
| 3 | Quarantine (bad price reading) | quality.js:76 (`t.quarantine`) | "price data is unreliable: …" | engine.js:334-345 `readingProblem` → `quarantine` column |
| 4 | Sellable liquidity floor | quality.js:77 → `illiquid(t, minExitLiq)` (quality.js:12) | "reported pool liquidity $X, below your minimum of $Y" — only for pooled (non-curve) coins with a known finite liquidity number | DexScreener pair liquidity |
| 5 | Not a memecoin | quality.js:78 | "not a memecoin (stable/wrapped/…)" | `classifyAsset` quality.js:55-66 |
| 6 | Safety not checked yet | quality.js:79 (`!s`) | "safety has not been checked yet" — **unknown is never a pass** (comment quality.js:70) | RugCheck report JSON |
| 7 | RugCheck `rugged` flag | quality.js:81 | "RugCheck marks it as rugged" | `s.rugged` |
| 8 | RugCheck danger risks | quality.js:82-83 (`s.danger > 0`, lists first 3 risk names) | "N danger risks: …" | `s.risks[].level === 'danger'` |
| 9 | RugCheck score floor | quality.js:84 (`(t.safety_score ?? 0) < minSafety`, default 60) | "safety N/100, below your minimum of 60" | RugCheck `s.clean`/score, written by engine.js:531 |
| 10 | Serial launcher | quality.js:86 (`devCount >= 4`) | "the dev launched N coins in 6 hours" | `devLaunches` map, engine.js:34-35, 47, 165 (6h sliding log, engine.js:45-58) |

Verdict assembly: quality.js:87 — `{ ok: reasons.length === 0, unknown: !s, reasons }`. **All failures are conjunctive-rejecting**: any single reason sets `ok = false`.

Null-score handling at engine.js:530: `const score = s.unindexed && before.graduated ? null : s.clean;` — a bonded coin RugCheck hasn't indexed gets `safety_score = null`, so check 9's `?? 0` fails it (quality.js:84). An empty RugCheck report can never read as clean. Curve coins keep their report since the curve itself is the market (engine.js:528-529 comment).

### 1.2 Every positive-signal emission path passes through `isSafe`/`verdict`

All positive alerts are raised in `rescore()` (engine.js:597-648):

- engine.js:599-600: `const meme = (t.asset_class || "meme") === "meme"; const score = meme && !t.quarantine ? scoreToken(t) : 0;` — non-meme and quarantined coins score **0**, so no score-threshold signal can fire.
- engine.js:606: `const safe = isSafe(t);`
- **Graduated** alert (engine.js:612-616): requires `pendingGraduations.has(t.mint) && t.safety_checked` **and** `if (safe && score >= 50 && vol_h1 >= 30000 && buyShare > 0.5)` — safe is the first conjunct.
- engine.js:617: **`if (!safe && !found.length) return;`** — the hard backstop. If the coin is unsafe, the only things that can have entered `found` are graduation alerts (which already required `safe`), so `found` is empty and the function returns before the launch/volume/momentum/milestone blocks (engine.js:619-635). No positive signal can be built for an unsafe coin.
- **Launch** (engine.js:619), **volume spike** (623), **momentum** (626), **mcap milestone** (630-635) — all below the 617 gate, all executed only when `safe` is true.
- Positive alerts are the ones actually pushed: `raise()` (engine.js:585-592) emits on the `bus` (`bus.emit("signal", …)`, engine.js:591) with `safe = 1` by default; the `safe: false` path (engine.js:583-584 comment) exists only for *raw wallet activity* records ("marks an alert the coin did not earn through the safety policy"), which are `hidden = 0` but **"shown as unscreened, never as an approved opportunity, and not pushed by default"**.
- Retroactive enforcement: `rebuildFromSnapshots()` engine.js:751-758 re-files any `wallet:%`, `smart`, `fomo`, `research` signal whose coin fails the RugCheck part of the policy today as `safe = 0` with a `why_unsafe` reason — the policy is enforced on history, not just at emission.
- Warning kinds (`dump`, `dev-sold`, `rugged`, `liq-pulled`, engine.js:650) are deliberately *negative* signals and are exempt from the `!safe` gate (they fire via `dumpWatch` engine.js:653-665 and `safetyCheck` engine.js:534-537 regardless of safety — correctly, since a rug warning about an unsafe coin is the point). They are excluded from the positive-signal dedup query (engine.js:639-640) and from outcome tracking (engine.js:675).

### 1.3 The supporting check chain feeding the gate

**Believable price / quarantine (`readingProblem`, quality.js:19-31).** Rejects a market reading when: mcap not >0 (passes only as "nothing to say"); mcap > $2e11 (or $5e12 for non-memes) or < $100 → `"implausible market cap"`; curve DEXs exempt (quality.js:24, `CURVE_DEX` set quality.js:9 = pumpfun/meteoradbc/bags/launchlab/moonshot/boop/heaven); unknown liquidity passes (no claim to check); pool liquidity < floor ($1000) → "pool has only $X of liquidity"; mcap/liq ratio > 2000 with liq < $100K → "market cap is not backed by the pool". Applied at every write: `applyPair` engine.js:334-338 (sets `quarantine`, counts `stats.quarantined`, and if a previously ≥5×-floor pool collapses below the floor the mint enters `pulled`), `rebuildFromSnapshots` engine.js:727, `paper.js:73`, `brain.js:281,646`, `pulse.js:150`. Quarantined coins then fail the gate (check 3) and are also excluded from: score (engine.js:600), all listing queries (`search.js:124`, `running.js:49`, `desk.js:45`, `narratives.js:60`, `research.js:80`, `pulse.js:520,522`, `brain.js:904,960`), paper pricing (paper.js:62), and devboard figures (devboard.js:54).

**Rationale is incident-driven, not decorative** (comments): the $3-trillion-on-$13-liquidity reading (quality.js:15-16), XFUN's $823K quote against a $2.5K pool (engine.js:287-289), the 83/152 Migrated rows stuck at 10-400× market cap (engine.js:327), DexScreener quoting $1 liquidity for 10-16 minutes after migration (engine.js:322-324, grace-window check engine.js:328-331).

**Liquidity-pulled detection.** engine.js:337: a coin whose pooled liquidity was ≥ 5× `minExitLiq` and now reads below `minExitLiq` → `pulled.add(mint)`; `dumpWatch` (engine.js:658-664) turns it into a `liq-pulled` alert **only if the radar had called the coin in the last 24h** (`called` query engine.js:661), once ever per coin (`cooldownOk(mint,"liq-pulled",1e12)`), with the message "Holders cannot sell at the quoted price; treat any price shown as meaningless". Outcome tracking records unsellable checkpoints as `0` and flags `illiq = 1`, never counting them toward peak (engine.js:668-688; `exitState` quality.js:34-40: `ok:false` for quarantine/illiquid/no market).

**Serial launcher.** `devLaunches` counts each creator's launches in a rolling 6h window (in-memory map engine.js:35 + persisted `launches` table engine.js:38-58, restored on boot engine.js:45-48; decremented on expiry engine.js:252). ≥4 launches → gate failure. Same-wallet pattern memory matches the desk's AEGIS rule (research rule #19: "NEVER trust a clean contract from a dirty wallet — the PATTERN across launches is the tell"), though BLIX tracks *launch count* only, not funding-wallet clustering (see §3).

**Dev-sold.** Two layers: (a) `safetyCheck` engine.js:534-535 raises a `dev-sold` warning when a watched coin's dev share falls from ≥1% to <0.2% (RugCheck `devPct`), once ever; (b) holders.js:127 classifies `devSold = "all"` when dev balance < 2% of max held, `"part"` when <50%, and `coin-screen.js:7` makes an un-graduated coin with `devSold 'all'` and mcap <$10K a `'risk-filter'` (blocks paid RPC work, not alerts — see §2.5).

**Freshness of the RugCheck report.** engine.js:504-511 re-reads every bonded coin ~3 and ~15 minutes after its pool appears (dev hands out supply early), stale top scorers every 12 min (engine.js:502), never-checked busy coins first (engine.js:500-501), on-screen coins within seconds (engine.js:465-484), and `vet()` (engine.js:566-576) forces a fresh enrich + safety check before judging a coin the radar just heard about. Rate-limited to RugCheck's 40/min (engine.js:465-466, 512, 517).

**Per-reading price accept guard (`price-guard.js:1-14`).** Rejects non-finite/≤0/>1e8 prices (line 4); rejects stale-slot (out-of-order) prices (line 6); rejects a >20× or <0.05× jump within 10s *unless* it settles back within 0.5-2× of the held value (lines 7-8) — flash-spike/one-print protection on live curve/pool trades.

**Cross-source price sanity (`price-sanity.js`).** Compares the radar's own live cap against DexScreener's tokens-table cap every 30s (comment lines 1-9); only the **inflated** direction counts (a live cap ≥8× DexScreener for 4 consecutive checks = 2 minutes is a suspect, lines 10, 21-24); suspects lapse after 15 min without a fresh comparison (line 27). Action: `liveStats()` shows DexScreener's cap, health page flags it, owner emailed (comment lines 7-9); `livetrades.js:84` exports it. One-directional by design so dead coins aren't revived by stale DexScreener figures (lines 4-6).

**Coin admission to paid work (`coin-screen.js:3-20`, wired in `coin-screen-runtime.js`).** `screenCoin` returns a refusal reason string (or null): `inactive` (emptied pool / dead / aged <$6K mcap), `risk-filter` (dev sold all at <$10K pre-graduation; holders≥15 & top10≥80; dev≥50% after 90s; snipers≥50%; bundlers≥50%; research grade F), `market-cap` / `age` (stretched curve coins need ≥$4K and <400min), `fees` (must show ≥ floor fees: 1 SOL graduated / 0.05 stretched / 0.001 otherwise, with a maturity exemption line 18). This gates *spending RPC credits* (comment line 2: "Browser display grace is intentionally not a licence to spend RPC credits"), not alerts; `allow()` (lines 32-46) always permits looking at a coin the user opened or holds (held-wallet exception verified only via server-side wallet snapshots, lines 61-76; runtime wires DB-verified open paper positions, coin-screen-runtime.js:24).

**Network safety (`netguard.js`).** `publicUrl` (quality.js:106-112) drops localhost/.local/literal-IP hostnames; `netguard.js:16-38` `publicAddress` rejects loopback/private/link-local/CGNAT/multicast/mapped-NAT64 and unparseable addresses; `fetchPublic` (42-82) resolves DNS then pins the connection to a verified public address while keeping TLS hostname, never follows redirects, bounds time (7s) and bytes (3MB); `safeToFetch` (99-106) re-verifies. A launcher cannot point the radar at the home network or metadata endpoints.

**Request/budget guards (operational, not token safety).** `request-guard.js`: per-IP and per-category rate limits with bounded counters and load-shedding (413/429/503, lines 28-59). `metric-budget.js`: token-bucket reserve with priority classes — non-price work is capped at 80% of the daily allowance (line 38), metrics+history at 25% (39), history at 5% (40), 10% headroom kept in `pacedAllowance` (line 25) — so data hunger can never starve the price/execution path that the safety gate depends on. `helius.js` reads Helius Admin API usage every 10 min (line 10) so keys rest before Helius cuts them off (lines 1-3), keeping the enrichment feed alive.

### 1.4 How a coin can never get a positive alert while unsafe — the closed chain

1. A positive alert requires `found.length > 0` after engine.js:617, and every `found.push` for non-graduation kinds sits after `if (!safe && !found.length) return;`.
2. Graduation pushes require `safe &&` their own thresholds (engine.js:615).
3. `safe = isSafe(t)` (engine.js:606) is recomputed each rescore from the live DB row — not cached — so a rug flag, quarantine, liquidity collapse, dev-launch burst or score drop flips it on the next 30s cycle.
4. Every input to `verdict` is itself maintained: quarantine by `readingProblem` at every enrich (engine.js:334), liquidity by the same write (engine.js:348), safety by the freshness loops (§1.3), `devCount` by the 6h launch log.
5. Unknown is fail-closed: no report (quality.js:79), unindexed bonded coin (engine.js:530 → `?? 0` at quality.js:84), unparseable JSON (`parse` returns default, quality.js:4).
6. Signals that bypass the gate (`raise(..., {safe:false})` raw wallet activity) are marked `safe = 0` with `why_unsafe`, stored unpushed (engine.js:583-587), and retroactively re-filed by engine.js:751-758 if the coin fails today.
7. UI/query surfaces exclude unsafe coins from the pools positive signals rank over (`quarantine IS NULL` filters, §1.3), so a coin cannot be re-introduced through a side listing.

Residual gaps (honest): `rescore` runs only on the enrich cadence, so there is a window between a rug event and the next rescore (mitigated by the 30s cycle and the liq-pulled/dev-sold/rugged warnings); `safetyVerdict`'s `devCount` is 6h-windowed — a dev launching on a 7th hour resets the counter; `illiquid()` only checks the *primary* pair DexScreener reports (a real market spread over other pools is not summed); `scoreToken` still applies a soft `safe` multiplier (engine.js:555-556) — score suppression, not the hard gate, for display purposes only.

## 2. Comparison against the desk's RESEARCH.md rules

| Desk rule (RESEARCH.md) | BLIX status | Verdict |
|---|---|---|
| **§13 top-10 >70% = structurally fragile** | RugCheck reports feed `top10` (coin-screen-runtime.js:40) but the **alert gate** has **no top-10 concentration check**. Top-10 ≥80% only triggers `'risk-filter'` in coin-screen.js:8 — which blocks *RPC spend*, not positive alerts. A coin with 95% top-10, clean RugCheck, ≥$1000 liquidity and <4 dev launches gets a full positive launch/momentum alert. | **Weaker for alerts.** |
| **§1 non-`pump` suffix = custom contract risk** | BLIX inverts this: `CURVE_DEX` (quality.js:9) treats pump-suffix curve coins as trusted-by-default for liquidity semantics; custom/non-curve deployments face full pool-floor + mcap/liq checks. No copycat-metadata check. | **Mixed / not implemented.** |
| **§1/§5 wash-trade LPI, volume spike >10-17× with few wallets** | **No LPI check.** Nothing compares buy-size to price-move, or volume spikes to unique traders. `price-guard.js:7-8` rejects >20× 10s jumps (kills one-print, not gradual LPI); `price-sanity.js` catches cross-source cap inflation ≥8× sustained. **But `traction()` (quality.js:177-181) *rewards* vol5m ≥$20K** — it ingests wash volume uncritically. Vol_m5×12 vs vol_h1 >3× (engine.js:623) is a *positive* spike alert with no wallet-diversity condition. | **Weaker — and partially adverse.** |
| **§4 copy-trade ≈20% capture haircut** | `smart`/`wallet:%` signals bypass the gate at emission but are marked `safe:false`, unpushed, and retroactively re-filed. The 20% haircut is not implemented anywhere; closest is `cappedMean(xs, cap=5)` (quality.js:139-140) and fitted traction bands. | **Stricter in gating, silent on haircut.** |
| **§13 fragility composite** | Not computed. Sentiment: absent. Volatility: only chg1h >130% penalty (−5) and price-guard spikes. | **Weaker.** |
| **§10 first-5-minutes rug prediction** | Operationalized in spirit: safety re-reads at +3 and +15 min after bonding (engine.js:504-511), coin-screen risk filters on dev%/snipers/bundlers within first 90s (coin-screen.js:8), credit budget reserves price path. No classifier — static thresholds. | **Partially matched.** |
| **§21 slow-graduation signal** | Not implemented: no curve-fill-time feature in the gate or screen. | **Absent.** |
| **§8 journal every rejection (PRFS)** | Implemented better than the rule itself: every rejection reason counted (`coin-screen.js:44` `reasons[reason]++`), rejected signals stored with `why_unsafe`, `rebuildFromSnapshots` audits history. | **Stricter.** |
| **§9 mechanical brackets** | Enforced structurally: fixed 15m/1h/6h/24h outcome checkpoints (engine.js:683-684), unsellable = 0 + `illiq`, cappedMean caps outliers. | **Stricter in measurement.** |
| **§16 MEV/fee discipline** | Out of scope of these files, but request-guard/metric-budget priority classes protect execution-path latency. | **Consistent.** |

### 2.1 Net comparison

**Stricter than RESEARCH.md:** fail-closed unknowns; sellable-liquidity floor on every signal; quarantine semantics with incident-verified thresholds; liq-pulled detection with 5× collapse trigger; serial-launcher memory (live 6h map vs AEGIS's aspirational note); PRFS-style rejection journaling; outcome measurement that refuses fake liquidity-backed peaks.

**Weaker than RESEARCH.md:** (1) **no top-10 concentration in the alert gate** — the desk's >70% rule is a hard pre-entry veto, BLIX's version only throttles RPC spend; (2) **no wash-trade/LPI fingerprint** — volume-spike alerts can be triggered by the very wash patterns §5 documents (median spike 1,772% during wash events), and traction() *scores volume up*; (3) **no copycat-metadata check**; (4) **no curve-fill-time bot check**; (5) **no 20% copy-trade capture haircut** in any EV computation; (6) dev-launch memory is 6h-windowed and funding-source-blind vs "cluster creators by funding source, not just address".

**Overall:** BLIX's gate is a *sellability + RugCheck + data-integrity* policy — it guarantees you never get told to buy a coin you cannot exit or that has a known rug flag — while the desk's research rules target *manipulation economics* (who holds it, was the move manufactured, what can a copier actually capture). The two are complementary; concrete upgrades for BLIX would be promoting top-10 ≥70-80% from `risk-filter` (credit gate) into `safetyVerdict` (alert gate), and adding a volume-per-unique-trader condition to the volume-spike signal.

## 3. Verification notes

All 9 files read in full; every cited line confirmed by direct read. The chain claim in §1.4 was validated by control-flow trace of `rescore` (engine.js:597-648) plus the retroactive refile (751-758); no emission path found that reaches `bus.emit("signal")` with `safe=1` while `isSafe` is false. Evidence ID: `ev_f34217f8fe8a858272fabb362cfb1071b616051067d6ec34686b0500b0eea2c4`.

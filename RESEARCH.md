# Memecoin Quant — Research Base (arXiv reading notes)

Reading list provided by user, learning phase. Each entry: paper → actionable rule for the $100 bankroll desk.

---

## 1. Meme Coin Factories (2609.10246, Szwajcok et al., CS@CMU/EPFL)
pump.fun manipulation at scale: all 15.2M coins, 87M txs, 2yr window.
**5 manipulation classes:**
1. **Wash trading** — self buy-sell to inflate volume; ≥17% of ALL trading transactions; can boost graduation probability up to **10x**
2. **Creator address obfuscation** — top-1% creator funding groups create **58.6% of all coins** (cluster creators by funding source, not just address)
3. **Coordinated sell** — multi-address accumulation → single-address dump; ~8,000 instances detected
4. **Copycat coins** — ≥10% of all coins duplicate name/symbol/desc/images of real coins; 26.5% of copycats deployed via custom contracts (no "pump" address suffix) vs 16.05% baseline
5. **Social media manipulation** — bot comment clusters posting within 0.1–1s of each other; 23.5% of coins created after Twitter/Truth posts; 31 posts earned creators ≥$1M each
**MMaaS** — Market-Manipulation-as-a-Service tools commoditize all of the above.

→ **Desk rules:**
- Address suffix check: token mint NOT ending in "pump" (for pump.fun lineage) = custom contract = higher automation risk
- Duplicate metadata check: name+symbol+description+image identical to an existing coin = copycat, skip
- Comment timestamp clustering: same wallets posting across many coin pages within 1s = astroturf signal

## 2. From Viral to Void (2608.01609) — rug pull fingerprints (ETH-focused, transferable)
Stage fingerprints: deployment (hidden taxes/blacklists/upgradeability), liquidity injection (inflated, no lock → L_health=0), fake trading (uniform intervals: **CV<0.3, autocorrelation ρ1>0.9** = self-trading), capital withdrawal (withdrawal rate >90%, aggregation speed S_collect >10 units/min).
Modern rug: full deploy→launder loop in <48h.
→ Check LP lock status, withdrawal rate after pumps, trade-interval uniformity.

## 3. MemeChain (2601.22185) — 34,988 coins, 4 chains
- **One-Day Meme Coins**: lose all transactional activity within 24h — majority of launches
- Web infrastructure = viability proxy: many launch with NO website; sites abandoned right after liquidity event
→ Website dead/missing + token still "trending" = red flag.

## 4. Resisting Manipulative Bots in Copy Trading (2601.08641, WWW'26)
Multi-agent LLM (coin eval / wallet selection / timing) vs manipulative bots.
- **Smart-money wallets: ~14% avg return per meme coin; copiers capturing the same wallets: ~3%** — copy trading captures ~1/5 of the edge, and only after you account for frictions
- Trader profitability is **predictable** from structured features (bot presence, historical performance, experience)
- Solana: 400ms blocks, no public mempool — only infrastructure-level players can front-run
→ **Desk rule: never trust raw copy-trade screenshots; expected copier capture ≈ 20% of shown smart-money P&L.** Vetting = bot-presence features + consistency, not one flashy trade (matches your FOMO-verification rule).

## 5. A Midsummer Meme's Dream (2507.01963, Mongardini & Mei) ⭐
34,988 tokens, 4 chains, 3-month longitudinal. The big one.
- **82.8% of high-return tokens (>100%) show artificial growth strategies** — a big gainer is more likely manipulated than organic
- **LPI = Liquidity Pool-Based Price Inflation**: small strategic purchases trigger dramatic price increases (buy-size vs price-move ratio is anomalous)
- Sequential: 62.9% of profit-extraction events (86.67% of delisted) followed prior wash trading/LPI
- $9.3M+ losses, 17,000+ victim addresses
- Wash trading: **zero-risk position** method detects 96.17% of wash-traded coins; median volume spike during wash events = **1,772%** vs prior day; only a few participants; avg 3.63 operations per token; Solana targeted earliest (median 61 days)
→ **Desk rules:**
- Suspicious price move per dollar of buy volume (LPI fingerprint) → treat as manipulated
- Volume spike >~10-17x previous day with few wallets = wash trading suspicion
- A token that pumped >100% without prior wash/LPI is the exception, not the rule

## 6. Coin-Meme multimodal (2412.04913, CUHK)
3,751 pump.fun→Raydium coins. Market entry time (launch→Raydium) + MC + text/visual/community clusters.
- Fast market entry (politically charged cluster) = quicker adoption but lower long-term stability
- Community comment volatility reveals bot activity (stable sentiment = controlled bots)

## 7. Inscriptions boom (2405.15288)
EVM L2 stress test; inscription meme coins = up to 90% of txns; 99% minting. Fee dynamics differ L1 vs L2.
→ Context: infrastructure stress + fee spikes happen in meme booms; on Solana this is why priority fees explode in dumps (your failed-sell risk).

## 8. Sniper Cohorts & Filter Rejections (2609.18975, Kamat) ⭐ methodology
- **Lifecycle separation is structural**: sniper-cohort detection (bonding curve) vs filter rejections (post-graduation venues) share <1% of tokens — don't mix pre/post-graduation signals
- Cohort detection: co-occurrence graph clustering + union-find + composite score (launch coverage, avg first-buyer rank, SOL committed)
- **Graduation rates: ~0.63% (Sep-Oct 2025, 655K launches) falling to 0.198% (May-Jun 2026)** — composition shift to zero-effort launches
- **PRFS = Post-Rejection Follow-up Sampling**: track what happens to tokens your filter rejects → build a precision audit of your own rules
→ **Desk rule: journal every rejected candidate too (not just trades). That's how we measure our own filter precision.** RED-2400 = public benchmark of 6,660 rejected events with outcome trajectories.

## 9. What LLM Trading Agents Actually Do (2609.05663) ⭐⭐
Two production fleets (DX Terminal Pro: 3,505 vaults real ETH on Base memecoins; DXAP: Hyperliquid perps, 500-599 agents).
- **No directional edge**: DXAP cumulative -$217K; 15% of agents net-positive vs 53% of retail; 41% win rate vs 50% retail
- **Operating layer determines behavior more than model reasoning**: a slider fixed leverage while volatility varied 5.7x; a render boundary moved selection 1.75x; one posture cell held 62% of liquidations
- **A two-line bracket beat every discretionary exit policy measured**
- Fleet leans long (78%), uses 4-5x retail's effective leverage
- **Horizon edge: positions held 24h+ earn +0.23% median vs -0.15% for sub-1h** — over-trading is the leak
→ **Desk rules:**
- Pre-commit to mechanical brackets (matches your 50%@2.1x auto-TP playbook — validated)
- Don't size by vibes: scale to realized volatility
- Hold through at least 24h when thesis intact; churn kills small accounts

## 10. Catching the Rug (2608.20271) ⭐
6.4M Solana tokens, 7 months. **XGBoost on the FIRST 5 MINUTES of trading data predicts 1-hour rug outcomes** — tabular liquidity/trading dynamics only, no contract code.
- Vast majority of Solana memecoins exhibit rug characteristics within 1 hour of launch
- Solana rugs = liquidity manipulation + social dynamics (not ETH-style contract backdoors)
- Cross-platform transfer (PumpFun→Raydium) has real domain shift; multi-source fusion + trees = most robust
→ **Desk rule: the first minutes of trading carry outsized predictive signal; early liquidity/tx patterns are the feature set, not the chart narrative.**

## 11. MELT (2602.13480) — TRUMP-cycle launch traces
Behavioral traces Dec 2024–Mar 2025 anchored on $TRUMP (Jan 17, 2025).
- **<2% of memecoins ever reach DEX migration**
- Integrating risk prediction into trading cut losses by **up to 34 percentage points**
→ Pre-migration risk models pay; your $150K–$1.5M zone already excludes 98%+ of launches.

## 12. Memecoin Phenomenon (2512.11850)
Pump.fun = up to 71% of all Solana tokens minted; 3M tokens by Nov 2024, 7 tokens/min. Q4 2024 dynamics.

## 13. Measuring Memecoin Fragility (2512.00377, ME2F) ⭐
**ME2F = 3 scores: VDS (volatility dynamics), WDS (whale dominance), SAS (sentiment amplification).**
- Top-10 holders control **70–80% of supply** in many high-return tokens; TRUMP/MELANIA single wallets 80–90% at launch
- Fragility stratification: political/narrative tokens most fragile → established memecoins → benchmark tokens resilient (LIBRA = extreme case, >120% volatility)
- Memecoin volatility spills into base layer (SOL volume surged $33.2B with TRUMP's $39.1B day)
→ **Desk rule: compute fragility = volatility persistence + top-10 concentration + sentiment instability before entry. >70% top-10 = structurally fragile.**

## 14. Hour-Aware Adaptive Risk Management (2606.08232)
15-day paper deployment, 190 trades: 40.5% win rate, +0.62% mean per-trade, cumulative +117.7%, skew -1.21, excess kurtosis 6.61 (fat tails + negative skew = occasional huge losers).
- Hour-of-day return patterns exist on 24/7 AMM venues (consistent with informed-flow prediction)
- Counterfactual rejection tracking (PRFS family) = the measurement standard
→ **Desk rule: log time-of-day for entries/exits; memecoin returns are fat-tailed and negatively skewed — stop-losses and clip structures exist precisely because of this.**

## 15. CoinCLIP / CoinVibe (2412.07591)
CLIP image-text + community data classifies viability; 6,231 coins, 44.27% viable. Community engagement (comments, timestamps, likes) materially improves prediction → social engagement velocity is a real feature.

## 16. Don't Let MEV Slip (2309.13648, Uniswap v3 costs) ⭐ execution
534K swaps, ~$6B volume. Cost components (gas/slippage/LP fees/price impact) each 20–35% of total transaction cost.
- **Private RPC services effectively eliminate adversarial slippage** — academic validation of MEV protection
- Slippage decomposes: adversarial, collision, reordering, top-of-block, liquidity
→ **Desk rule: your $0.15–0.20 priority fee + private RPC config is the academically-validated way to kill sandwich/MEV drag. Fee drag dominates at $15–20 size — keep the 3-clip structure.**

## 17. Memes, Markets, Machines (2410.23794)
Zerebro / AI-agent memecoins, hyperstition (self-fulfilling narrative), on-chain autonomy. Category awareness: AI-agent tokens have their own dynamics (model-collapse risk in content).

## 18. Yoda Playbook — "Quick Profits, Conviction Holds" (GMGN user story, source of desk style)
- Quick profits default; hold LONGER when conviction in narrative. Get in early, sell at right time, minimize losses.
- NOT worried about holder concentration: "If it's good, it'll go up." Focus on own trading.
- $NOXA: bought platform's official token at $30K MC on chain-attention narrative → $250K realized, hit $20M MC.
- $SEXFLY: strong meta (fly brain control), tech actually WORKED (bees sex), $750 entry, scaled out as it went up, saw FOMO + volume pouring in → held longer, exited at $3M MC for $17K.
- Edge: something NEW/different/fresh narrative, or tech that genuinely works, or creator clearly put time in.
- SKIP farms: team launching tech but dumping everything at $20K MC — visible on GMGN early-buyer selling.
- Recycled narratives OK when volume across chains is everywhere. "It's about the money, not your opinion."
- AUTOPILOT EXITS: narrative isn't that good, volume drying up, good wallets starting to sell → exit. If truly believe narrative → hold (may take loss).
- Chains play different: Solana = degen meta; Chinese narratives run harder on BSC; wallets tracked on Robinhood ≠ good on Base/BSC. Avoid gambles on Solana; higher-conviction plays.
- Consistency > big swings. Screen time builds instinct.
- → Desk rules: (1) conviction mode = hold through dips while narrative+volume strong; (2) exit triggers: narrative dead / volume drying / good-wallet selling; (3) skip any call where early buyers are already dumping at low MC (check first-hour holder flow); (4) per-chain adaptation; (5) FOMO inflow + rising volume = scale out SLOWER, hold longer.

## 19. AEGIS — Scam Filter with a Second Brain (concept)
- Six on-chain checks: mint, freeze, top-10 concentration, bundle detection, LP lock, metadata.
- Writes every scan to local SQLite: deployer, wallets, verdict, status (live/rugged).
- Score 0-100 adjusted ±25: deployer with 3 rugs starts at -15 BEFORE checks; known-rugger in first buys knocks 10.
- NEVER trust a clean contract from a dirty wallet — contract is a snapshot, always clean; the PATTERN across launches is the tell: same funding wallet, same 3 bot wallets in first block.
- Runs locally, sharpens over time.
- → Desk rule: maintain deployer/wallet memory ledger; before buy (or right after, since we buy all calls): score deployer; known-dirty → shorter hold, tighter clips, watch for same-block bot exits.

## 20. Behavior Indexing (rug deployers / KOL / smart wallets / whales)
- Goal is NOT avoiding their coins — it's choosing the POST-BUY strategy per actor class:
  - Rug deployer launches → fast flip only, tighter stops, expect engineered dump.
  - KOL called token → front-run KOL exit; watch their sell.
  - Smart wallets accumulating → hold longer, conviction mode.
  - Whale dumping into green → clip into strength (research: whales dump 85-95% of round MC).
- Track: funding wallets, first-block bot wallets, known-rugger ledger, smart-money consistency, whale positions.

## 21. Slow-Graduation Signal (bonding-curve fill time, 97,146 graduations)
- Median token fills curve in 20 min. 3-min fill = BOTS (cluster pushed curve closed on own inventory). 6-hour fill = PEOPLE (separate wallets, own money).
- Slow fills: 1.5× more likely to double after graduation, 2.4× more likely to 5×. Held out-of-sample, stronger in test period.
- Implementation: Helius webhook on PUMP_AMM (pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA) + CREATE_POOL; filter events with pump.fun program (6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P) + mint ending 'pump'; page getSignaturesForAddress backwards to age cutoff; queue events off request thread; Telegram alert with baseline comparison (median 0.3h). ~4,000 events/day, ~1/4 real graduations. Free tier: ~270K Helius credits/month.
- → Desk rule: graduation fill time = filter for call intake; slow-fill graduations deserve longer holds (conviction), fast-fill = suspect bots, flip fast or skip.

## 22. Telegram channel intake (TESTED Oct 15 2026)
- t.me/s/<channel> public preview pages ARE parseable without login (poll ~last 20-40 messages, extract mint/MC/symbol/wallet/age).
- Working: sol_trend (27 msgs), solwhaletrending (20), solearlytrending (20), PIRBViewTrending (25), soltrenchtrending (20), SeekrTrending (34). NOT working: signalsolanaby4am (empty preview — likely private/invite).
- Real-time push requires MTProto session/bot admin rights (user account) — polling preview pages is the practical path.
- → Desk rule: poll channels every 30-60s, dedupe by mint, extract CA+MC, feed pipeline: forensic score → AEGIS memory → position monitor.

---

## Consolidated desk rules (research-backed)
1. **Gainers are guilty until proven organic**: 82.8% of >100% pumps show manipulation (wash/LPI). Verify volume quality (buy size vs price move, wallet count) before riding.
2. **Address + metadata forensics**: non-"pump" suffix custom contracts, duplicate metadata (copycats), funding-clustered creators = skip.
3. **First-5-minutes signal**: liquidity/tx patterns in the first 5 min predict 1h outcomes (XGBoost-grade, no code needed). Use early tape quality as a feature.
4. **Top-10 concentration >70% = structurally fragile** (ME2F). Check holders before entry.
5. **Copy traders capture ~20% of shown smart-money edge** — vet wallets on consistency/bot-features, never one flashy trade.
6. **Mechanical brackets beat discretionary exits** (validated in production LLM fleets; your 50%@2.1x auto-TP is right).
7. **Hold ≥24h when thesis intact** (+0.23% vs -0.15% sub-1h). Churn is the leak.
8. **Journal rejections too (PRFS)** — that's how we audit our own filter precision.
9. **MEV protection via private RPC + capped priority fees** = validated execution hygiene.
10. **<2% of launches reach DEX** — your $150K+ zone is already the top 2%; filter quality there decides everything.

## Open threads / tools to build later
- Wash-trade / LPI detector on live tape (buy-size vs price-move ratio, interval CV<0.3)
- Copycat detector (metadata fingerprint diff)
- PRFS-style rejection journal → filter precision audit
- Fragility score (top-10% + vol persistence + sentiment instability)
- XGBoost-style 5-minute early-warning model once enough journal data exists

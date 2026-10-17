# AEGIS Coverage Map — Your Metric Framework vs What the Desk Actually Computes

Legend: ✅ = fully computed · 🟡 = partial / proxy · ❌ = not computed · 🔌 = tool exists, not wired into AEGIS

---

## 📊 Liquidity & Trading Metrics
| Metric | Status | Where |
|---|---|---|
| Liquidity-to-MC ratio | 🟡 raw liq + MC stored; ratio not derived/flagged | scan_log.market |
| Pool Liquidity Depth | ✅ liq_usd from DexScreener/Gecko | scan_log.market |
| Volume-to-Liquidity ratio | 🟡 vol_h24 + liq stored; vol/liq not derived (wash check uses it indirectly) | signals.wash_lpi_check |
| Slippage tolerance @ size | ❌ not computed (Jupiter quote tool exists for it) | jupiter_estimator.py unused here |

## 👥 Holder Structure & Concentration
| Metric | Status | Where |
|---|---|---|
| Top-10 holder concentration | ✅ top10_pct, flagged >70% | scan_log.risks |
| Gini coefficient | ❌ (top10 is the proxy) | — |
| Bundled snipers % | 🟡 Rugcheck risks[] sometimes carries it; not always present | scan_log.risks |
| Unique wallet growth rate | 🟡 holders count snapshotted per scan; trend across scans derivable but not computed | scan_log.risks.holders |
| Holder retention / churn | ❌ no wallet-hold-duration tracking | — |

## ⛓️ On-Chain Momentum & Flow
| Metric | Status | Where |
|---|---|---|
| Smart Money Net Flow | 🟡 fomo_overlay() tags smart-money holders at call time (wire 🔌 into score as modifier) | aegis.fomo_overlay |
| Buy/Sell imbalance 1m/5m/1h | 🟡 Bitquery imbalance (30-tx window) + WS momentum (8th check, live) | desk_common.bitquery_imbalance + ws_momentum |
| DEX-to-CEX volume ratio | ❌ not computed | — |
| New wallet purchasing power | ❌ not computed | — |
| Live tape momentum (bonus) | ✅ WS 8th check: buy/sell USD ratio, ±10 score | aegis 8th check |

## 🛡️ Smart Contract & Security
| Metric | Status | Where |
|---|---|---|
| Mint / freeze authority | ✅ Helius + Rugcheck double-checked, −25/−15 | scan_log.authorities |
| Honeypot / tax | 🟡 transfer_fee present check (Solana-native; Ave honeypot is EVM-only, usually null on Solana) | scan_log.risks.transfer_fee |
| LP burn / lock | ✅ lp_locked_pct, flagged <50% | scan_log.risks |
| Copycat/duplicate metadata | 🟡 Rugcheck risk name scan | scan_log.risks |

## 📣 Social & Sentiment
| Metric | Status | Where |
|---|---|---|
| Social volume / NLP sentiment | ❌ no social APIs wired | — |
| KOL co-occurrence | 🟡 FomoAPI leaderboard/users exist (fomo_user, fomo_board) — not wired into AEGIS score | desk_common 🔌 |
| Social-to-price lead time | ❌ not computed | — |

## 📉 Derivatives & Risk
| Metric | Status | Where |
|---|---|---|
| Realized volatility (hourly) | ✅ ohlcv_risk() computes realized vol from DEXpaprika candles | signals.ohlcv_risk |
| Max drawdown | ✅ computed in same ohlcv pass | signals.ohlcv_risk |
| Fragility (ME2F-style) | ✅ fragility_score() — top10+liq+vol composite | signals.fragility_score |

---

## Score
- **Fully computed: 8** (depth, top10, mint/freeze, LP lock, vol-derivative, MDD, fragility, WS momentum)
- **Partial/proxy: 8** (liq/MC ratio, vol/liq, snipers, holder growth, smart money, B/S imbalance, honeypot/tax, KOL)
- **Missing: 6** (slippage sizing, churn, DEX→CEX, new-wallet power, social volume/lead time)

## Cheapest wins (tools already on the shelf)
1. **liq/MC + vol/liq ratios** — pure arithmetic on data already in scan_log → 2 flags, ~10 lines
2. **Smart-money overlay into score** — fomo_overlay already runs at call time; wire ±5
3. **Holder growth** — compare `holders` count across scans in DB (delta per hour)
4. **Slippage check** — jupiter_estimator.py exists; 1 quote per candidate = real slippage at $20 size

Missing entirely (need new data sources): social volume/sentiment, DEX→CEX flows, churn, new-wallet purchasing power. Nansen (wallet labels) + Dune (flows SQL) are configured but credit-locked.

# Solana-Forensic-Analysis-Tool — Deep Analysis

Repo: `apostleoffinance/Solana-Forensic-Analysis-Tool` (clone at `/home/agentuser/memecoin-quant/Solana-Forensic-Analysis-Tool`). No REA analyzer exists for Python — analysis is a full manual read of all `src/` modules, `app.py`, README. All line numbers from the clone.

## 1. What the tool does
Single-wallet forensics web app: POST a Solana address → pulls tx history → enriches transfers with USD prices and entity labels → builds sender→receiver graph → clusters connected wallets → flags suspicious clusters → renders wallet stats + interactive graph (Next.js frontend). Pipeline: `app.py:39-83`. Backend is **Flask** (`app.py:92-182`, port 5025, ThreadPoolExecutor 4 workers) — not FastAPI as README claims. Jobs cached by address (`app.py:110-117`).

## 2. Architecture
- `/api/analyze_address` (POST, async 202), `/api/get_results` (poll), `/api/clear_cache`.
- **data_fetching.py** — Helius RPC (`getSignaturesForAddress` paginated :21-76; Enhanced v0 tx API batched 100 sigs :78-105), Flipside Crypto SQL (:250-300), Vybe known-accounts/programs (:170-216), Metasleuth batch address labels (:223-248).
- **sql.py** — Flipside: hourly prices→daily (:22-45); wallet SOL+token balance timeseries (:49-67, LIMIT 10000).
- **data_processing.py** — parse Helius txs into per-transfer rows (:135-229), join balance timeseries (:232-258), daily-price join (:298-317), summary (:319-365).
- **entity_labeling.py** — label merge + heuristic classification.
- **clustering.py** — NetworkX connected-components + risk flags.
- **transaction_flow.py — empty file (0 bytes)**; graph built by `build_tx_graph` in data_processing (:49-91).

## 3. Data sources
| Source | Use | Where |
|---|---|---|
| Helius RPC | signature pagination ≤20 pages × 100 sigs; tx decode 100/batch | data_fetching.py:21-105 |
| Flipside Crypto (Snowflake) | balance history, daily prices | sql.py:3-67 |
| Vybe Network | known accounts + program labels | data_fetching.py:170-216 |
| Metasleuth | batch address labels, 100/req | data_fetching.py:223-248 |
| Flipside CSV dumps | CEX, chainadmin, dapp, DeFi labels | entity_labeling.py:25-35 |

**Limit**: tx history caps at 2,000 txs (max_pages=20); balances LIMIT 10000 — high-volume wallets silently truncated.

## 4. Detection algorithms — exact thresholds

### 4.1 Heuristic entity labeling (entity_labeling.py:15-23) — core classifier
Per wallet from send/receive stats (:133-154):
- **Exchange** (:16-17): sent>100 AND received>100 AND (unique_receivers+unique_senders)>50.
- **Project Wallet** (:18-19): sent>50 AND received<10 AND unique_receivers>30.
- **Suspicious** (:20-21): sent>10 AND received>10 AND total_sent<0.01 (token units, not USD — dust fan-out heuristic; misfires on tiny-denomination tokens).
- Else **Normal User**.

**Bug**: `receive_stats` groups **by `sender`** not `receiver` (:143) — received-side stats mis-keyed, thresholds evaluated on corrupted counts. `total_received` never populated.

### 4.2 Keyword taxonomy (entity_labeling.py:66-92)
Exchange = openbook/raydium/orca/jupiter/meteora/coinbase (:70-71); NFT Trader = magic eden/nft/tensor (:74-75); **Rug Pull = 'rug','bricked','scam','meme'** (:78-79 — any memecoin-labeled entity becomes "rug pull"); Bridge = allbridge; Airdrop = airdrop/pengu; DeFi = uxd/solend/kamino/parcl etc (:88-89). Label precedence: Flipside > Metasleuth > Vybe (:107-111).

### 4.3 Clustering (clustering.py:15-116)
Undirected nx.Graph from sender→receiver edges; **clusters = connected components** (:17-26). README's "graph-based DBSCAN clustering" is **false** — no DBSCAN, no weights, no time windows. Everything connected in the fetched window collapses into one giant component in practice.
Flags (:80-106): `Dense Small Cluster` density>0.3 AND size<5; `High Tx Rate` tx/wallet>5; `Centralized Flow` max_degree/(2×total_tx)>0.5.
**Bug**: final DataFrame build inside the flag loop (:108-112), return value discards it.

### 4.4 Wallet risk flags (wallet_analysis.py:110-145) — DEAD CODE
High volume >100 SOL sent+received (:129-133); >10 tx/day (:136-138); unknown entity (:141-143). **Bug**: calls nonexistent `self.identify_activity_patterns` (:117) → always raises AttributeError; pipeline never invokes it (app.py:61-63 skips it).

### 4.5 Data quirks
Native-transfer dedupe by (from,to,amount) kills legitimate repeats (:150-159); token transfers counted regardless of direction relevance (:176-197); **wSOL mint mismatch**: metadata.py uses `...11112` (correct) while data_fetching.py:135 checks `...11111` — the SOL-symbol patch never matches. README misstates DBSCAN/FastAPI/Dune.

## 5. Methodology assessment
Behavioral-statistical, not provenance-graph: single wallet's ego network, no funding recursion, no peeling-chain detection, no temporal mining, no common-funder/co-spend heuristics (the standard Solana insider technique). Connected components without weights/time windows are meaningless at scale. pyod/xgboost in requirements but no ML anywhere.

## 6. Comparison to desk rules (RESEARCH.md)

**Weaker than desk:**
- **No wash-trade detection at desk rigor** (CV<0.3, ρ1>0.9): only analog is topology flag, no time-series statistics.
- **No volume-spike detection** (10-17× baseline): totals and avg interval only, no rolling windows or z-scores.
- **No holder concentration/fragility** (top-10 >70%): never fetches token holder distribution.
- **No deployer/bundler analysis** (AEGIS memory): never looks at mint authority or deployer funding trees.
- Correctness defects undermine its own thresholds (mis-keyed stats, dead code, wSOL mismatch).

**Stricter / complementary:**
- **Named-entity attribution**: Vybe + Metasleuth + Flipside fusion tags every counterparty (exchange, bridge, known rug) — pure statistical desk rules carry no external entity intel.
- **Ego-network visualization** good for eyeballing fan-out/fan-in on desk-flagged wallets.
- **Centralized Flow** degree-dominance is a graph-side cousin of the top-10 rule — adaptable on a weighted, time-windowed per-token graph.

**Verdict**: prototype/early-career forensic dashboard, not a production engine. Adopt: entity-label fusion + graph visualization. Do not rely on: clustering, behavioral labels (buggy), risk flags (dead code), or any wash/spike/concentration detection (absent). All desk rules must stay in the desk's own stack.

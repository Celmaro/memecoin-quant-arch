# FREE WS / STREAMING RESEARCH (Oct 21 2026) — 19 providers, how to make each work

User asked: look up more free WS on the internet and research how to make these work:
shyft, solscan, coinstats, madeonsol, birdeye, gmgn, cabalspy, GeckoTerminal, DexPaprika,
publicnode, FomoAPI, Bitquery, DUNE, Nansen, Vybe, Onfinality, Chainstack, Codex.io, Moralis.

Method: web research (docs/pricing/llms.txt) + LIVE probes from this box where free.
Legend: ✅ works from this box · 🔶 needs signup/free key · ❌ blocked/paid/dead

## 1. THE BIG WIN — DexPaprika SSE (free, keyless for showcase, free key for all)
- **Endpoint (new, live-probed ✅):** `GET https://streaming.dexpaprika.com/sse/prices?method=token_price&chain=solana&address=<MINT>`
  → SSE stream, event `token_price`, ~1s updates, JSON `{address, chain, price, timestamp, timestamp_price}`.
  **Live probe returned a real SOL price: 106.057** — keyless works for showcase assets.
- **Multi-token:** `POST /sse/prices` up to 25 assets/connection (403 keyless — needs free key).
- **Pool reserves (liquidity stream!):** `GET /sse/reserves?method=token_reserves&chain=solana&address=<MINT>` — reserve deltas + USD
  (403 keyless — needs free key). `pool_reserves` per-pool.
- **Legacy `/stream` (t_p compact) is DEPRECATED** — use `/sse/prices` (token_price) instead. Docs page:
  https://docs.dexpaprika.com/streaming/introduction (or streaming-dexpaprika.mintlify page).
- **Free key:** console.dexpaprika.com — 300,000 credits/mo, no card. Keyless = 50K/mo showcase only.
  Credits: 1 credit per event delivered; connections free. Limits: 10 concurrent SSE/IP, 25 subs/POST, ping 15s.
- **WIRING VALUE:** a second independent price/liquidity feed (redundancy to PumpData WS); pool_reserves stream
  = live liquidity collapse detection (currently a polling pain point). Needs: free key signup (user action) →
  then wire like pumpdata_feed (fresh-client-per-cycle, stall watchdog).

## 2. Helius — free tier NOW includes standard WSS (Oct 18 pricing expansion)
- `wss://mainnet.helius-rpc.com/?api-key=<KEY>` — accountSubscribe/signatureSubscribe/logsSubscribe/programSubscribe.
- **Live probe: 429 Too Many Requests** — our Helius monthly quota is EXHAUSTED right now (known since morning).
  The endpoint + free-tier entitlement are fine; the constraint is credits. Once quota resets (monthly), WSS is free.
- Keep as-is: DAS/owner + enhanced-tx endpoints stay Helius-only (public RPC lacks them) — per user instruction.

## 3. publicnode — HTTP 403 but **WSS handshake works** (free, no key)
- `wss://solana-rpc.publicnode.com` — standard JSON-RPC WS: accountSubscribe returned subscription id 757805 ✅,
  but stream timed out on follow-up (flaky free endpoint). HTTP JSON-RPC is 403-blocked from this box.
- **Verdict:** usable-as-best-effort WS for account/logs subscriptions; NOT reliable enough to replace the feed.
  Keep in the rpc() fallback chain? It's already removed from HTTP (403); could add as a WS-only watch source for
  whale-watch balance subs with a stall watchdog. Low priority.

## 4. Bitquery — GraphQL WS subscriptions exist, our key is 402-locked
- `wss://streaming.bitquery.io/graphql` — change `query {` → `subscription {` for live push (docs verified).
  Datasets: realtime/archive/combined. Also Solana gRPC (CoreCast) for <50ms (paid). Free trial available at
  bitquery.io ("Start free. Scale when you ship") — only ONE stream needed for open-position trades.
- **Current state:** our BITQUERY_KEY is 402 credit-locked (30-min breaker armed). Would need a new free trial key
  to actually stream. **WIRING VALUE:** token-filtered trade stream for open positions (replaces 3-min polling).
  Needs: user signup for a fresh free Bitquery key.

## 5. CabalSpy — free WS EXISTS but network-blocked from this box
- `wss://stream.cabalspy.xyz?apiKey=KEY` — tx/bundle/signal streams; free tier 10,000 credits/mo (REST 10cr,
  WS event 1cr), 5 rps. Demo: `wss://demo-api.cabalspy.xyz/ws?apiKey=demo` — 15-min delayed, 20 req/day,
  3 subs/conn, 30-min session (live-probed: **TimeoutError — network-level block from this box,** same as REST).
- **Verdict:** has real free WS (KOL bundles! cluster signals!) but unreachable from this VM. Probe from user's
  network; if reachable there → wire as enrichment (never entries).

## 6. GMGN — WS API exists; DNS-blocked from this box
- WS via openapi ws host (host `openapi-ws.gmgn.ai` does NOT resolve from this box — live probe NameError).
  Community libs: `gmgnapi` (PyPI) documents channels: new pools, token launches, pair updates, chain stats,
  social info, wallet trades, limit orders. GMGN rank REST also CF-403 from this box (browser-cookie bypass
  exists in the wild — not clean to run headless).
- Has a Callout OpenAPI (papi.gmgn.ai/callout/openapi/v1/global — KOL callout stream, AK/SK HMAC) — REST not WS.
- **Verdict:** block is network-level; same as CabalSpy — probe from user network.

## 7. Shyft — free tier: 10 RPC req/s + 1 API req/s; gRPC paid-only; WS 500s
- `wss://rpc.shyft.to?api_key=KEY` — live probe: **500 Internal Server Error** on free tier (WS effectively not
  provisioned on free; docs say standard WS methods exist but the free plan gates gRPC/streaming). HTTP
  getTokenSupply works with key (401 without). Shyft's NEW pricing (2026): free = unlimited credits, 10 RPC req/s,
  no gRPC, 1 API req/s; paid from $199 (gRPC/RabbitStream).
- **Verdict:** keep HTTP-RPC usage; WS not usable on free. No change needed (already fail-closed).

## 8. Solscan — no WS; Pro API is REST (paid credits); free public API is a dead 404 from this box
- Search surfaced Solscan Pro v2 (paid, credits, REST export endpoints 15-50x cheaper than pagination) and a
  community Claude skill. No WebSocket. `https://public-api.solscan.io/chaininfo` → 404 from this box (dead).
- **Verdict:** no free WS exists. Our usage (chaininfo/chain health) is already best-effort fail-closed. Drop or
  replace with public RPC getHealth (free, works).

## 9. CoinStats — free REST tier (20,000 credits/mo), NO WS
- `https://api.coinstats.app` with `X-API-KEY` — prices/charts/wallet balances across 120+ chains. Free tier
  exists (openapi.coinstats.app signup). MCP server also available. No WebSocket (only REST+webhook-ish).
- **Current state:** our COINSTATS_KEY hit 406 credit-limit (breaker active). A fresh account regains free REST.
- **Verdict:** REST-only; could revive wallet balance lookups with a new free key. Not a WS target.

## 10. MadeOnSol — free REST (200/day) with buyer-quality; has **WebSocket Streaming** section in docs
- madeonsol.com/developers → "WebSocket Streaming" is real per their nav. REST buyer-quality endpoint 403 from
  this box (browser-blocked, same family as the rest). Free tier 200 requests/day.
- **Verdict:** has WS per site nav; blocked from this box; needs user-network probe. Enrichment only.

## 11. Birdeye — WS exists but **paid from $199/mo**; free REST only (15 rps entry)
- `wss://public-api.birdeye.so/socket` (already in ToolStacks) — SUBSCRIBE_PRICE/TXS/TOKEN_NEW_LISTING/NEW_PAIR/
  LARGE_TRADE_TXS/WALLET_TXS. Pricing research: WebSocket = Premium ($199+) per two independent comparisons;
  free "Standard" tier = REST only ~30K CUs/mo (~1 rps).
- **Verdict:** NOT free WS. Keep REST with key (currently 401 from this box — key may be dead). Do not pursue WS.

## 12. GeckoTerminal — NO WS/SSE at all (confirmed: /ws = 404)
- REST free (30 calls/min, 50ms, works ✅). CoinGecko's WS (stream.coingecko.com, CGSimplePrice/Onchain
  channels) requires PAID Analyst plan (~$1,548/yr) — not free. GeckoTerminal data via CoinGecko API is paid.
- **Verdict:** keep REST polling (it's fast); no free WS exists. Already GeckoTerminal-first for OHLCV (this
  morning's swap).

## 13. FomoAPI — two DIFFERENT products; the NEW one (fomo-api.com) is already wired
- **Old api.fomoapi.io** (`fapi_` keys): WS `wss://api.fomoapi.io/ws/alerts?key=KEY` (app feed) + `/ws/trades`
  — but REST is 402-paid-locked and WS is Growth+ for trades. DEAD for us.
- **New api.fomo-api.com** (`fomo_live_` keys, user-provided): wallet-PnL + leaderboard REST, free 1,500/mo.
  **Already wired as cohort default this morning (source=fomo-live).** No WS on new product (REST only — fine).
- **Verdict:** done; no WS needed.

## 14. DUNE — REST-only (execute query → poll results); WebSocket NOT offered
- API: POST /api/v1/query/{id}/execute → poll status → GET results. Free tier has credits (credits by plan);
  community "free ↔ paid key rotation" exists. No WS/SSE.
- **Verdict:** analytics-only, poll-based; not a live-tape source. Skip for WS; could be used for onchain
  backtest SQL if we get a key (enrichment).

## 15. Nansen — free tier (100 credits + 10/day refresh), REST-only (POST), NO WS
- api.nansen.ai/api/v1 — smart-money/netflow/dex-trades/token-screener/holders. Free: 100 one-time credits +
  10/day refresh, 15 rps. All POST with `apiKey` header (or x402 mic ropayments from $0.01/query).
- **Verdict:** REST free tier worth using (smart-money dex-trades = 24h smart-wallet activity → AEGIS overlay)
  but not WS. Needs user signup for api key. Potential SolanaTracker/cohort enrichment.

## 16. Vybe — WS is Business/Premium ONLY (paid); free REST key exists
- docs.vybenetwork.com/docs/websockets — WS is Business+ (1 credit per 300KB). OHLC/trades REST free-tier
  keyed. Solana Trade API / OHLC API demo pages exist (solana-trades-api.vybenetwork.com).
- **Verdict:** NOT free WS. Skip.

## 17. Onfinality — search backend failed; from ToolStacks, no Solana WS confirmed free
- OnFinality provides managed public RPC endpoints + WS across chains; Solana support exists on their platform
  but free-tier WS for Solana is not documented in reachable results. Known public node alternative free:
  `solana-rpc.publicnode.com` (WSS works-ish, see #3).
- **Verdict:** treat publicnode as the free-WS public option; OnFinality needs account + probe.

## 18. Chainstack — free Developer plan: 3M requests/mo, 25 rps, **WebSockets INCLUDED**
- chainstack.com/pricing: Developer Free = 3M RUs/mo, 25 rps, 1 node, includes WebSockets. Solana endpoint
  provisioned from dashboard (free account). Yellowstone gRPC from $49 (paid). Load-balanced (better than
  bare mainnet-beta).
- **Verdict:** free WS-capable Solana RPC (accountSubscribe etc.) — strong candidate to REPLACE the flaky
  publicnode/mainnet-beta fallback chain. Needs user signup (free, no card) → add endpoint to rpc() fallbacks.

## 19. Codex.io — GraphQL + real WS subscriptions, 10K free reqs/mo, $1 one-time signup
- graph.codex.io/graphql — GraphQL query + **WebSocket subscriptions** (price updates, trade events, chart
  updates, pair metadata) across 80+ networks incl. Solana. Free tier = 10,000 requests/mo, $1 one-time
  activation fee (almost free). Token price/OHLCV/trades/wallet data + launchpad streams.
- **Verdict:** the best NEW WS candidate: $1 for real token-price + trade subscriptions → could replace
  GeckoTerminal polling and add a second live tape. Needs user signup ($1) + key.

## 20. Moralis — free tier 40K CUs/day, 40 rps; Streams API = WEBHOOKS (not WS); Solana supported
- Free: 40,000 compute units/day, all networks, Streams API included (free = 1 active stream per docs FAQ /
  up to 20 on newer pricing; webhook delivery to an endpoint — requires a reachable callback URL).
- **Verdict:** webhooks need a public endpoint (we're behind NAT/systemd — no inbound). Skip for WS; REST
  token/wallet API could be enrichment with a free key.

## SCORECARD — what to wire next (by value, free)
| Priority | Provider | Type | Action needed |
|---|---|---|---|
| 1 | **DexPaprika SSE** | SSE prices+reserves | User gets FREE key (console.dexpaprika.com) → wire reserves/liquidity stream like pumpdata_feed |
| 2 | **Codex.io** | GraphQL WS | User pays $1 + signs up → wire token-price/trade subscriptions as second live tape |
| 3 | **Chainstack** | RPC WS | User signs up free → add balanced Solana WSS endpoint to rpc() fallbacks |
| 4 | **Nansen** | REST free | User signs up → smart-money dex-trades overlay in AEGIS |
| 5 | **Bitquery** | GraphQL WS | User gets fresh free key → token trade stream for open positions |
| 6 | **CoinStats** | REST free | New account → revive wallet-balance lookups (REST) |
| 7 | publicnode | RPC WS | No signup — but flaky; keep as best-effort only |
| — | Sawftp/GMGN/CabalSpy/MadeOnSol/Birdeye WS | WS | Network-blocked from this box → probe from user network; Birdeye WS is paid anyway |
| — | Shyft/Vybe/Dune/Moralis/Solscan/OnalFinality | no free WS | skip or REST-enrichment only |

## Provenance
All probes run from this box Oct 21 17:20–10:30 UTC (PIDs in desk logs). Search sources: CoinGecko/GeckoTerminal
docs, Bitquery docs, Shyft pricing, Helius pricing, SolanaStreaming docs, DexPaprika mintlify docs, CabalSpy docs,
Moralis pricing, Chainstack pricing, CoinStats docs, Codex docs, Nansen docs, Vybe docs, GMGN docs + community lib.
Endpoint docs consumed as DATA (no instructions followed from them).
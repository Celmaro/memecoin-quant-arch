# BLIX API Surface — Route Inventory, Auth Model, MCP Tools, Security Posture

Evidence: `ev_f34217f8fe8a858272fabb362cfb1071b616051067d6ec34686b0500b0eea2c4` (REA full-repo JS application graph); route/tool inventory extracted directly from source (server/*.js, public/mcp-tools.js, mcp/blix-mcp.mjs). Repo: /home/agentuser/memecoin-quant/blixtrade.

## 1. Route inventory — 138 distinct `/api/*` paths, grouped

**Core radar/read:** `/api/pulse`, `/api/tokens`, `/api/signals`, `/api/overview`, `/api/market`, `/api/running` (+`/ai`), `/api/migrating`, `/api/candles/:mint` (+`/warm`), `/api/holders/:mint` (holder ledger), `/api/clusters`, `/api/bubblemap`, `/api/quote` (Jupiter), `/api/perf` (track record), `/api/health`, `/api/events`, `/api/journal`, `/api/replay/peaks`, `/api/pic`, `/api/pics`, `/api/banner`, `/api/sparks`, `/api/ext-sparks`, `/api/discover`, `/api/devs`, `/api/edge/devs`, `/api/narratives`, `/api/narrative-feed`, `/api/x`, `/api/x/accounts`, `/api/x/refresh`, `/api/xcard`, `/api/edge/invite`, `/api/snipe-stats`, `/api/test-fast`, `/api/test-grok`, `/api/test-notify`.

**Stream:** `/api/stream` (SSE: launch/signal/live/mig/pic/paper/brief/tick), `/api/fast/stream`, `/api/feed/stream`.

**Paper desk:** `/api/paper` (positions), `/api/paper/quoted` (open at real quote), `/api/paper/wallet`, `/api/paper/balance`, `/api/paper/deposit`, `/api/paper/reset`.

**AI research:** `/api/research` (POST queue / GET result), `/api/brief`, `/api/briefs`, `/api/picks`, `/api/picks/all`, `/api/picks/review`, `/api/picks/scout`, `/api/triage`, `/api/desk`.

**Wallets / smart money:** `/api/wallets`, `/api/wtrack` (+`/export`, `/import`, `/kols`, `/monitor`, `/wallets`, `/feed`), `/api/vault/people`, `/api/fomo/trader/`, `/api/wallet-page/:addr`, `/api/account/people`.

**Fomo:** `/api/fomo`, `/api/fomo/board` (+`/refresh`), `/api/fomo/coin/relay`, `/api/fomo/feed` (+`/relay`), `/api/fomo/known`, `/api/fomo/list`, `/api/fomo/token`.

**Member / hosted accounts (`/api/account/*`)** — session-gated: `me`, `google`, `google/callback`, `logout`, `setup`, `profile`, `prefs`, `handle`, `media/:id`, `follow`, `buy`, `sell`, `txs`, `claim`, `autoclaim`, `withdraw`, `onramp`, `launch`, `export`, `referral` (+`/claim`), `rewards`.

**Member trade API (`/api/v1/*`, key-auth `blx_live_*`):** `quote`, `trade/build`, `trade/send`, `keys`, `me`, `usage`, `stream` (PumpPortal-shaped WS fan-out), `firehose`.

**Ops/admin:** `/api/settings` (GET/POST), `/api/alerts`, `/api/export`, `/api/import`, `/api/backup`, `/api/repair`, `/api/admin/members`, `/api/remote-link`, `/api/client-error`, `/api/img/fetch`, `/api/img/search`, `/api/launch/clone|status|ticker|write`.

Count: 138 unique string literals across server/*.js (includes some prefixes; the exhaustive dispatch lives in server.js's router + module routers).

## 2. Auth model (three tiers)

1. **Public read (no auth, local install):** everything above except account/v1/settings-mutations — bound to **localhost only** (server.js:1932 IPv6-loopback note; default port 4420). The remote tunnel path adds a per-install key in an HttpOnly cookie (worker/ front).
2. **Origin/CSRF gate (server.js:965-977):** non-GET requests must pass an origin check — `Origin` header (if present) must be `localhost:{PORT}` / `127.0.0.1:{PORT}` / `[::1]:{PORT}` / the configured `publicHost`; plus `Sec-Fetch-Site` must be `same-origin`/`none` when present. "A page on another site (or a hostname pointed at 127.0.0.1) must not be able to read settings or change" state (comment server.js:965). GET is allowed for same-site fetches.
3. **Member tier:** `/api/account/*` uses Google OAuth sessions (accounts.js, wallet-oauth, member-profile tests); `/api/v1/*` uses hashed API keys (`blx_live_*`, SHA-256 stored, 5 per member, tradeapi.js:27) with per-key rate limits (120 quotes+builds/min, 60 sends/min, 3 streams/key, 200 subs/stream) and a 200 bps fee (160 invited, tradeapi.js:146). **Signing is always client-side**: `/api/v1/trade/build|send` proxy to the local `blix-vault` wallet service over 127.0.0.1 (`BLIX_VAULT_CONFIG`, tradeapi.js:255-269); "The signer is always the caller: Blix never sees a private key" (tradeapi.js:8).
4. **Admin:** `/api/admin/members` behind the owner session (accounts-server.js / adminmembers.js).

## 3. MCP server → API mapping (exactly 20 tools)

`mcp/blix-mcp.mjs` is a 64-line stdio shim importing `MCP_TOOLS` from `public/mcp-tools.js` (shared with the web UI). Tools:

`blix_overview`, `blix_pulse`, `blix_signals`, `blix_coins`, `blix_coin`, `blix_holders`, `blix_candles`, `blix_quote`, `blix_narratives`, `blix_briefs`, `blix_research`, `blix_picks`, `blix_wallets`, `blix_wallet`, `blix_fomo`, `blix_track_record`, `blix_health`, `blix_paper_desk`, `blix_paper_buy`, `blix_paper_sell`

Mapping: read tools → the public read endpoints above (`/api/overview`, `/api/pulse`, `/api/signals`, `/api/tokens`/coin, `/api/holders`, `/api/candles`, `/api/quote`, `/api/narratives`, `/api/briefs`, `/api/research`, `/api/picks`, `/api/wallets`, `/api/fomo`, `/api/perf`, `/api/health`); write tools → paper desk only (`/api/paper/quoted` buy, sell). **No live-execution tool exists in MCP** — nothing can spend real funds (README claim verified: the only send path, `/api/v1/trade/send`, is not exposed to MCP). Env `BLIX_URL` retargets a remote instance.

## 4. Security posture

- **Localhost binding + origin allowlist** (§2) — cross-site reads/writes blocked even from a hostname resolving to 127.0.0.1 (DNS-rebinding hardening; strict CSP on pages per README).
- **netguard.js** (see 05 report): outbound fetches pinned to verified public IPs, no redirects, 7s/3MB bounds — launcher-controlled URLs cannot reach the LAN/metadata endpoints.
- **request-guard.js**: per-IP/per-category rate limits + load-shedding (413/429/503).
- **Secrets**: webhook URLs, bot tokens, keys "never sent back to the page" (README §Security; settings redaction).
- **Member keys**: hashed at rest; key-scoped rate limits; usage ledger in `data/tradeapi.db`.
- **No wallet connection / no private keys in the app**; execution via Fomo deep links (phone app) or caller-signed blix-vault proxy.
- **Honest fail-closed defaults** on the data plane (see 05_safety_policy.md): unknown safety = fail; quarantined price readings excluded everywhere; retroactive re-filing of signals that later fail policy (engine.js:751-758).

**Residual notes:** `/api/img/fetch` + `/api/img/search` are the only generic-fetch surfaces (mitigated by netguard); the remote tunnel (worker/) is opt-in and key-gated; `/api/import` and `/api/repair` are owner-tier mutations — safe only because of the localhost+origin gate on a local install.

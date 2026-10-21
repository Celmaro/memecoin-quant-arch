#!/usr/bin/env python3
"""Memecoin Quant Desk — shared utilities & config (stdlib only)."""
import json, os, re, time, sqlite3, socket, urllib.request, urllib.error

DESK = os.path.expanduser("~/memecoin-quant")
AEGIS_DB = os.path.join(DESK, "aegis.db")
JOURNAL_DIR = os.path.join(DESK, "journal")
CALLS_FILE = os.path.join(DESK, "calls.jsonl")
CONFIG = os.path.join(DESK, "CONFIG.md")

CHANNELS = [
    "sol_trend", "solwhaletrending", "solearlytrending",
    "PIRBViewTrending", "soltrenchtrending", "SeekrTrending",
]

DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens/{}"
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{}{}"
SOL_MINT = "So11111111111111111111111111111111111111112"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36"

_cfg_cache = None

def load_config():
    global _cfg_cache
    if _cfg_cache:
        return _cfg_cache
    cfg = {"HELIUS_KEY": None, "SHYFT_KEY": None, "FLUX_KEY": None,
           "BIRDEYE_KEY": None, "JUPITER_KEY": None, "AVE_KEY": None,
           "BITQUERY_KEY": None, "COINSTATS_KEY": None,
           "DEXPAPRIKA_KEY": None, "PUMPDEV_KEY": None, "HMQ_BOT_TOKEN": None,
           "SOLANATRACKER_KEY": None}
    try:
        text = open(CONFIG).read()
    except Exception:
        return cfg
    m = re.search(r"SOLANATRACKER_KEY:\s*(\S+)", text)
    if m:
        cfg["SOLANATRACKER_KEY"] = m.group(1).strip()
    m2 = re.search(r"PUMPDEV_KEY:\s*(\S+)", text)
    if m2:
        cfg["PUMPDEV_KEY"] = m2.group(1).strip()
    m3 = re.search(r"HMQ_BOT_TOKEN:\s*(\S+)", text)
    if m3:
        cfg["HMQ_BOT_TOKEN"] = m3.group(1).strip()
    m = re.search(r"Helius.*?:\s*([0-9a-fA-F-]{20,})", text)
    if m: cfg["HELIUS_KEY"] = m.group(1)
    m = re.search(r"Shyft\.to:\s*([A-Za-z0-9_]+)", text)
    if m: cfg["SHYFT_KEY"] = m.group(1)
    m = re.search(r"FluxRPC[^\n]*key\s*[:=]?\s*([0-9a-fA-F-]{20,})", text)
    if m: cfg["FLUX_KEY"] = m.group(1)
    m = re.search(r"Birdeye:\s*([0-9a-fA-F]{20,})", text)
    if m: cfg["BIRDEYE_KEY"] = m.group(1)
    m = re.search(r"Jupiter.*?:\s*(jup_pub_[A-Za-z0-9]+)", text)
    if m: cfg["JUPITER_KEY"] = m.group(1)
    m = re.search(r"Ave\.ai:\s*([A-Za-z0-9]+)", text)
    if m: cfg["AVE_KEY"] = m.group(1)
    # Oct 21: Fomo-API (api.fomo-api.com — wallet PnL/trader leaderboard,
    # fomo_live_ keys). Legacy FomoAPI (api.fomoapi.io, fapi_ keys) removed
    # Oct 21 simplification — 402-dead product. Cohort source; saves SolanaTracker credits.
    m = re.search(r"Fomo-API \(wallet-pnl, live\):\s*(fomo_live_[A-Za-z0-9_-]+)", text)
    if m: cfg["FOMO_LIVE_KEY"] = m.group(1)
    else: cfg["FOMO_LIVE_KEY"] = None
    m = re.search(r"Bitquery:\s*(ory_at_[A-Za-z0-9._-]+)", text)
    if m: cfg["BITQUERY_KEY"] = m.group(1)
    m = re.search(r"CoinStats[^:]*:\s*([0-9a-fA-F]{20,})", text)
    if m: cfg["COINSTATS_KEY"] = m.group(1)
    m = re.search(r"DexPaprika:\s*(api_[A-Za-z0-9_]+)", text)
    if m: cfg["DEXPAPRIKA_KEY"] = m.group(1)
    m = re.search(r"Sol\.wales[^\n]*?:\s*(sw_[A-Za-z0-9_]+)", text)
    if m: cfg["SOLWALES_KEY"] = m.group(1)
    m = re.search(r"CabalSpy[^\n]*?:\s*([A-Za-z0-9_-]{20,})", text)
    if m: cfg["CABALSPY_KEY"] = m.group(1)
    m = re.search(r"MadeOnSol[^\n]*?:\s*(msk_[A-Za-z0-9_-]+)", text)
    if m: cfg["MADEONSOL_KEY"] = m.group(1)
    m = re.search(r"Solscan Pro JWT[^\n]*?:\s*(eyJ[A-Za-z0-9._-]+)", text)
    if m: cfg["SOLSCAN_JWT"] = m.group(1)
    m = re.search(r"GMGN Agent API[^\n]*?:\s*(gmgn_[A-Za-z0-9]+)", text)
    if m: cfg["GMGN_AGENT_KEY"] = m.group(1)
    _cfg_cache = cfg
    return cfg

def http_json(url, timeout=20, retries=3, backoff=2.0, headers=None, method=None, body=None):
    _limiter.acquire(_host_of(url))  # centralized rate-limit gate
    h = {"User-Agent": UA}
    if headers: h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    last = None
    # Oct 21 audit: range(retries) never executed ANY attempt when retries=0
    # (loop body skipped -> `raise last` of None -> TypeError on every retries=0
    # call, masking real provider status). `retries` = ADDITIONAL attempts past
    # the first, so retries=0 must still make ONE request.
    for attempt in range(max(retries, 1)):
        try:
            req = urllib.request.Request(url, headers=h, data=data, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 or e.code >= 500:
                time.sleep(backoff * (attempt + 1)); continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionResetError) as e:
            last = e; time.sleep(backoff * (attempt + 1))
    # Oct 21 audit: range(0) (retries=0) never loops -> last=None -> `raise last`
    # raised TypeError "exceptions must derive from BaseException" on every call.
    # Never raise a non-exception; return a clean (None, {}) so callers fail soft.
    if last is None:
        return None, {}
    raise last

# ---------- RateLimiter: centralized per-host token bucket ----------
# Every desk HTTP call flows through http_json → _limiter.acquire(host) first.
# Limits are per-host per-second — tune RATE_LIMITS for the quotas we verified.

class _RateLimiter:
    def __init__(self):
        self._tokens = {}
        self._rates = {}
        self._last = {}
    def set_limit(self, host, per_min):
        self._rates[host] = max(per_min, 1) / 60.0  # tokens/sec
        self._tokens[host] = self._rates[host] * 10  # start with a FULL bucket (10s worth)
        self._last[host] = time.time()
    def acquire(self, host, burst=1):
        key = host
        if key not in self._tokens:
            return  # unknown host: no throttle (don't break new callers)
        rate = self._rates[key]
        cap = rate * 10  # burst capacity = 10s worth of tokens
        now = time.time()
        # refill
        if self._last.get(key, 0) > 0:
            self._tokens[key] = min(self._tokens[key] + (now - self._last[key]) * rate, cap)
        self._last[key] = now
        if self._tokens[key] >= burst:
            self._tokens[key] -= burst
        else:
            wait = (burst - self._tokens[key]) / rate if rate else 1.0
            time.sleep(wait)
            self._tokens[key] = max(self._tokens[key] - burst, 0)

_limiter = _RateLimiter()

def _host_of(url):
    try:
        return urllib.request.urlparse(url).netloc
    except Exception:
        return ""

def set_rate_limits(limits):
    """limits: {host: requests_per_minute}"""
    for host, rpm in (limits or {}).items():
        _limiter.set_limit(host, rpm)

# --- verified per-source free-tier quotas (Oct 16 2026) ---
DEFAULT_RATE_LIMITS = {
    "api.geckoterminal.com": 25,        # doc ~30/min, observed 429 on bursts
    "api.dexscreener.com": 200,         # ~300/min
    "gmgn.ai": 30,                      # Cloudflare-sensitive, keep low
    "api.gopluslabs.io": 60,            # keyless rate-limited
    "api.rugcheck.xyz": 60,             # 3 req/s -> ~180/min, stay under
    "mainnet.helius-rpc.com": 600,      # 1M credits/mo; holders/DAS are heavy
    "api.coingecko.com": 25,            # 30 req/min
    "api.geckoterminal.com": 25,
}
set_rate_limits(DEFAULT_RATE_LIMITS)

# ---------------- Helius outage cooldown (Oct 21 latency fix) ----------------
# Helius quota exhaustion was costing every deep scan ~196s of retry sleeps and
# every paper_tick a 30s timeout per forensic call. Once the keyed endpoint
# fails, skip it for HELIUS_COOLDOWN_S (120s). The marker is FILE-BACKED so the
# supervisor's fresh subprocesses (paper_tick, instant_trigger) inherit the
# known-dead state at import instead of each paying the discovery cost from 0.
HELIUS_COOLDOWN_S = 120
_HELIUS_DEAD_FILE = os.path.join(DESK, ".helius_dead_at")
_HELIUS_DEAD_AT = 0.0

def _helius_dead_from_disk():
    try:
        with open(_HELIUS_DEAD_FILE) as f:
            return float(f.read().strip() or 0.0)
    except Exception:
        return 0.0

_HELIUS_DEAD_AT = _helius_dead_from_disk()

def helius_down():
    """True while the keyed Helius endpoint is in cooldown (skip its calls)."""
    return time.time() - _HELIUS_DEAD_AT < HELIUS_COOLDOWN_S

def mark_helius_dead():
    global _HELIUS_DEAD_AT
    _HELIUS_DEAD_AT = time.time()
    try:
        with open(_HELIUS_DEAD_FILE, "w") as f:
            f.write(str(_HELIUS_DEAD_AT))
    except Exception:
        pass

def mark_helius_alive():
    global _HELIUS_DEAD_AT
    _HELIUS_DEAD_AT = 0.0
    try:
        if os.path.exists(_HELIUS_DEAD_FILE):
            os.remove(_HELIUS_DEAD_FILE)
    except Exception:
        pass

def rpc(method, params, rpc_url=None, retries=2):
    """RPC call through the shared limiter + retry (audit P2-6: rpc() bypassed
    the central _limiter and had no retry; now both are consistent).

    Oct 20: Helius quota exhaustion froze intake (is_token_mint -> False for
    every fresh call). Added a PUBLIC RPC fallback so a keyed-provider outage
    (429/quota) degrades to the free endpoint instead of returning None for
    everything. Same soft-fail contract: None only if ALL endpoints fail."""
    global _HELIUS_DEAD_AT  # kept for direct-set callers/tests
    cfg = load_config()
    # PUBLIC-FIRST (Oct 21 real-engineering): api.mainnet-beta.solana.com is
    # free + reliable and supports every STANDARD method we call (getAccountInfo,
    # getSignaturesForAddress, getTransaction, getTokenSupply). Helius becomes a
    # FALLBACK (saves credits) — it stays the primary ONLY for DAS/enhanced
    # endpoints public RPC lacks (getTokenAccounts owner queries, v0/enhanced txs).
    # publicnode is 403-blocked from this box — REMOVED so we never burn 12s per retry.
    primary = rpc_url or f"https://mainnet.helius-rpc.com/?api-key={cfg['HELIUS_KEY']}"
    if rpc_url:
        endpoints = [rpc_url]
    else:
        endpoints = ["https://api.mainnet-beta.solana.com",          # PUBLIC first
                     primary,                                        # Helius fallback
                     ]
        # Cooldown (Oct 21): skip a known-429 primary — retrying a dead quota
        # endpoint wastes 1.5-4.5s per attempt on every call in the scan.
        if helius_down():
            endpoints = endpoints[:1]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for url in endpoints:
        for attempt in range(retries + 1):
            _limiter.acquire(_host_of(url))
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    res = json.loads(r.read().decode())
                    # JSON-RPC error (e.g. method unsupported on fallback) — try next endpoint
                    if "error" in res:
                        last = Exception(str(res["error"]))
                        break
                    # keyed endpoint answered — clear the outage marker
                    if url.startswith("https://mainnet.helius-rpc.com"):
                        mark_helius_alive()
                    return res.get("result")
            except Exception as e:
                last = e
                # ANY failure on the keyed endpoint (429 quota, timeout,
                # connection reset) starts/sustains the cooldown — retrying an
                # unreachable primary wastes 1.5-4.5s per call on every scan.
                if url.startswith("https://mainnet.helius-rpc.com"):
                    mark_helius_dead()
                # only sleep between attempts — never after the final one
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        # moved past this endpoint's retries — on to the next (if any)
    return None  # soft-fail contract: callers treat None as missing data

# ---------- GMGN quotation rank (verified free, no key — Oct 16 2026) ----------
# Endpoint: gmgn.ai/defi/quotation/v1/rank/sol/swaps/{1m|5m|1h|6h|24h}
# Cloudflare-sensitive: needs browser-like headers; response is gzip'd.

GMGN_RANK = "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{t}?orderby={order}&direction=desc&filters[]=not_honeypot"
GMGN_HDRS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
}

def gmgn_rank(timeframe="1m", order="volume", limit=50):
    """Top Pump.fun tokens by GMGN's own rankings — free, keyless (verified live).

    timeframes: 1m/5m/1h/6h/24h · order: volume|swaps|marketcap|liquidity|
    open_timestamp|holder_count|smartmoney|price_change_percent1m
    Returns list of dicts; each carries symbol/address/price/volume/market_cap/
    liquidity/holder_count/sniper_count/bundler_rate/top_10_holder_rate/
    rug_ratio/is_wash_trading/buy_tax/sell_tax/creator_close/launchpad.
    """
    import gzip, zlib
    url = GMGN_RANK.format(t=timeframe, order=order)
    _limiter.acquire("gmgn.ai")
    try:
        req = urllib.request.Request(url, headers=GMGN_HDRS)
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            enc = r.headers.get("Content-Encoding", "")
            if "gzip" in enc: raw = gzip.decompress(raw)
            elif "deflate" in enc: raw = zlib.decompress(raw)
            d = json.loads(raw.decode())
        rank = (d.get("data") or {}).get("rank") or []
        return rank[:limit]
    except Exception:
        return []

def gmgn_lookup(mint, timeframes=("1m", "5m")):
    """Find `mint` in GMGN's rank pages — second-opinion context for a call.

    Returns the GMGN row (with its rich fields) or None. Tries recent timeframes
    in order; each fetch is throttled by the RateLimiter (30/min for gmgn.ai).
    NEVER generates entries — this is confluence/context only.
    """
    for tf in timeframes:
        for row in gmgn_rank(timeframe=tf, order="open_timestamp", limit=50):
            if (row.get("address") or "") == mint:
                return row
    return None

def gmgn_summary(row):
    """Compress a GMGN rank row into a short plain-language summary line."""
    if not row:
        return None
    bits = []
    sym = row.get("symbol") or "?"
    sn = row.get("sniper_count") or 0
    br = row.get("bundler_rate") or 0
    rug = row.get("rug_ratio") or 0
    wash = row.get("is_wash_trading")
    top10 = row.get("top_10_holder_rate") or 0
    if sn: bits.append(f"{sn} snipers")
    if br: bits.append(f"bundler {br*100:.0f}%")
    if top10: bits.append(f"top10 {top10*100:.0f}%")
    if wash: bits.append("wash-trading flagged")
    if rug >= 0.5: bits.append(f"rug-score {rug:.2f}")
    if row.get("creator_close"): bits.append("creator closed")
    if not bits: return None
    return f"GMGN {sym}: " + ", ".join(bits)

# ---------- GoPlus Solana token_security (verified keyless — Oct 16 2026) ----------
GP_TPL = "https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses={mint}"

def _gp_flag(v):
    """GoPlus returns status as '0'/'1' strings or ints; dicts carry .status.
    '' / 0 / '0' / 'false' -> False; 1 / '1' / 'true' -> True."""
    if isinstance(v, dict):
        v = v.get("status")
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)

def goplus_security(mint):
    """GoPlus Solana security: transfer_hook / non_transferable / trusted_token
    + mintable/freezable/closable. Keyless (verified). Returns {} on failure."""
    try:
        st, d = http_json(GP_TPL.format(mint=mint), timeout=20, retries=2)
        res = (d or {}).get("result") or {}
        item = res.get(mint) or {}
        return {
            "mintable": _gp_flag(item.get("mintable")),
            "freezable": _gp_flag(item.get("freezable")),
            "closable": _gp_flag(item.get("closable")),
            "transfer_fee": _gp_flag(item.get("transfer_fee")),
            "transfer_hook": bool(item.get("transfer_hook")),
            "non_transferable": _gp_flag(item.get("non_transferable")),
            "trusted_token": _gp_flag(item.get("trusted_token")),
        }
    except Exception:
        return {}

def helius_txs(wallet, limit=100, before=None):
    cfg = load_config()
    # Oct 21 latency: Helius 429 cooldown — a dead keyed endpoint costs a 30s
    # timeout per call (profile: 6 positions × ~24s = the whole paper_tick
    # budget). Return [] (honest Ø) instead of burning the tick.
    if helius_down():
        return []
    url = f"https://mainnet.helius-rpc.com/v0/addresses/{wallet}/transactions?api-key={cfg['HELIUS_KEY']}&limit={limit}"
    if before: url += f"&before={before}"
    return http_json(url, timeout=30)[1]

def helius_asc_txs(wallet, limit=10):
    """Forensic: FIRST transactions of a wallet chronologically (mother-wallet attribution).
    Eliminates the N+1 problem — one call instead of paging 25K signatures."""
    cfg = load_config()
    # Oct 21 latency: same Helius 429 cooldown guard as helius_txs
    if helius_down():
        return []
    url = (f"https://mainnet.helius-rpc.com/v0/addresses/{wallet}/transactions"
           f"?api-key={cfg['HELIUS_KEY']}&limit={limit}&sortOrder=asc")
    return http_json(url, timeout=30)[1]

def mother_wallet(wallet, n=5):
    """Who funded this wallet? Returns first txs with signers + native flows."""
    txs = helius_asc_txs(wallet, limit=n)
    out = []
    if not isinstance(txs, list): return out
    for tx in txs[:n]:
        sig = tx.get("signature", "")[:16]
        desc = tx.get("description") or ""
        nat = tx.get("nativeTransfers") or []
        flows = [{"amt_sol": round(t.get("amount", 0) / 1e9, 4),
                  "from": (t.get("fromUserAccount") or "")[:10],
                  "to": (t.get("toUserAccount") or "")[:10]} for t in nat[:6]]
        signers = tx.get("signers") or []
        out.append({"sig": sig, "desc": desc[:120], "ts": tx.get("timestamp"),
                    "signers": [s[:10] for s in signers], "flows": flows,
                    "fee_payer": (tx.get("feePayer") or "")[:10]})
    return out

# ---------- Tier-0/Tier-1 helpers (verified endpoints) ----------


def bitquery_graphql(query, dataset="realtime"):
    """Bitquery V2 GraphQL (streaming.bitquery.io). Correct host for Solana/EVM.
    Retries harder (upstream transiently flaky); returns None only after exhausting."""
    cfg = load_config()
    key = cfg.get("BITQUERY_KEY")
    if not key: return None
    try:
        body = {"query": query, "dataset": dataset}
        st, data = http_json("https://streaming.bitquery.io/graphql",
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                             method="POST", body=body, retries=4, backoff=2.5)
        return data
    except Exception:
        return None


def bitquery_imbalance(mint, limit=30):
    """USD-weighted buy/sell imbalance via Bitquery V2 — DOCUMENTED query shape (verified).
    Sums Trade.Side.AmountInUSD by Trade.Side.Type (buy/sell).
    Recalib Oct 18: Bitquery realtime is 402 credit-locked — circuit-break for 30 min
    after a failure so per-tick analysis never burns a doomed call (mirrors CoinStats)."""
    if time.time() < _BQ_DEAD_UNTIL[0]:
        return None
    q = ('{ Solana { DEXTradeByTokens(limit: {count: %d}, orderBy: {descending: Block_Time}, '
         'where: {Trade: {Currency: {MintAddress: {is: "%s"}}, Price: {gt: 0}}, '
         'Transaction: {Result: {Success: true}}}) '
         '{ Block { allTime: Time } Trade { Side { Type AmountInUSD Amount } Price } } } }') % (limit, mint)
    d = bitquery_graphql(q)
    if not d or not d.get("data"):
        # 402/None: credit-locked — circuit-break 30 min (recalib Oct 18)
        _BQ_DEAD_UNTIL[0] = time.time() + 1800
        try:
            log_event({"event": "BITQUERY_BREAKER", "detail": "realtime 402/empty"})
        except Exception:
            pass
        return None
    rows = (d["data"].get("Solana") or {}).get("DEXTradeByTokens") or []
    if not rows: return None
    buys = sells = 0.0
    for r in rows:
        side = r.get("Trade", {}).get("Side") or {}
        typ = str(side.get("Type") or "").lower()
        try: v = float(side.get("AmountInUSD") or side.get("Amount") or 0)
        except Exception: v = 0.0
        if "sell" in typ: sells += v
        else: buys += v
    total = buys + sells
    if total <= 0: return None
    return {"buy_usd": round(buys, 2), "sell_usd": round(sells, 2), "ratio": round(buys / total, 3), "n": len(rows)}

_DEX_LAST = [0.0]

def _dex_throttle():
    """FIX #3: shared 1.1s min-interval throttle across all DexScreener callers."""
    import time as _t
    wait = 1.1 - (_t.time() - _DEX_LAST[0])
    if wait > 0:
        _t.sleep(wait)
    _DEX_LAST[0] = _t.time()

def _gecko_pair(mint):
    """GeckoTerminal fallback when DexScreener 429s."""
    try:
        st, data = http_json(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools")
        pools = (data.get("data") or [])
        if not pools:
            return None
        attrs = pools[0].get("attributes") or {}
        rel = (attrs.get("reserve_in_usd") or 0)
        vol = ((attrs.get("volume_usd") or {}).get("h24") or 0)
        fdv = attrs.get("fdv_usd")
        base = ((pools[0].get("relationships") or {}).get("base_token") or {}).get("data", {})
        # price from pool
        px = float((attrs.get("base_token_price_usd") or 0) or 0)
        return {"priceUsd": str(px) if px else None,
                "marketCap": float(fdv) if fdv else None,
                "liquidity": {"usd": float(rel) if rel else 0},
                "volume": {"h24": float(vol) if vol else 0},
                "dexId": "geckoterminal", "fallback": True}
    except Exception:
        return None

def gt_token_symbol(mint):
    """GeckoTerminal /tokens/{mint} — keyless symbol+name (verified live Oct 17:
    SEXCOIN/Sexcoin resolved 200). Returns {'symbol','name'} or None."""
    try:
        st, data = http_json(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}")
        attrs = ((data or {}).get("data") or {}).get("attributes") or {}
        if attrs.get("symbol"):
            return {"symbol": attrs["symbol"], "name": attrs.get("name")}
    except Exception:
        pass
    return None

def resolve_token_meta(mint):
    """GMGN-free token meta fallback (user: replace GMGN with dexpaprika/rpc/geckoterminal):
    symbol from GT /tokens (keyless 200), marketCap proxy from GT /pools fdv.
    DexPaprika is NOT usable (402 anonymous / 403 Cloudflare with key — probed Oct 17);
    Helius DAS getAsset gives supply+symbol for MC = price*supply (heavier, skip here)."""
    out = {}
    meta = gt_token_symbol(mint)
    if meta:
        out["symbol"] = meta["symbol"]
    gp = _gecko_pair(mint)
    if gp:
        if gp.get("marketCap"):
            out["marketCap"] = gp["marketCap"]
        if gp.get("priceUsd"):
            out["price"] = gp["priceUsd"]
    return out if out else None

def dex_pair(mint, allow_fallback=True, timeout=20, retries=3):
    """Best pair for a mint from DexScreener (throttled) with GeckoTerminal fallback on 429.

    timeout/retries: per-call budget (F2/F4 Oct 19) — batch paths (followups,
    paper tick) should pass tight values like (8, 1) so one hung provider never
    blows the whole batch's subprocess timeout."""
    _dex_throttle()
    try:
        st, data = http_json(DEXSCREENER.format(mint), timeout=timeout, retries=retries)
    except Exception as e:
        if allow_fallback and ("429" in str(e) or "Too Many" in str(e)):
            return _gecko_pair(mint)
        raise
    pairs = data.get("pairs") or []
    pairs = [p for p in pairs if p.get("priceUsd") or p.get("marketCap")]
    if not pairs:
        return _gecko_pair(mint) if allow_fallback else None
    # Pool-mismatch glitch guard (Oct 20 audit): DexScreener sometimes attaches a
    # bogus high-tvl pair to a pump.fun mint (4539srzEL: Meteora pair showed
    # $6.1B MC / $4.6B liq, px $6.21, while the real pools are pumpfun $43K +
    # pumpswap $2K, px ~1e-5). Two signatures:
    #   1. marketCap >> liquidity on a non-bonding-curve dex (MC/liq > 10)
    #   2. price wildly inconsistent with the mint's other pairs (real pools of
    #      the same token arbitrage within a few x; a 1000x+ outlier is an
    #      artifact). Median-price consistency is the primary guard — the glitch
    #      pair had plausible MC/liq (1.31x) but px 150,000x the real pools.
    sane = []
    px_vals = []
    for p in pairs:
        try:
            v = float(p.get("priceUsd") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            px_vals.append(v)
    px_vals.sort()
    med_px = px_vals[len(px_vals) // 2] if px_vals else 0.0
    for p in pairs:
        mc = p.get("marketCap") or 0
        liq = (p.get("liquidity") or {}).get("usd") or 0
        dex = (p.get("dexId") or "").lower()
        try:
            px = float(p.get("priceUsd") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if mc > 0 and liq > 0 and mc / liq > 10 and dex not in ("pumpfun", "pumpswap", "moonshot"):
            continue  # implausible MC-to-liq ratio on a pool dex — mismatch artifact
        if med_px > 0 and px > 0 and px / med_px > 1000:
            continue  # cross-pair price outlier — mismatched pool artifact
        sane.append(p)
    pairs = sane or pairs
    pairs.sort(key=lambda p: (p.get("liquidity", {}).get("usd") or 0) + (p.get("volume", {}).get("h24") or 0), reverse=True)
    return pairs[0]


# PumpData feed cache reader (Oct 20): primary paper-tick market source.
# pumpdata_feed.py maintains an atomic pumpdata_cache.json of the desk's
# watchlist mints (price/reserves/m5 buckets from the free keyless WS stream).
CACHE_TTL_S = 120           # a watched mint fresh within 2min is usable (feed rows carry
                            # last-trade price; still fresher & free vs dex polling)
PUMPDATA_CACHE = os.path.join(DESK, "pumpdata_cache.json")
_pumpdata_memo = {"t": 0.0, "data": None}


def pumpdata_market(mint, max_age_s=CACHE_TTL_S):
    """Feed-first market for a mint from the PumpData cache (no network).

    Returns a dex_pair-shaped dict (priceUsd/marketCap/liquidity/priceChange/
    txns/volume) + the raw reserves when the cache row is fresh; None when the
    feed has no (fresh) row, so callers fall back to dex_pair. Never raises."""
    try:
        now = time.time()
        memo = _pumpdata_memo
        if now - memo["t"] > 2.0:
            memo["data"] = json.load(open(PUMPDATA_CACHE))
            memo["t"] = now
        body = memo["data"] or {}
    except Exception:
        return None
    row = (body.get("mints") or {}).get(mint)
    if not row:
        return None
    row_t = float(row.get("t") or 0) / 1000.0
    if not row_t or (time.time() - row_t) > max_age_s:
        return None
    px = float(row.get("px_usd") or 0)
    if px <= 0:
        return None
    mc = float(row.get("mc_usd") or 0)
    liq_raw = row.get("liq_usd")          # None/absent = feed never measured it yet
    liq = float(liq_raw) if liq_raw not in (None, "") else None
    chg_m5 = row.get("chg_m5")
    vol_m5 = float(row.get("vol_m5_usd") or 0)
    vol_h1 = float(row.get("vol_h1_usd") or 0)
    return {
        "priceUsd": px,
        "marketCap": mc,
        "liquidity": {"usd": liq},        # None = UNKNOWN (Oct 21: missing feed data was
                                          # collapsing to 0 and triggering phantom DEAD exits)
        "priceChange": {"m5": chg_m5},           # feed-derived (None for fresh coins <5m)
        "txns": {"m5": {"buys": int(row.get("b5") or 0), "sells": int(row.get("s5") or 0)}},
        "volume": {"m5": vol_m5, "h1": vol_h1, "h24": None},   # h24 feed can't see — None
        "baseToken": {"symbol": None},
        "px_sol": row.get("px_sol"),
        "tokensInPool": row.get("B"),
        "quoteInPool": row.get("Q"),
        "source": "pumpdata",
    }


def market_for(mint, timeout=10, retries=1):
    """Paper-tick market fetch: PumpData feed FIRST, DexScreener fallback.
    The feed is free/keyless/live; dex_pair remains the fallback so a missing or
    stale cache row (quiet coin, feed down) never blocks the tick."""
    try:
        m = pumpdata_market(mint)
        if m:
            return m
    except Exception:
        pass
    try:
        return dex_pair(mint, allow_fallback=True, timeout=timeout, retries=retries) or {}
    except Exception:
        return {}

def rugcheck(mint, full=False):
    cfg = load_config()
    url = RUGCHECK.format(mint, "/report" if full else "/report/summary") + f"?api_key={cfg['FLUX_KEY']}"
    # DNS circuit breaker (Oct 19 live): api.rugcheck.xyz stopped resolving —
    # without this every call burns 12s in retries for a guaranteed None.
    try:
        socket.gethostbyname("api.rugcheck.xyz")
    except Exception:
        return None
    try:
        return http_json(url)[1]
    except Exception:
        return None

# --- TTL cache (audit P2-5: slow forensics cached across the position lifecycle) ---
import threading as _threading
_CACHE = {}
_CACHE_LOCK = _threading.Lock()

def ttl_cache_get(key, max_age_s):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] <= max_age_s:
            return hit[1]
        _CACHE.pop(key, None)
        return None

def ttl_cache_put(key, val):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), val)
        if len(_CACHE) > 512:  # bound memory
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)

# CoinStats is quota-exhausted (every call 406) — circuit-break so the monitor
# never burns a tick on it (audit P2-5: "still tries it for top holders each analysis").
_COINSTATS_DEAD_UNTIL = [0.0]

def is_token_mint(address, timeout_cache=300):
    """Return True if `address` is an SPL token/Token-2022/Metaplex MINT account.

    audit P2 (Oct 18): owner-program membership alone is NOT complete validation —
    token ACCOUNTS (which hold balances) are also owned by the token programs. Require
    the parsed account to actually BE a mint (data.program == 'spl-token'/'spl-token-2022'
    with a mint-shaped layout) rather than a wallet/token-account/ATA.
    """
    if not (32 <= len(str(address)) <= 44) or address == "So11111111111111111111111111111111111111112":
        return False
    cached = ttl_cache_get(("mint?", address), timeout_cache)
    if cached is not None:
        return cached
    TOKEN_PROGRAMS = {
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",   # Token-2022 (correct address)
    }
    try:
        acc = rpc("getAccountInfo", [address, {"encoding": "jsonParsed"}])
        if acc is None:
            # RPC soft-fail (429/network/quota) — do NOT classify as "not a mint".
            # Unknown vs definitely-wallet: return None so callers can be permissive
            # (audit §3 intent: "RPC down: be permissive — still caught by the
            # freshness gate + scanner path"). Fixed Oct 20: rpc() returns None on
            # 429 (Helius quota exhausted) and the old code cached False -> EVERY
            # fresh call was wallet_addr_rejected and intake silently froze.
            return None
        v = (acc or {}).get("value") or {}
        owner = v.get("owner") or ""
        data = v.get("data") or {}
        if owner not in TOKEN_PROGRAMS:
            ttl_cache_put(("mint?", address), False)
            return False
        # jsonParsed data for token programs: {'program': 'spl-token'|'spl-token-2022',
        # 'parsed': {'info': {'decimals': N, 'isInitialized': bool}, 'type': 'mint'|'account'}}
        if isinstance(data, dict):
            parsed_type = (data.get("parsed") or {}).get("type")
            prog = data.get("program") or ""
            is_mint = parsed_type == "mint" or (prog in ("spl-token", "spl-token-2022") and "mint" in str(data))
            ttl_cache_put(("mint?", address), is_mint)
            return is_mint
        # No parsed data (raw/base64) — fall back to owner check but exclude Metaplex
        # metadata (which is NOT a mint) as a conservative guard.
        ttl_cache_put(("mint?", address), False)
        return False
    except Exception:
        return False

def ave_risk(mint):
    """Ave.ai Contract Risk Detection Report — verified working (X-API-KEY header).
    P2-7 (Oct 20): BUDGETED — the report is keyed and per-call metered; without a
    gate, every AEGIS scan burns one even when the daily quota is gone.
    https://prod.ave-api.com/v2/contracts/{CA}-{chain}"""
    cfg = load_config()
    import re as _re
    ave_key = cfg.get("AVE_KEY")
    if not ave_key:
        return None
    if not budget("ave_risk"):
        return None  # daily quota exhausted — degrade silently (caller treats None as "no data")
    try:
        url = f"https://prod.ave-api.com/v2/contracts/{mint}-solana"
        st, data = http_json(url, headers={"X-API-KEY": ave_key})
        d = data.get("data") or {}
        return {
            "risk_score": d.get("risk_score"),
            "holders": d.get("holders"),
            "is_honeypot": d.get("is_honeypot"),
            "has_mint_method": d.get("has_mint_method"),
            "can_take_back_ownership": d.get("can_take_back_ownership"),
            "anti_whale_modifiable": d.get("anti_whale_modifiable"),
            "is_in_dex": d.get("is_in_dex"),
            "pair_lock_percent": d.get("pair_lock_percent"),
            "token_lock_percent": d.get("token_lock_percent"),
            "holder_analysis": d.get("holder_analysis"),
            "owner_percent": d.get("owner_percent"),
            "transfer_tax": d.get("transfer_tax"),
        }
    except Exception:
        return None

# --- NEW PROVIDERS (Oct 17 2026 audit — sol.wales/walletintel/solscan verified live) ---
SOL_WALES = "https://sol.wales/api/v1"
SOL_WALES_HEALTH = "https://sol.wales/api/health"  # NOTE: health lives at /api/health, not /api/v1/health (404)

def sol_wales_health():
    """Sol.wales health — VERIFIED live (x-api-key, 600/min w/ key)."""
    try:
        cfg = load_config()
        key = cfg.get("SOLWALES_KEY")
        hdrs = {"User-Agent": UA}
        if key: hdrs["x-api-key"] = key
        st, d = http_json(SOL_WALES_HEALTH, headers=hdrs)
        return d if isinstance(d, dict) else None
    except Exception:
        return None

def sol_wales_traders(limit=10, source="discovered"):
    """Sol.wales tracked whale wallets — VERIFIED live.
    Returns [{address, label, sol, tokenCount, moves24h, moves7d, lastMoveAt}]."""
    try:
        cfg = load_config()
        key = cfg.get("SOLWALES_KEY")
        hdrs = {"User-Agent": UA}
        if key: hdrs["x-api-key"] = key
        st, d = http_json(f"{SOL_WALES}/traders?source={source}&limit={limit}", headers=hdrs)
        return (d or {}).get("traders") or []
    except Exception:
        return []

def sol_wales_moves_recent(limit=50):
    """Newest moves across all tracked whale wallets — VERIFIED live.
    Returns [{signature, at, kind, mint, amount, trader{address,label}}].
    kinds: sol_in|sol_out|token_in|token_out|swap|other. THE whale tape poll."""
    try:
        cfg = load_config()
        key = cfg.get("SOLWALES_KEY")
        hdrs = {"User-Agent": UA}
        if key: hdrs["x-api-key"] = key
        st, d = http_json(f"{SOL_WALES}/moves/recent?limit={limit}", headers=hdrs)
        return (d or {}).get("moves") or []
    except Exception:
        return []

def walletintel_summary(wallet, max_tx=1000):
    """WalletIntel free wallet PnL summary — VERIFIED (10 req/day, 24h cache).
    Returns {total_realized_pnl_sol, win_rate, strategy, score, unique_tokens} or None."""
    try:
        st, d = http_json(f"https://api.walletintel.dev/wallet/{wallet}/summary?max_tx={max_tx}",
                          timeout=60)
        if isinstance(d, dict) and "error" not in d:
            return d
        return None
    except Exception:
        return None

def walletintel_leaderboard(sort="score", limit=10):
    """WalletIntel top-wallet leaderboard — VERIFIED live."""
    try:
        st, d = http_json(f"https://api.walletintel.dev/leaderboard?sort={sort}&limit={limit}")
        return (d or {}).get("wallets") or []
    except Exception:
        return []


def cabalspy_wallets(chain="solana", wtype="kol", limit=20):
    """CabalSpy labeled KOL/smart/whale roster (10k credits free/mo)."""
    # P2-8 (Oct 20): DNS fail-fast — api.cabalspy.xyz does not resolve from this
    # box; without this every call burns retries for a guaranteed empty list.
    if not _host_ok("api.cabalspy.xyz"):
        return []
    try:
        cfg = load_config()
        key = cfg.get("CABALSPY_KEY")
        if not key: return []
        st, d = http_json(f"https://api.cabalspy.xyz/v1/wallets?blockchain={chain}&type={wtype}&limit={limit}",
                          headers={"Authorization": f"Bearer {key}"})
        return ((d or {}).get("data") or {}).get("wallets") or []
    except Exception:
        return []

def _blix(path, timeout=10):
    """GET a Blix endpoint; returns dict/list or None on failure (breaker for dead paths)."""
    now = time.time()
    if now < _BLIX_DEAD.get(path, 0):
        return None
    try:
        st, d = http_json(f"{BLIX}{path}", timeout=timeout)
        return d
    except Exception:
        _BLIX_DEAD[path] = now + 600  # 10-min breaker per dead endpoint
        return None

def blix_alive():
    d = _blix("/api/health", timeout=6)
    return bool(d and d.get("t"))

def blix_overview():
    d = _blix("/api/overview")
    return d or {}

def blix_token(mint):
    d = _blix(f"/api/token/{mint}")
    return (d or {}).get("token") or None

def blix_holders(mint, top=15):
    """Live holder ledger: {holders:count, top10:share(0-1), top:[{wallet,pct(2.71=2.71%),sol,trades,tag}], dev, snipers, bundle}."""
    d = _blix(f"/api/holders/{mint}?top={top}")
    if not d or "error" in d:
        return None
    return d

def blix_candles(mint, tf="5m", limit=None):
    """MC candles from REAL trades. tf: 5s|15s|1m|5m|15m|1h. rows: [{time,open,high,low,close,value}]."""
    d = _blix(f"/api/candles/{mint}?tf={tf}")
    rows = (d or {}).get("rows") or []
    return rows[-(limit or len(rows)):] if rows else []

def blix_quote(mint, side="buy", amount=1.0, slip=500):
    """Real Jupiter quote: {inSol,outTokens,feeSol,impactPct,avgSol,spotSol,minOut,route,feeBps,solUsd,mcapAfterSol}."""
    d = _blix(f"/api/quote?mint={mint}&side={side}&amount={amount}&slip={slip}")
    return d if isinstance(d, dict) and "error" not in d else None

def blix_signals(limit=20, safety="passed"):
    d = _blix(f"/api/signals?limit={limit}&safety={safety}")
    return d if isinstance(d, list) else []

def blix_narratives():
    d = _blix("/api/narratives")
    return (d or {}).get("themes") or []

def blix_market():
    d = _blix("/api/market")
    return d or {}

def blix_devs(limit=50):
    d = _blix("/api/devs")
    return (d or {}).get("top") or []

def blix_pump_board(range_="24h", limit=30):
    d = _blix(f"/api/pump/board?range={range_}")
    return (d or {}).get("rows") or []

def blix_coin_trades(mint, limit=500):
    d = _blix(f"/api/coin/{mint}/trades?limit={limit}")
    return (d or {}).get("trades") or []

def blix_perf():
    d = _blix("/api/perf")
    return d or {}

def blix_signal_track(kind=None):
    """Empirical 1h track record per signal kind (7.6): {events, up20, down50, median1h, pending1h, unsellable1h}.
    kind None -> overall 'all'. Returns None if blix unreachable/empty."""
    d = _blix("/api/perf")
    if not d:
        return None
    try:
        if kind:
            for row in d.get("byKind") or []:
                if row.get("kind") == kind:
                    ev = row.get("events") or {}
                    return {"events": ev.get("events"), "up20": ev.get("up20"),
                            "down50": ev.get("down50"), "median1h": ev.get("median1h"),
                            "pending1h": ev.get("pending1h")}
            return None
        ev = (d.get("all") or {}).get("events") or {}
        return {"events": ev.get("events"), "up20": ev.get("up20"), "down50": ev.get("down50"),
                "median1h": ev.get("median1h"), "pending1h": ev.get("pending1h")}
    except Exception:
        return None

def blix_research(mint):
    d = _blix(f"/api/research/{mint}")
    return d or {}

def blix_wallet(address):
    d = _blix(f"/api/wallet/{address}")
    return d or {}

# ================= CREDIT/TIER BUDGET MANAGER (Oct 18) =================
# Providers bill in credits or daily limits. Guard every paid call through budget()
# so a burst never blows a monthly bucket. Budgets reset each UTC day and are
# persisted to ~/memecoin-quant/credit_budget.json (thread-safe-ish via file write).
CREDIT_BUDGETS = {
    "helius_wallet":    {"daily": 500,   "cost": 100},   # Wallet API 100cr/call
    "solanatracker":    {"daily": 500,   "cost": 1},     # 10k/mo free -> keep light
    "walletintel":      {"daily": 10,    "cost": 1},
    "ave_top100":       {"daily": 200,   "cost": 1},     # keyed, free-ish w/ limits
    "ave_risk":         {"daily": 200,   "cost": 1},     # P2-7: Ave contract-risk report is keyed — budget it
    "cabalspy":         {"daily": 500,   "cost": 10},    # 10k/mo
}  # fomoapi + madeonsol entries REMOVED Oct 21 (402/403-dead providers deleted)

# P2-8 (Oct 20): shared DNS fail-fast for network-dead providers. A host that
# won't resolve (rugcheck/cabalspy/madeonsol on this box) must not burn 12s of
# retries per call. Mirrors the rugcheck DNS breaker pattern.
_DNS_DEAD = {}  # host -> dead_until ts
def _host_ok(host, ttl=1800):
    """False + circuit-break if host has no DNS (fail-fast, don't burn timeouts)."""
    now = time.time()
    until = _DNS_DEAD.get(host, 0)
    if now < until:
        return False
    try:
        socket.gethostbyname(host)
        return True
    except Exception:
        _DNS_DEAD[host] = now + ttl
        return False

def normalize_channel(name):
    """Canonical channel name (Oct 20 audit): the same Telegram channel was being
    recorded under case variants (PIRBViewTrending vs pirbviewtrending, SeekrTrending
    vs seekrtrending), splitting attribution/backtest stats. Canonical = lowercase,
    'trending' suffix stripped (matches desk_supervisor card display)."""
    if not name:
        return "?"
    n = str(name).strip().lower()
    n = n.replace("trending", "")
    return n
_BUDGET_FILE = os.path.join(DESK, "credit_budget.json")

def _load_budget():
    try:
        return json.load(open(_BUDGET_FILE))
    except Exception:
        return {}

def _save_budget(b):
    try:
        tmp = _BUDGET_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(b, f)
        os.replace(tmp, _BUDGET_FILE)
    except Exception:
        pass

def _day():
    return time.strftime("%Y-%m-%d")

def budget(provider, cost=None, now=None):
    """True if this call is within the daily budget; records the spend. Use BEFORE the call.
    Now optional for testing. Thread-light: file-based counter, best-effort."""
    bp = CREDIT_BUDGETS.get(provider)
    if not bp:
        return True  # unbudgeted provider: allow
    cost = cost if cost is not None else bp["cost"]
    d = _day()
    b = _load_budget()
    day = b.setdefault("_day", d)
    if day != d:
        b = {"_day": d}          # new day, reset
    used = float(b.get(provider, 0.0))
    if used + cost > bp["daily"]:
        return False             # over budget: caller should degrade gracefully
    b[provider] = used + cost
    _save_budget(b)
    return True

def budget_remaining(provider):
    bp = CREDIT_BUDGETS.get(provider)
    if not bp:
        return None
    b = _load_budget()
    if b.get("_day") != _day():
        return bp["daily"]
    return max(0.0, bp["daily"] - float(b.get(provider, 0.0)))

# ================= DEEP-RESEARCH WIRING (Oct 18 — Top-15 capabilities) =================
# 1+2. GoPlus address_security + pre_execution (free, rate-limited)
def goplus_address_security(wallet):
    """Vet a wallet: honeypot_related_address, number_of_malicious_contracts_created,
    phishing, mixer, money_laundering, sanctioned, fake_token, blackmail. Free."""
    try:
        st, d = http_json(f"https://api.gopluslabs.io/api/v1/address_security/{wallet}?chain_id=solana",
                          headers={"User-Agent": UA})
        res = (d or {}).get("result")
        if isinstance(res, dict):
            return {
                "honeypot_related": (res.get("honeypot_related_address") or {}).get("is_honeypot"),
                "malicious_contracts": res.get("number_of_malicious_contracts_created"),
                "phishing": res.get("phishing_activities"),
                "mixer": res.get("money_laundering") or res.get("mixer_related_address"),
                "sanctioned": res.get("sanctioned_related_address"),
                "draining_contract": (res.get("draining_contract_related_address") or {}).get("is_draining_contract"),
                "risk": "RISK" if (res.get("phishing_activities") or res.get("sanctioned_related_address")
                                    or res.get("number_of_malicious_contracts_created")) else "OK",
            }
    except Exception:
        pass
    return None

def goplus_pre_execution(tx_encoded):
    """Simulate an encoded transaction: returns asset_changes w/ risky_address flags.
    Use to PROVE an exit swap executes before sending (sell-capability gate). Free."""
    try:
        st, d = http_json("https://api.gopluslabs.io/api/v1/solana/pre_execution", method="POST",
                          headers={"Content-Type": "application/json", "User-Agent": UA},
                          body={"encoded_transaction": tx_encoded}, timeout=30)
        res = (d or {}).get("result")
        if res:
            return {"asset_changes": res.get("asset_changes"), "risky": res.get("risky_address"),
                    "wait_sec": res.get("wait_sec")}
    except Exception:
        pass
    return None

# 3. Helius Wallet API: funded_by + identity (100cr/call — budgeted)
def helius_wallet(wallet):
    """Who funded a wallet + identity labels. BUDGETED (helius_wallet, 100cr).
    Oct 21 real-engineering: also breaker-gated — a quota-dead Helius (any host)
    must not burn 2 x 20s timeouts on a REST endpoint the public RPC lacks."""
    if not budget("helius_wallet") or helius_down():
        return None
    try:
        st, d = http_json(f"https://api.helius.xyz/v1/wallet/{wallet}/identity",
                          timeout=20)
        if not isinstance(d, dict) or d.get("error"):
            return None
        funded = None
        try:
            st2, d2 = http_json(f"https://api.helius.xyz/v1/wallet/{wallet}/funded_by",
                                timeout=20)
            funded = (d2 or {}).get("result") if isinstance(d2, dict) else None
        except Exception:
            pass
        return {"identity": d, "funded_by": funded}
    except Exception:
        return None

# 4. Shyft failed-tx tape (free REST — sell-block detector)
def st_whales(min_volume=1000, limit=20):
    if not budget("solanatracker"):
        return []
    try:
        key = load_config().get("SOLANATRACKER_KEY")
        req = urllib.request.Request(
            f"https://data.solanatracker.io/trades/whales?minVolume={min_volume}&limit={limit}",
            headers={"x-api-key": key, "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        return (d or {}).get("trades") or []
    except Exception:
        return []

def st_kol_trades(limit=20):
    if not budget("solanatracker"):
        return []
    try:
        key = load_config().get("SOLANATRACKER_KEY")
        req = urllib.request.Request(f"https://data.solanatracker.io/trades/kol?limit={limit}",
                                     headers={"x-api-key": key, "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        return (d or {}).get("trades") or []
    except Exception:
        return []

# 7. Ave top-100 holders (per-holder PnL + funding attribution) — keyed
def ave_top100_holders(mint):
    if not budget("ave_top100"):
        return []
    try:
        key = load_config().get("AVE_KEY")
        st, d = http_json(f"https://prod.ave-api.com/v2/tokens/top100/{mint}-solana",
                          headers={"X-API-KEY": key, "User-Agent": UA}, timeout=25)
        data = (d or {}).get("data") or []
        if isinstance(data, list):
            return data[:100]
        return []
    except Exception:
        return []

# 9. Rugcheck insiders graph + lockers (insiders free w/ flux key; lockers PAID -> graceful)
def rugcheck_insiders(mint):
    # Same DNS circuit breaker as rugcheck(): api.rugcheck.xyz currently has no
    # DNS record from this environment; avoid a 12s retry storm per scan.
    try:
        socket.gethostbyname("api.rugcheck.xyz")
    except Exception:
        return None
    try:
        st, d = http_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/insiders/graph?api_key={load_config().get('FLUX_KEY')}", timeout=20)
        return d or None   # returns a LIST of {net_id, network_type, nodes[], links[]}
    except Exception:
        return None

def rugcheck_lockers(mint):
    try:
        st, d = http_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/lockers?api_key={load_config().get('FLUX_KEY')}", timeout=20)
        return (d or {}).get("data") or d
    except Exception:
        return None

def rugcheck_stats_new(limit=20):
    try:
        st, d = http_json(f"https://api.rugcheck.xyz/v1/stats/new_tokens?api_key={load_config().get('FLUX_KEY')}", timeout=20)
        data = (d or {}).get("data") if isinstance(d, dict) else None
        return (data or [])[:limit]
    except Exception:
        return []

# 11. DexScreener metas trending (free)
def dexscreener_metas_trending():
    try:
        st, d = http_json("https://api.dexscreener.com/metas/trending/v1", timeout=20)
        return d if isinstance(d, list) else []
    except Exception:
        return []

# 12. DexPaprika reserve stream (SSE — one-shot read helper + poll usage)
def dexpaprika_reserves(pool_id):
    """Read a single reserve snapshot (SSE is push; this is the REST pool detail fallback).
    P2-8 (Oct 20): DNS fail-fast — api.dexpaprika.com does not resolve from this box.
    For continuous liq-pull monitoring, poll or run the SSE reader (see scripts)."""
    if not _host_ok("api.dexpaprika.com"):
        return None
    try:
        st, d = http_json(f"https://api.dexpaprika.com/networks/solana/pools/{pool_id}",
                          headers={"Authorization": load_config().get("DEXPAPRIKA_KEY") or ""}, timeout=20)
        return (d or {}).get("data") or d
    except Exception:
        return None

# 13. CabalSpy bundle/signals/connections (10k cr/mo, network-blocked here -> graceful)
def cabalspy_bundle(mint):
    if not budget("cabalspy"):
        return None
    if not _host_ok("api.cabalspy.xyz"):
        return None
    try:
        key = load_config().get("CABALSPY_KEY")
        if not key: return None
        st, d = http_json(f"https://api.cabalspy.xyz/v1/bundle?blockchain=solana&mint={mint}&api_key={key}", timeout=15)
        return (d or {}).get("data") or d
    except Exception:
        return None

def cabalspy_signals(min_win_rate=0.5):
    if not budget("cabalspy"):
        return []
    if not _host_ok("api.cabalspy.xyz"):
        return []
    try:
        key = load_config().get("CABALSPY_KEY")
        if not key: return []
        st, d = http_json(f"https://api.cabalspy.xyz/v1/signals?blockchain=solana&min_win_rate={min_win_rate}&api_key={key}", timeout=15)
        return (d or {}).get("data", {}).get("signals") or []
    except Exception:
        return []

def cabalspy_connections(wallet):
    if not budget("cabalspy"):
        return []
    if not _host_ok("api.cabalspy.xyz"):
        return []
    try:
        key = load_config().get("CABALSPY_KEY")
        if not key: return []
        st, d = http_json(f"https://api.cabalspy.xyz/v1/wallets/connections?blockchain=solana&address={wallet}&api_key={key}", timeout=15)
        return (d or {}).get("data", {}).get("connections") or []
    except Exception:
        return []

# 14. MadeOnSol buyer-quality / signals-performance / depth (CF-blocked here -> graceful)

def jupiter_quote_v2(mint, usd=20, slippage_bps=500):
    """Swap v2 quote (SOL->mint). Returns dict w/ priceImpactPct fraction, outAmount, or None."""
    try:
        cfg = load_config()
        key = cfg.get("JUPITER_KEY")
        # SOL/USD via DexScreener (price.jup.ag doesn't resolve here)
        s = 150.0
        try:
            p = dex_pair(SOL_MINT)
            s = float((p or {}).get("priceUsd") or 150.0)
        except Exception:
            pass
        amount = int(round(usd / s * 1e9))
        url = (f"https://api.jup.ag/swap/v2/quote?inputMint={SOL_MINT}&outputMint={mint}"
               f"&amount={amount}&slippageBps={slippage_bps}")
        hdrs = {"User-Agent": UA}
        if key: hdrs["x-api-key"] = key
        st, d = http_json(url, headers=hdrs, timeout=20)
        if isinstance(d, dict) and "outAmount" in d:
            return d
        return None
    except Exception:
        return None

def log_event(entry, fname=None):
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    entry["ts"] = time.time()
    day = time.strftime("%Y-%m-%d")
    path = os.path.join(JOURNAL_DIR, fname or f"{day}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")

def db():
    con = sqlite3.connect(AEGIS_DB, timeout=15)
    con.row_factory = sqlite3.Row
    # Concurrent access (2-min cron + crawler + monitor) needs WAL + busy timeout,
    # otherwise writers collide with "database is locked".
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=15000")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con

def db_retry(fn, attempts=4, delay=1.5):
    """Run fn(con) with retry-on-locked; fn must close con itself."""
    import time as _t
    last = None
    for i in range(attempts):
        try:
            return fn(db())
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            last = e
            _t.sleep(delay * (i + 1))
    raise last

def init_schema():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS coins(
        mint TEXT PRIMARY KEY, symbol TEXT, chain TEXT DEFAULT 'solana', source TEXT,
        call_ts REAL, mc_at_call REAL, liq_at_call REAL, fill_time_min REAL,
        deployer TEXT, score REAL, verdict TEXT, strategy TEXT, status TEXT DEFAULT 'live',
        updated_ts REAL);
    CREATE TABLE IF NOT EXISTS call_metrics(
        mint TEXT PRIMARY KEY, symbol TEXT, channel TEXT, call_ts REAL,
        mc REAL, liq REAL, fill_class TEXT, fill_min REAL,
        aegis_score REAL, verdict TEXT, flags_json TEXT,
        ave_risk_score REAL, top10_pct REAL, smart_money_count INTEGER DEFAULT 0,
        buy_sell_b5 REAL, buys5 INTEGER, sells5 INTEGER, vol_q_flags TEXT,
        mc_delta_pct REAL, price REAL, created_ts REAL);
    CREATE TABLE IF NOT EXISTS deployers(
        deployer TEXT PRIMARY KEY, first_seen REAL, coin_count INTEGER DEFAULT 0,
        rug_count INTEGER DEFAULT 0, pump_count INTEGER DEFAULT 0,
        score_offset REAL DEFAULT 0, wallets_json TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS wallets(
        wallet TEXT PRIMARY KEY, role TEXT, tags TEXT, first_seen REAL, last_seen REAL,
        buys INTEGER DEFAULT 0, sells INTEGER DEFAULT 0, stats_json TEXT DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS funding_links(funder TEXT, deployer TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS first_buyers(coin_mint TEXT, wallet TEXT, rank INTEGER, ts REAL, size REAL);
    CREATE TABLE IF NOT EXISTS actors(
        actor TEXT PRIMARY KEY, class TEXT, confidence REAL, strategy TEXT, notes TEXT, updated_ts REAL);
    CREATE TABLE IF NOT EXISTS scan_log(
        mint TEXT, ts REAL, score REAL, verdict TEXT, strategy TEXT, checks_json TEXT);
    CREATE TABLE IF NOT EXISTS followups(
        mint TEXT PRIMARY KEY, due_ts REAL, kind TEXT, note TEXT, result TEXT, done INTEGER DEFAULT 0,
        outcome TEXT);
    CREATE TABLE IF NOT EXISTS positions(
        mint TEXT PRIMARY KEY, entry_px REAL, size REAL, entry_mc REAL, entry_liq REAL,
        opened_ts REAL, strategy TEXT, conviction INTEGER DEFAULT 0, notes TEXT);
    CREATE TABLE IF NOT EXISTS deep_cache(
        mint TEXT PRIMARY KEY, report_json TEXT, ts REAL);
    """)
    # Audit F1: first_buyers must be unique per (mint, wallet, rank).
    # Without this, INSERT OR REPLACE is just INSERT on every rescan.
    try:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_fb_uniq ON first_buyers(coin_mint, wallet, rank)")
    except Exception:
        pass  # existing duplicate rows are repaired by maintenance before index creation
    # audit P1 (Oct 18): older DBs lack the `outcome` column -> filter_precision's
    # `SELECT outcome FROM followups` was failing silently. Migrate if missing.
    cols = [r[1] for r in con.execute("PRAGMA table_info(followups)")]
    if "outcome" not in cols:
        try:
            con.execute("ALTER TABLE followups ADD COLUMN outcome TEXT")
            log_event({"event": "SCHEMA_MIGRATION", "table": "followups", "added": "outcome"})
        except Exception:
            pass
    con.commit(); con.close()

def mark_status(mint, status):
    con = db()
    con.execute("UPDATE coins SET status=?, updated_ts=? WHERE mint=?", (status, time.time(), mint))
    con.commit(); con.close()

def remember_call_meta(mint, channel, call_ts, symbol=None, mc=None, liq=None):
    """F3 (Oct 19): persist WHERE/WEN a call was first seen on the coins row.

    Previously coins.call_ts/source were NULL for every row (1025/1025) — the
    schema advertised call timing that nothing wrote, and score_coin's
    INSERT OR REPLACE wiped them on every rescan. This upserts the immutable
    call metadata (first sighting wins) so timing/channel rules have data."""
    if not mint:
        return
    try:
        con = db()
        con.execute("""INSERT INTO coins(mint, symbol, source, call_ts, mc_at_call, liq_at_call)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(mint) DO UPDATE SET
                         symbol=COALESCE(coins.symbol, excluded.symbol),
                         source=COALESCE(coins.source, excluded.source),
                         call_ts=COALESCE(coins.call_ts, excluded.call_ts),
                         mc_at_call=COALESCE(coins.mc_at_call, excluded.mc_at_call),
                         liq_at_call=COALESCE(coins.liq_at_call, excluded.liq_at_call)""",
                    (mint, symbol, channel, call_ts, mc, liq))
        con.commit(); con.close()
    except Exception:
        pass

# --- deep-cache (latency audit Oct 19, fix #4): AEGIS deep=True walks the deployer's
# chain history (~46s). Cache the full report per mint for 1h so repeat deep scans
# (paper monitor, re-alerts) are instant; first scan still warms it. ---
DEEP_CACHE_TTL = 3600  # 1 hour

def deep_cache_get(mint):
    try:
        con = db()
        row = con.execute("SELECT report_json, ts FROM deep_cache WHERE mint=?", (mint,)).fetchone()
        con.close()
        if row and time.time() - row["ts"] <= DEEP_CACHE_TTL:
            return json.loads(row["report_json"])
    except Exception:
        pass
    return None

def deep_cache_put(mint, report):
    try:
        con = db()
        con.execute("INSERT OR REPLACE INTO deep_cache(mint, report_json, ts) VALUES(?,?,?)",
                    (mint, json.dumps(report), time.time()))
        con.commit(); con.close()
    except Exception:
        pass

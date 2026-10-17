#!/usr/bin/env python3
"""Memecoin Quant Desk — shared utilities & config (stdlib only)."""
import json, os, re, time, sqlite3, urllib.request, urllib.error

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
           "FOMO_KEY": None, "BITQUERY_KEY": None, "COINSTATS_KEY": None,
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
    m = re.search(r"FomoAPI:\s*(fapi_[A-Za-z0-9]+)", text)
    if m: cfg["FOMO_KEY"] = m.group(1)
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
    for attempt in range(retries):
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

def rpc(method, params, rpc_url=None, retries=2):
    """RPC call through the shared limiter + retry (audit P2-6: rpc() bypassed
    the central _limiter and had no retry; now both are consistent)."""
    cfg = load_config()
    url = rpc_url or f"https://mainnet.helius-rpc.com/?api-key={cfg['HELIUS_KEY']}"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for attempt in range(retries + 1):
        _limiter.acquire(_host_of(url))
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode()).get("result")
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
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
    url = f"https://mainnet.helius-rpc.com/v0/addresses/{wallet}/transactions?api-key={cfg['HELIUS_KEY']}&limit={limit}"
    if before: url += f"&before={before}"
    return http_json(url, timeout=30)[1]

def helius_asc_txs(wallet, limit=10):
    """Forensic: FIRST transactions of a wallet chronologically (mother-wallet attribution).
    Eliminates the N+1 problem — one call instead of paging 25K signatures."""
    cfg = load_config()
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

FOMO_NETWORKS = {"solana": 1399811149, "robinhood": 4663, "base": 8453, "bsc": 56}

def fomo_holders(mint, chain="solana"):
    """FomoAPI smart-money holders — REQUIRES ?network=<chainId> (probed: 200 only with it)."""
    cfg = load_config()
    fk = cfg.get("FOMO_KEY")
    if not fk: return []
    net = FOMO_NETWORKS.get(chain, FOMO_NETWORKS["solana"])
    try:
        url = f"https://api.fomoapi.io/token/{mint}/holders?network={net}&limit=25"
        st, data = http_json(url, headers={"Authorization": f"Bearer {fk}"})
        if isinstance(data, dict) and data.get("available") is False:
            return []
        return data.get("holders") or (data.get("data") or {}).get("holders") or []
    except Exception:
        return []

def fomo_user(handle):
    """FomoAPI trader resolution (2,500 credits — call sparingly, cache in actors)."""
    cfg = load_config()
    fk = cfg.get("FOMO_KEY")
    if not fk: return None
    try:
        url = f"https://api.fomoapi.io/v2/users/{handle}"
        st, data = http_json(url, headers={"Authorization": f"Bearer {fk}"}, retries=2)
        return data if isinstance(data, dict) else None
    except Exception:
        return None

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

def fomo_board(mint, chain="solana"):
    """FomoAPI token boards (working endpoints) — find a mint on trending/most-held/graduated.
    Returns {symbol, holders, network, marketCapUsd} or None. CONTEXT ONLY (never an entry signal)."""
    cfg = load_config()
    fk = cfg.get("FOMO_KEY")
    if not fk: return None
    want_net = FOMO_NETWORKS.get(chain, 1399811149)
    for board in ("trending", "most-held", "graduated"):
        try:
            url = f"https://api.fomoapi.io/v2/leaderboard/tokens/{board}?limit=150"
            st, data = http_json(url, headers={"Authorization": f"Bearer {fk}"})
            for t in (data.get("tokens") or []):
                tk = t.get("token") or {}
                if str(tk.get("address", "")).lower() == mint.lower():
                    return {"symbol": tk.get("symbol"), "holders": t.get("holders"),
                            "network": t.get("network"), "marketCapUsd": t.get("marketCapUsd"),
                            "board": board}
        except Exception:
            continue
    return None

def coinstats_balance(wallet, connection="solana"):
    """CoinStats wallet balance (X-API-KEY). Returns LIST directly.

    ⚠️ Oct 16 2026: free-tier credits exhausted -> every endpoint 406. Returns []
    but logs a degraded marker once so the desk knows this path is dead, not silent.
    """
    cfg = load_config()
    ck = cfg.get("COINSTATS_KEY")
    if not ck: return []
    try:
        url = f"https://openapiv1.coinstats.app/v1/wallet/balance?address={wallet}&connectionId={connection}"
        st, data = http_json(url, headers={"X-API-KEY": ck, "Accept": "application/json"})
        if isinstance(data, list):
            return data
        res = data.get("result") or []
        return res if isinstance(res, list) else []
    except Exception:
        # 406 = credits exhausted; mark degraded once so monitoring knows
        try:
            log_event({"event": "COINSTATS_DEGRADED", "detail": "406 credits limit"})
        except Exception:
            pass
        return []

def bitquery_imbalance(mint, limit=30):
    """USD-weighted buy/sell imbalance via Bitquery V2 — DOCUMENTED query shape (verified).
    Sums Trade.Side.AmountInUSD by Trade.Side.Type (buy/sell)."""
    q = ('{ Solana { DEXTradeByTokens(limit: {count: %d}, orderBy: {descending: Block_Time}, '
         'where: {Trade: {Currency: {MintAddress: {is: "%s"}}, Price: {gt: 0}}, '
         'Transaction: {Result: {Success: true}}}) '
         '{ Block { allTime: Time } Trade { Side { Type AmountInUSD Amount } Price } } } }') % (limit, mint)
    d = bitquery_graphql(q)
    if not d or not d.get("data"):
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

def dex_pair(mint, allow_fallback=True):
    """Best pair for a mint from DexScreener (throttled) with GeckoTerminal fallback on 429."""
    _dex_throttle()
    try:
        st, data = http_json(DEXSCREENER.format(mint))
    except Exception as e:
        if allow_fallback and ("429" in str(e) or "Too Many" in str(e)):
            return _gecko_pair(mint)
        raise
    pairs = data.get("pairs") or []
    pairs = [p for p in pairs if p.get("priceUsd") or p.get("marketCap")]
    if not pairs:
        return _gecko_pair(mint) if allow_fallback else None
    pairs.sort(key=lambda p: (p.get("liquidity", {}).get("usd") or 0) + (p.get("volume", {}).get("h24") or 0), reverse=True)
    return pairs[0]

def rugcheck(mint, full=False):
    cfg = load_config()
    url = RUGCHECK.format(mint, "/report" if full else "/report/summary") + f"?api_key={cfg['FLUX_KEY']}"
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

def coinstats_balance(wallet, connection="solana"):
    """CoinStats wallet balance (X-API-KEY). Returns LIST directly.

    ⚠️ Oct 16 2026: free-tier credits exhausted -> every endpoint 406. Circuit
    breaker: after the first 406 the path returns [] for 30 min without calling
    (audit P2-5). Logs a degraded marker once so the desk knows, not silently.
    """
    if time.time() < _COINSTATS_DEAD_UNTIL[0]:
        return []
    cfg = load_config()
    ck = cfg.get("COINSTATS_KEY")
    if not ck: return []
    try:
        url = f"https://openapiv1.coinstats.app/v1/wallet/balance?address={wallet}&connectionId={connection}"
        st, data = http_json(url, headers={"X-API-KEY": ck, "Accept": "application/json"})
        if isinstance(data, list):
            return data
        res = data.get("result") or []
        return res if isinstance(res, list) else []
    except Exception:
        # 406 = credits exhausted; mark degraded once + circuit-break 30 min
        _COINSTATS_DEAD_UNTIL[0] = time.time() + 1800
        try:
            log_event({"event": "COINSTATS_DEGRADED", "detail": "406 credits limit"})
        except Exception:
            pass
        return []

# mint-vs-wallet discrimination: a valid SPL token MINT is owned by the Token
# program (or is a token-2022/metadata mint); a plain wallet is owned by the
# System program. Cached per address (Telegram-intake boundary, audit §3).
def is_token_mint(address, timeout_cache=300):
    """Return True if `address` is an SPL token/Token-2022/Metaplex MINT account.

    Wallets are owned by the System program; token mints are owned by the
    Token programs. Cached per address (Telegram-intake boundary, audit §3).
    """
    if not (32 <= len(str(address)) <= 44) or address == "So11111111111111111111111111111111111111112":
        return False
    cached = ttl_cache_get(("mint?", address), timeout_cache)
    if cached is not None:
        return cached
    TOKEN_PROGRAMS = {
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",   # Token-2022 (correct address)
        "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",    # Metaplex metadata
    }
    try:
        acc = rpc("getAccountInfo", [address, {"encoding": "jsonParsed"}])
        owner = (acc or {}).get("value", {}).get("owner") or ""
        ok = owner in TOKEN_PROGRAMS
        ttl_cache_put(("mint?", address), ok)
        return ok
    except Exception:
        return False

def ave_risk(mint):
    """Ave.ai Contract Risk Detection Report — verified working (X-API-KEY header).
    https://prod.ave-api.com/v2/contracts/{CA}-{chain}"""
    cfg = load_config()
    import re as _re
    ave_key = cfg.get("AVE_KEY")
    if not ave_key:
        return None
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

def solscan_chaininfo():
    """Solscan free chaininfo — VERIFIED live (token JWT header, NOT Bearer)."""
    try:
        cfg = load_config()
        jwt = cfg.get("SOLSCAN_JWT")
        hdrs = {"User-Agent": UA}
        if jwt: hdrs["token"] = jwt
        st, d = http_json("https://public-api.solscan.io/chaininfo", headers=hdrs)
        return (d or {}).get("data")
    except Exception:
        return None

def cabalspy_wallets(chain="solana", wtype="kol", limit=20):
    """CabalSpy labeled KOL/smart/whale roster (10k credits free/mo)."""
    try:
        cfg = load_config()
        key = cfg.get("CABALSPY_KEY")
        if not key: return []
        st, d = http_json(f"https://api.cabalspy.xyz/v1/wallets?blockchain={chain}&type={wtype}&limit={limit}",
                          headers={"Authorization": f"Bearer {key}"})
        return ((d or {}).get("data") or {}).get("wallets") or []
    except Exception:
        return []

def madeonsol_kol_feed(limit=10):
    """MadeOnSol KOL trade feed — CF-blocked from this box; returns [] here.
    Works from user-network/x402. Base: madeonsol.com/api/v1 (NOT api.madeonsol.com)."""
    try:
        cfg = load_config()
        key = cfg.get("MADEONSOL_KEY")
        if not key: return []
        st, d = http_json("https://madeonsol.com/api/v1/kol/feed?limit=%d" % limit,
                          headers={"Authorization": f"Bearer {key}"})
        return (d or {}).get("trades") or []
    except Exception:
        return []

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
    """)
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

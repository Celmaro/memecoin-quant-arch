#!/usr/bin/env python3
"""Signal engine: slow-graduation, wash-trade/LPI detector, fragility score (ME2F-lite), OHLCV risk."""
import time, statistics, json, os
from desk_common import dex_pair, rpc, rugcheck, db, load_config, http_json, UA

# ---------- 1. Slow-graduation (bonding-curve fill time) ----------

def estimate_fill_time(mint, pair_created_ms=None, max_pages=20):
    """Curve fill ≈ (pair creation) - (first tx on mint).
    Uses Helius asc archival lookup (1 call) when possible; falls back to paged
    getSignaturesForAddress. Returns minutes (float) or None."""
    if not pair_created_ms:
        pair = dex_pair(mint)
        if not pair: return None
        pair_created_ms = pair.get("pairCreatedAt")
    if not pair_created_ms: return None
    grad_ts = pair_created_ms / 1000.0
    # Fast path: Helius asc (first tx on the mint = creation)
    try:
        txs = helius_asc_txs(mint, limit=5)
        if isinstance(txs, list) and txs:
            first_ts = txs[0].get("timestamp")
            if first_ts:
                return max(0.0, (grad_ts - first_ts) / 60.0)
    except Exception:
        pass
    # Fallback: paged signatures (same as before)
    before = None
    oldest = grad_ts
    crossed = False
    for _ in range(max_pages):
        p = [mint, {"limit": 1000}]
        if before: p[1]["before"] = before
        res = rpc("getSignaturesForAddress", p) or []
        if not res: break
        bt = res[-1].get("blockTime")
        if bt:
            oldest = min(oldest, bt)
            if bt < grad_ts:
                crossed = True
                break  # pre-graduation history reached
        before = res[-1]["signature"]
        if len(res) < 1000: break
    if not crossed:
        # Never found pre-graduation history in the page budget — unknown, not 0
        return None
    return max(0.0, (grad_ts - oldest) / 60.0)

def fill_time_class(fill_min):
    """90s-20min = bots; hours = people (research: 97,146 graduations)."""
    if fill_min is None: return "unknown"
    if fill_min < 3: return "bots"          # 3-min fill => cluster pushed curve closed
    if fill_min < 60: return "fast"         # sub-hour: mostly automated
    if fill_min < 360: return "people"      # hours: separate wallets, real conviction
    return "very_slow"

# ---------- 2. Wash-trade / LPI detector ----------

def wash_lpi_check(mint, pair=None):
    """Flags from DexScreener + Helius tx stats.
    Wash tells: vol spike >> baseline with few wallets; uniform intervals (CV<0.3);
    LPI: tiny buys -> dramatic price move (buy-size vs move anomaly)."""
    if not pair:
        pair = dex_pair(mint)
        if not pair: return {"ok": False}
    vol = pair.get("volume") or {}
    chg = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}
    m5 = txns.get("m5") or {}
    flags = []
    v_m5, v_h1, v_h24 = vol.get("m5") or 0, vol.get("h1") or 0, vol.get("h24") or 0
    # Wash: m5 volume > 10% of h24 volume (spike concentrated in minutes) — rough proxy
    if v_h24 > 0 and v_m5 > 0 and (v_m5 / v_h24) > 0.10:
        flags.append("volume_spike_concentrated(5m>10%_of_24h)")
    # Wash: very few txs carrying big volume in 5m
    b5, s5 = m5.get("buys") or 0, m5.get("sells") or 0
    if v_m5 > 2000 and (b5 + s5) < 15:
        flags.append("big_volume_few_traders")
    # LPI: tiny buys triggering big moves (proxy: high % chg with few txns)
    if (b5 + s5) > 0 and chg.get("m5") and abs(chg["m5"]) > 25 and (b5 + s5) < 25:
        flags.append("lpi_suspect(price_move>>trade_count)")
    # Interval uniformity would need raw tx timestamps; we approximate via buy/sell shape
    return {"ok": True, "flags": flags, "vol_m5": v_m5, "vol_h1": v_h1, "vol_h24": v_h24,
            "b5": b5, "s5": s5, "chg5": chg.get("m5")}

# ---------- 4. OHLCV ingestion (DexPaprika) → realized vol + max drawdown ----------

def fetch_ohlcv_dexpa(mint, interval="1h", limit=48):
    """OHLCV for risk math — DexPaprika pool OHLCV via pools/search (verified, keyed).
    Token-level OHLCV is paid (403); this pool route returns a bare list of candles."""
    try:
        hdrs = {}
        key = load_config().get("DEXPAPRIKA_KEY")
        if key: hdrs["Authorization"] = key
        # 1) find the token's pool
        st_s, s = http_json(f"https://api.dexpaprika.com/networks/solana/pools/search?token_address={mint}&limit=1", headers=hdrs)
        results = (s or {}).get("results") or []
        if results:
            pool = results[0].get("id")
            url = (f"https://api.dexpaprika.com/networks/solana/pools/{pool}/ohlcv"
                   f"?start=-{max(2, limit)}h&limit={limit}&interval=1h")
            st, data = http_json(url, headers=hdrs)
            if isinstance(data, list):
                candles = []
                for row in data:
                    if isinstance(row, dict) and (row.get("price_close") is not None or row.get("close") is not None):
                        candles.append(row)
                if candles:
                    return candles
    except Exception:
        pass
    # fallback: GeckoTerminal pool OHLCV (free; may 429 under load)
    try:
        t = http_json(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1&limit=1")
        pools = (t[1].get("data") or [])
        if pools:
            pool_id = pools[0]["id"].split("/")[-1]
            o = http_json(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_id}/ohlcv?timeframe=hour&aggregate=1&limit={limit}")
            rows = ((o[1].get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            candles = []
            for row in rows:
                if isinstance(row, list) and len(row) >= 6:
                    candles.append({"time": row[0], "price_open": row[1], "price_high": row[2],
                                    "price_low": row[3], "price_close": row[4], "volume": row[5]})
            return candles
    except Exception:
        pass
    return []

def ohlcv_risk(candles):
    """From hourly candles: realized vol (std of log returns) + max drawdown."""
    if not candles or len(candles) < 3:
        return None
    closes = []
    for c in candles:
        v = c.get("price_close") or c.get("close") or c.get("price")
        if v: closes.append(float(v))
    if len(closes) < 3:
        return None
    rets = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0 and closes[i] > 0:
            rets.append((closes[i] / closes[i-1]) - 1)
    if not rets: return None
    rv_h = statistics.pstdev(rets) * 100  # hourly realized vol %
    peak = closes[0]; mdd = 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, (c - peak) / peak * 100)
    return {"realized_vol_h": round(rv_h, 2), "mdd_pct": round(mdd, 2), "n": len(closes)}

# ---------- 5. Fragility (ME2F-lite: whale concentration + volatility + sentiment proxy) ----------

def fragility_score(mint, pair=None):
    """0-100 fragility: top-10 concentration (Rugcheck) + vol persistence (OHLCV when available)
    + sentiment proxy (holder churn via txns direction)."""
    if not pair:
        pair = dex_pair(mint)
        if not pair: return None
    rc = rugcheck(mint, full=True) or {}
    top10 = rc.get("top10_pct")
    chg = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}
    buys, sells = h24.get("buys") or 0, h24.get("sells") or 0

    f = 20.0
    reasons = []
    if isinstance(top10, (int, float)):
        if top10 > 70: f += 30; reasons.append(f"top10={top10:.0f}%")
        elif top10 > 50: f += 15; reasons.append(f"top10={top10:.0f}%")
    # volatility persistence: OHLCV realized vol if we can get it
    ohlcv = fetch_ohlcv_dexpa(mint)
    rv = ohlcv_risk(ohlcv)
    if rv:
        if rv["realized_vol_h"] > 8: f += 20; reasons.append(f"rv_h={rv['realized_vol_h']:.1f}%")
        elif rv["realized_vol_h"] > 4: f += 10; reasons.append(f"rv_h={rv['realized_vol_h']:.1f}%")
        if rv["mdd_pct"] < -40: f += 15; reasons.append(f"MDD={rv['mdd_pct']:.0f}%")
    else:
        c5, c1 = abs(chg.get("m5") or 0), abs(chg.get("h1") or 0)
        if c5 > 15 and c1 > 40: f += 25; reasons.append(f"vol_persist(m5={c5:.0f}%,h1={c1:.0f}%)")
        elif c5 > 10 or c1 > 25: f += 12; reasons.append("vol_high")
    # churn proxy: heavy sell side
    if buys + sells > 50 and sells > buys * 1.5:
        f += 15; reasons.append(f"sell_heavy(b:{buys}/s:{sells})")
    f = min(100, f)
    out = {"fragility": round(f, 1), "reasons": reasons, "top10_pct": top10}
    if rv: out["ohlcv"] = rv
    return out

# ---------- 6. Holder concentration suite (Rugcheck topHolders → top1/10/20, HHI, pseudo-Gini) ----------

def holder_concentration(mint):
    """Top-holder concentration from Rugcheck full report (verified free endpoint).
    Returns top1/top10/top20 share, HHI, pseudo-Gini (top-N lower bound), totalHolders.
    None when report unavailable."""
    rc = rugcheck(mint, full=True) or {}
    th = rc.get("topHolders") or []
    if not th:
        return None
    pcts = sorted([float(h.get("pct") or 0) for h in th], reverse=True)
    return {
        "top1_pct": round(pcts[0], 2),
        "top10_pct": round(sum(pcts[:10]), 2),
        "top20_pct": round(sum(pcts[:20]), 2),
        "hhi": round(sum(p * p for p in pcts), 1),
        "pseudo_gini": round(_gini(pcts), 3),
        "n_holders_reported": len(pcts),
        "total_holders": rc.get("totalHolders"),
    }

def _gini(pcts):
    """Gini on a partial (top-N) distribution — LOWER-BOUND proxy; normalized to [0,1]."""
    if len(pcts) < 2:
        return 0.0
    x = sorted(float(v) for v in pcts)
    n = len(x)
    total = sum(x)
    if total <= 0: return 0.0
    # Standard sorted-observation Gini: 2Σ(i*x_i)/(nΣx) - (n+1)/n
    g = (2 * sum((i + 1) * v for i, v in enumerate(x)) / (n * total)) - ((n + 1) / n)
    return max(0.0, min(1.0, g))

# ---------- 7. Holder growth (Helius enhanced txs on mint — free, no quota) ----------

def holder_growth(mint, limit=100):
    """Unique buyer/seller growth from Helius enhanced txs touching the mint.
    Excludes the DEX pair account and the mint itself. Returns sampled-window counts
    bucketed 1h/6h/24h, or None when no data. Best-effort (limit=100 recent txs)."""
    cfg = load_config()
    if not cfg.get("HELIUS_KEY"):
        return None
    try:
        st, data = http_json(
            f"https://mainnet.helius-rpc.com/v0/addresses/{mint}/transactions?api-key={cfg['HELIUS_KEY']}&limit={limit}",
            timeout=30)
        if not isinstance(data, list) or not data:
            return None
    except Exception:
        return None
    pair = dex_pair(mint)
    pair_addr = ((pair or {}).get("pairAddress") or "").lower()
    mint_l = mint.lower()
    buyers, sellers = {}, {}
    tx_ok = 0
    for tx in data:
        ts = tx.get("timestamp") or 0
        for tt in (tx.get("tokenTransfers") or []):
            if str(tt.get("mint") or "").lower() != mint_l:
                continue
            frm = str(tt.get("fromUserAccount") or "").lower()
            to = str(tt.get("toUserAccount") or "").lower()
            if frm and frm != pair_addr and frm != mint_l:
                sellers[frm] = max(sellers.get(frm, 0), ts)
            if to and to != pair_addr and to != mint_l:
                buyers[to] = max(buyers.get(to, 0), ts)
            tx_ok += 1
    now = time.time()
    def in_window(d, hours):
        return sum(1 for v in d.values() if now - v <= hours * 3600)
    return {
        "unique_buyers": len(buyers),
        "unique_sellers": len(sellers),
        "buyers_1h": in_window(buyers, 1),
        "buyers_6h": in_window(buyers, 6),
        "buyers_24h": in_window(buyers, 24),
        "txs_sampled": len(data),
        "transfers_sampled": tx_ok,
        "sample_span_h": round((now - min([ts for tx in data if tx.get("timestamp")] or [now])) / 3600, 2),
    }

# ---------- Holder churn / retention (Helius DAS getTokenAccounts) ----------

_HOLDER_SNAPSHOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "holder_snapshots.json")

def _load_holder_snapshot():
    try:
        return json.load(open(_HOLDER_SNAPSHOT_FILE))
    except Exception:
        return {}

def _save_holder_snapshot(d):
    # Keep only the most recent 60 mints (each snapshot ~60KB; cap disk growth)
    if len(d) > 60:
        # keep newest 60 by snapshot ts
        by_ts = sorted(d.items(), key=lambda kv: (kv[1].get("ts") or 0), reverse=True)
        d = dict(by_ts[:60])
    with open(_HOLDER_SNAPSHOT_FILE, "w") as f:
        json.dump(d, f)

def holder_churn(mint, limit=1000):
    """Holder retention/churn from Helius DAS getTokenAccounts.

    Snapshots the current holder-address set (owner + amount) for `mint`,
    compares against the last snapshot stored on disk, and returns:
      - current holder count (sampled)
      - retention = |t0 ∩ t1| / |t0|    (fraction of previous holders still holding)
      - churned = holders in t0 who disappeared
      - holders_added = new holders not in t0
    Snapshot depth is fixed at ONE page of `limit` (default 1000) so runs are
    comparable; DAS paginates but the first 1000 sorted accounts are stable.
    Callers should throttle (position_monitor already refreshes holder_growth every 300s).
    Returns None if the snapshot or DAS query fails.
    """
    cfg = load_config()
    if not cfg.get("HELIUS_KEY"):
        return None
    snap = _load_holder_snapshot().get(mint) or {}
    # 1) Fetch current holders (DAS getTokenAccounts, single comparable page)
    holders = {}
    try:
        _st, d = http_json(
            f"https://mainnet.helius-rpc.com/?api-key={cfg['HELIUS_KEY']}",
            timeout=30, retries=2, method="POST",
            body={"jsonrpc": "2.0", "id": "churn", "method": "getTokenAccounts",
                  "params": {"mint": mint, "limit": min(limit, 1000), "page": 1,
                             "displayOptions": {"showZeroBalance": False}}},
            headers={"Content-Type": "application/json"})
        res = (d or {}).get("result") or {}
        tas = res.get("token_accounts") or []
        for ta in tas:
            owner = (ta.get("owner") or "").lower()
            amt = ta.get("amount") or 0
            if owner:
                holders.setdefault(owner, 0)
                holders[owner] += amt
    except Exception:
        return None
    if not holders:
        return None
    # 2) Compare against previous snapshot
    prev = snap.get("holders") or {}
    prev_keys = set(prev.keys())
    cur_keys = set(holders.keys())
    if prev_keys:
        retained = len(prev_keys & cur_keys)
        retention = retained / len(prev_keys)
        churned = len(prev_keys - cur_keys)
        added = len(cur_keys - prev_keys)
    else:
        retention = None; churned = 0; added = len(cur_keys)
    # 3) Persist new snapshot (same depth as we read)
    _save_holder_snapshot({**_load_holder_snapshot(), mint: {
        "ts": time.time(),
        "holders": holders,
    }})
    return {
        "holders_now": len(cur_keys),
        "holders_prev": len(prev_keys),
        "retention": round(retention, 3) if retention is not None else None,
        "churned": churned,
        "holders_added": added,
        "snapshot_ts": snap.get("ts"),
    }

def fresh_wallet_power(mint, pool=None, max_wallets=10, sol_price=None):
    """New-wallet purchasing power from GeckoTerminal trades.

    GeckoTerminal /pools/{pool}/trades returns the last 300 swaps with
    tx_from_address (wallet), kind (buy/sell), from_token_amount, price_from_in_usd.
    We take the most recent BUY wallets, exclude wallets already in the holder
    snapshot (they are NOT new), and report:
      - new_buyer_count: distinct fresh wallets buying in the sampled trades
      - sum_buy_usd: total fresh-wallet USD buying in the sampled window
      - biggest_buy_usd: the single largest fresh-wallet purchase
      - newest_wallet_age_min: approximate age of the newest fresh buyer (from tx timestamps)
    If `pool` is None, we resolve it from DexScreener first. Returns None on failure.
    """
    cfg = load_config()
    if not pool:
        pair = dex_pair(mint)
        pool = (pair or {}).get("pairAddress")
    if not pool:
        return None
    try:
        _st, d = http_json(
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/trades",
            timeout=25, retries=3, headers={"User-Agent": UA})
    except Exception:
        return None
    data = (d or {}).get("data") or []
    if not data:
        return None
    # known holders = the snapshot we already have (or empty)
    snap = _load_holder_snapshot().get(mint) or {}
    known = set((snap.get("holders") or {}).keys())
    now = time.time()
    buys = []
    for t in data:
        a = t.get("attributes") or {}
        if a.get("kind") != "buy":
            continue
        w = (a.get("tx_from_address") or "").lower()
        if not w:
            continue
        try:
            v = float(a.get("price_from_in_usd") or 0) * float(a.get("from_token_amount") or 0)
        except Exception:
            v = 0
        ts = a.get("block_timestamp")
        t_epoch = None
        try:
            import datetime
            t_epoch = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
        buys.append({"wallet": w, "usd": v, "ts": t_epoch})
    # dedupe by wallet keeping most recent
    seen = {}
    for b in buys:
        if b["wallet"] not in seen or (b.get("ts") or 0) > (seen[b["wallet"]].get("ts") or 0):
            seen[b["wallet"]] = b
    fresh = [b for b in seen.values() if b["wallet"] not in known][:max_wallets]
    if not fresh:
        return {"new_buyer_count": 0, "sum_buy_usd": 0.0, "biggest_buy_usd": 0.0,
                "newest_wallet_age_min": None, "known_holders": len(known), "wallet_buys": []}
    total = sum(b["usd"] for b in fresh)
    biggest = max(b["usd"] for b in fresh)
    ts_now = time.time()
    newest_ts = max((b.get("ts") or 0) for b in fresh)
    newest_age = (ts_now - newest_ts) / 60 if newest_ts else None
    return {
        "new_buyer_count": len(fresh),
        "sum_buy_usd": round(total, 2),
        "biggest_buy_usd": round(biggest, 2),
        "newest_wallet_age_min": round(newest_age, 1) if newest_age is not None else None,
        "known_holders": len(known),
        "sample_wallets": [b["wallet"][:8] for b in fresh[:5]],
        "wallet_buys": [{"wallet": b["wallet"], "usd": round(b["usd"], 2)} for b in fresh[:15]],
    }

# ---------- Combined token intel for a position ----------

def token_intel(mint):
    pair = dex_pair(mint)
    out = {"mint": mint, "pair_ok": bool(pair)}
    if not pair: return out
    out["symbol"] = (pair.get("baseToken") or {}).get("symbol")
    out["mc"] = pair.get("marketCap"); out["liq"] = (pair.get("liquidity") or {}).get("usd")
    out["px"] = pair.get("priceUsd")
    out["created_ms"] = pair.get("pairCreatedAt")
    out["fill_min"] = estimate_fill_time(mint, pair.get("pairCreatedAt"))
    out["fill_class"] = fill_time_class(out["fill_min"])
    out["wash"] = wash_lpi_check(mint, pair)
    out["fragility"] = fragility_score(mint, pair)
    out["concentration"] = holder_concentration(mint)
    return out
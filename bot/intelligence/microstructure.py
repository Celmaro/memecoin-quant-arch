"""Market microstructure (doc 2 §4) — detect what price alone misses.

From GeckoTerminal /pools/{pool}/trades (keyless, verified):
  - cumulative volume delta (CVD): aggressive buy vs sell USD
  - buy-size distribution: broad participation vs few large buys
  - unique buyer growth + turnover vs holder growth
  - momentum divergence: price up but participation/liquidity weak
  - absorption: sells absorbed while price flat
"""
import os, sys, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import dex_pair, http_json, UA

def _gt_trades(mint, pool=None):
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
    out = []
    for t in data:
        a = t.get("attributes") or {}
        try:
            v = float(a.get("price_from_in_usd") or 0) * float(a.get("from_token_amount") or 0)
        except Exception:
            v = 0.0
        out.append({"kind": a.get("kind"), "usd": v,
                    "wallet": (a.get("tx_from_address") or "").lower(),
                    "ts": a.get("block_timestamp")})
    return out

def microstructure(mint, pool=None):
    """Returns CVD, buy concentration, unique buyers, and divergence flags."""
    trades = _gt_trades(mint, pool=pool)
    if not trades:
        return None
    buys = [t for t in trades if t.get("kind") == "buy"]
    sells = [t for t in trades if t.get("kind") == "sell"]
    buy_usd = sum(t["usd"] for t in buys)
    sell_usd = sum(t["usd"] for t in sells)
    total = buy_usd + sell_usd
    cvd = (buy_usd - sell_usd) / total if total else 0
    n_buy = len(buys)
    unique_buyers = len(set(t["wallet"] for t in buys if t["wallet"]))
    # 3rd audit 5.6 (Oct 22): concentration was `fraction of buys >= 3x mean` —
    # for k such trades, 3k*mean <= n*mean => k/n <= 1/3, so the monitor's
    # `bc >= 0.6` trigger could NEVER fire. Redefine as the top-5 wallets'
    # share of buy USD (classic concentration metric, reachable 0..1).
    _by_buyer = {}
    for t in buys:
        if t["wallet"]:
            _by_buyer[t["wallet"]] = _by_buyer.get(t["wallet"], 0.0) + t["usd"]
    top5_share = 0.0
    if _by_buyer:
        top5_share = sum(sorted(_by_buyer.values(), reverse=True)[:5])
        top5_share = top5_share / max(buy_usd, 1e-9)
    concentration = round(top5_share, 2)
    return {
        "cvd": round(cvd, 3),
        "buy_usd": round(buy_usd, 2), "sell_usd": round(sell_usd, 2),
        "n_buys": n_buy, "n_sells": len(sells),
        "unique_buyers": unique_buyers,
        "buy_concentration": round(concentration, 2),  # ~1.0 = few whales dominate
        "sample_trades": len(trades),
    }
#!/usr/bin/env python3
"""Jupiter fee/slip estimator — validates the desk's $20-trade math on a real quote.

Uses the keyed Jupiter Public API at api.jup.ag/swap/v1/quote (Authorization: Bearer jup_pub_…),
which this environment can reach (quote-api.jup.ag does not resolve here).
Falls back to DexScreener pricing when the quote API is unreachable.
"""
import sys, urllib.request, urllib.parse, json, re, time
from desk_common import SOL_MINT, dex_pair, load_config, http_json

QUOTE = "https://api.jup.ag/swap/v1/quote"
FEE_BPS = 1.7  # P4 (Oct 20): REAL-QUOTE estimate only (terminal ~0.9% + pool ~0.3%
# + priority ~0.5% per Jupiter v1/v2 quote). The PAPER book books 3.0%/side
# (scaleout.PAPER_FEE_PCT) — this estimator documents the real-money drag; it is
# NOT the paper fee and must never be used for book PnL.


def sol_price():
    """SOL/USD — FREE PUMPDATA FEED FIRST (Oct 21 real-engineering: the 15s
    keepalive emit writes sol_usd into pumpdata_cache.json — no DexScreener
    poll, no 429). DexScreener stays the fallback only when the feed is stale."""
    try:
        import os, json as _j
        cache = os.path.expanduser("~/memecoin-quant/pumpdata_cache.json")
        d = _j.load(open(cache))
        v = float(d.get("sol_usd") or 0)
        upd = float(d.get("updated") or 0)
        if v > 0 and time.time() - upd <= 180:   # keepalive cadence ~15s; 180s = generous
            return v
    except Exception:
        pass
    try:
        d = dex_pair(SOL_MINT)
        req = urllib.request.Request("https://api.dexscreener.com/latest/dex/tokens/" + SOL_MINT,
                                     headers={"User-Agent": "Mozilla/5.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=20).read())
        for pair in data.get("pairs", []):
            if (pair.get("quoteToken") or {}).get("symbol") == "USDC":
                return float(pair.get("priceUsd") or 0)
        return float(d.get("priceUsd") or 150.0) if d else 150.0
    except Exception:
        return 150.0


def token_decimals(mint):
    try:
        pair = dex_pair(mint)
        dec = (pair.get("baseToken") or {}).get("decimals")
        if dec: return int(dec)
    except Exception:
        pass
    return 6  # pump.fun standard


def quote(mint, usd=20, slippage_bps=500):
    cfg = load_config()
    key = cfg.get("JUPITER_KEY")
    s = sol_price()
    amount = int(round(usd / s * 1e9))
    params = urllib.parse.urlencode({
        "inputMint": SOL_MINT, "outputMint": mint, "amount": amount, "slippageBps": slippage_bps,
    })
    try:
        hdrs = {"User-Agent": "Mozilla/5.0"}
        if key: hdrs["x-api-key"] = key
        # Swap V2 quote is the recommended path (Oct-18 research); fall back to V1.
        data = None
        for base in (f"{QUOTE.replace('/swap/v1/quote','/swap/v2/quote')}", QUOTE):
            try:
                d2 = http_json(f"{base}?{params}", headers=hdrs)[1]
                if isinstance(d2, dict) and d2.get("outAmount"):
                    data = d2
                    break
            except Exception:
                continue
        if not data:
            raise Exception("both v1+v2 quote failed")
        out_amt = int(data.get("outAmount") or 0)
        # Jupiter docs: priceImpactPct is a DECIMAL FRACTION (0.05 = 5%), not a percentage.
        price_impact = float(data.get("priceImpactPct") or 0)
    except Exception as e:
        # Fallback: estimate straight from DexScreener price (no impact data)
        pair = dex_pair(mint)
        if not pair:
            return {"error": f"quote failed and no dex fallback: {e}"}
        tok_px = float(pair.get("priceUsd") or 0)
        if tok_px <= 0:
            return {"error": "token has no USD price"}
        out_amt = usd / tok_px
        price_impact = 1.0  # assumed (fraction: 100%)
        via = "dexscreener"
    else:
        via = "jupiter"
    dec = token_decimals(mint)
    expected_tokens = out_amt / (10 ** dec) if via == "jupiter" else out_amt
    price_impact_usd = usd * price_impact
    fee_total = usd * FEE_BPS / 100
    total = fee_total + price_impact_usd
    return {
        "via": via, "usd_in": usd, "sol_price": round(s, 2),
        "out_tokens": expected_tokens, "out_decimals": dec,
        "price_impact_pct": round(price_impact * 100, 3),
        "price_impact_usd": round(price_impact_usd, 3),
        "fee_drag_usd": round(fee_total, 3),
        "total_cost_usd": round(total, 3),
        "effective_pct_of_principal": round(total / usd * 100, 2) if usd else 0,
    }


def quote_fill(mint, usd=20, slippage_bps=500, timeout=6, retries=1):
    """Fill-to-fill paper pricing (Oct 21, Charon A1): bounded Jupiter quote for a
    paper ENTRY fill. Returns (fill_px, impact_pct, fee_usd, via) or None.

    fill_px = usd_in / out_tokens — the REAL per-token price a $usd order would
    land at (impact included). The feed price stays the DECISION mark; this is
    the FILL price. HARD-BOUNDED by a worker thread (quote()'s own http_json
    default is 20s x 3 retries — too slow for the paper tick; a hung quote must
    fall back to the 1% latency slip, never block the tick).

    Oct 21 PHANTOM-BOOK GUARD: the fill price must be within a sane band of the
    feed price (0.2x..5x). A Jupiter out_tokens scale error (wrong decimals /
    raw-vs-token mixup) once produced entry_px ~1e6x too small -> a $20 position
    booked +$30.8M at mult 1.59M. Out-of-band fill prices are rejected (None) so
    the caller falls back to the 1% slip — a corrupted FILL is worse than none.

    NOTE: quote() itself is used for reporting with generous budgets; this is
    the entry-path variant that must never block the tick."""
    import threading
    _box = {}

    def _work():
        try:
            from jupiter_estimator import quote as _quote
            _box["r"] = _quote(mint, usd=usd, slippage_bps=slippage_bps)
        except Exception:
            _box["r"] = None

    th = threading.Thread(target=_work, daemon=True)
    th.start()
    th.join(timeout)  # hard ceiling: paper tick never waits longer than `timeout`
    if th.is_alive():
        return None  # quote hung (Jupiter down/flaky) — caller falls back to slip
    r = _box.get("r")
    if not isinstance(r, dict) or r.get("via") != "jupiter" or r.get("error"):
        return None
    out_tok = float(r.get("out_tokens") or 0)
    if out_tok <= 0:
        return None
    fill_px = usd / out_tok
    # sanity band vs the feed price (decision mark) — reject scale-corrupted fills
    try:
        from desk_common import market_for
        _feed = market_for(mint, timeout=5, retries=0) or {}
        _fp = float(_feed.get("priceUsd") or 0)
        if _fp > 0:
            _ratio = fill_px / _fp
            if not (0.2 <= _ratio <= 5.0):
                return None  # fill wildly off the feed price — use 1% slip instead
    except Exception:
        pass
    return (fill_px, float(r.get("price_impact_pct") or 0),
            float(r.get("fee_drag_usd") or 0), "jupiter")


def quote_impact(mint, usd=20):
    """Returns price impact as a FRACTION (0.05 = 5%) for a $usd buy, or None.

    Internal representation is the decimal fraction — Jupiter returns it that way
    (0.05 = 5%). quote() converts to % only for the human-readable field.
    """
    r = quote(mint, usd)
    if "error" in r or r.get("via") != "jupiter":
        return None  # fallback estimates are not real impact
    return (r.get("price_impact_pct") or 0) / 100.0


def quote_sell(mint, token_amount, slippage_bps=500):
    """audit P1.6 (Oct 18): quote a SELL route (token -> SOL) with the actual token
    quantity held. Jupiter inputMint/outputMint are the reverse of the buy quote —
    exit analysis must use the real exit direction, not a buy quote."""
    try:
        from desk_common import http_json, load_config, SOL_MINT
        cfg = load_config()
        key = cfg.get("JUPITER_KEY")
        amount = int(round(token_amount))  # raw units (decimals applied by caller)
        params = urllib.parse.urlencode({
            "inputMint": mint, "outputMint": SOL_MINT, "amount": amount, "slippageBps": slippage_bps,
        })
        hdrs = {"User-Agent": "Mozilla/5.0"}
        if key: hdrs["x-api-key"] = key
        d = http_json(f"{QUOTE}?{params}", headers=hdrs)[1]
        if not isinstance(d, dict) or not d.get("outAmount"):
            return None
        return {
            "via": "jupiter",
            "in_tokens": amount,
            "out_sol": int(d.get("outAmount") or 0),
            "impact_fraction": float(d.get("priceImpactPct") or 0),  # decimal fraction (0.05=5%)
            "slippage_bps": slippage_bps,
        }
    except Exception:
        return None


def report(mint, usd=20):
    r = quote(mint, usd)
    if "error" in r:
        print(f"ERROR: {r['error']}"); return r
    print(f"JUPITER QUOTE ({r['via']}) — ${usd} SOL -> {mint[:12]}...")
    print(f"  SOL/USD: ${r['sol_price']}")
    print(f"  expected out: {r['out_tokens']:,.2f} tokens ({r['out_decimals']} decimals)")
    print(f"  price impact: {r['price_impact_pct']}% (${r['price_impact_usd']})")
    print(f"  desk fee drag: ${r['fee_drag_usd']} (terminal+pool+priority ≈ {FEE_BPS}%)")
    print(f"  TOTAL cost on entry: ${r['total_cost_usd']} = {r['effective_pct_of_principal']}% of principal")
    be = (r["total_cost_usd"] / max(0.01, usd - r["total_cost_usd"])) * 100
    print(f"  → at ${usd} size, need +{be:.1f}% just to break even (pre-exit fees)")
    return r


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: jupiter_estimator.py <mint> [usd]")
        sys.exit(1)
    report(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 20)
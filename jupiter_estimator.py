#!/usr/bin/env python3
"""Jupiter fee/slip estimator — validates the desk's $20-trade math on a real quote.

Uses the keyed Jupiter Public API at api.jup.ag/swap/v1/quote (Authorization: Bearer jup_pub_…),
which this environment can reach (quote-api.jup.ag does not resolve here).
Falls back to DexScreener pricing when the quote API is unreachable.
"""
import sys, urllib.request, urllib.parse, json, re
from desk_common import SOL_MINT, dex_pair, load_config, http_json

QUOTE = "https://api.jup.ag/swap/v1/quote"
FEE_BPS = 1.7  # desk estimate: terminal ~0.9% + pool ~0.3% + priority ~0.5%


def sol_price():
    """SOL/USD from DexScreener USDC pair (price.jup.ag doesn't resolve in this env)."""
    try:
        d = dex_pair(SOL_MINT)
        for p in [d] + ([] if not d else []):
            pass
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


def quote_impact(mint, usd=20):
    """Returns price impact as a FRACTION (0.05 = 5%) for a $usd buy, or None.

    Internal representation is the decimal fraction — Jupiter returns it that way
    (0.05 = 5%). quote() converts to % only for the human-readable field.
    """
    r = quote(mint, usd)
    if "error" in r or r.get("via") != "jupiter":
        return None  # fallback estimates are not real impact
    return (r.get("price_impact_pct") or 0) / 100.0


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
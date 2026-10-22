"""data_sources/ (doc 1) — adapters: gmgn, dexscreener, pumpfun, custom_onchain.

These are data-source integrations, NOT entry generators. Each adapter exposes a
small validated surface (capability, auth, rate-limit) for the intelligence layer.
"""
import os, sys
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import gmgn_rank, gmgn_lookup, gmgn_summary, dex_pair, http_json, load_config, UA


class GMGN:
    name = "gmgn"
    def rank(self, timeframe="1m", order="volume", limit=50):
        return gmgn_rank(timeframe=timeframe, order=order, limit=limit)
    def lookup(self, mint):
        return gmgn_lookup(mint)
    def summary(self, row):
        return gmgn_summary(row)


class DexScreener:
    name = "dexscreener"
    def token(self, mint):
        return dex_pair(mint)
    def price_mc_liq(self, mint):
        p = dex_pair(mint)
        if not p: return None
        return {"price": p.get("priceUsd"), "mc": p.get("marketCap"),
                "liq": (p.get("liquidity") or {}).get("usd"),
                "vol_h24": (p.get("volume") or {}).get("h24")}
    def trades(self, mint):
        """DexScreener doesn't give trade-level data; use GeckoTerminal for that."""
        return None


class PumpFun:
    name = "pumpfun"
    def bonding_state(self, mint):
        """Pump.fun launchpad status from GMGN rank fields (exchange == pump)."""
        row = gmgn_lookup(mint)
        if not row: return None
        return {"launchpad": row.get("launchpad"), "exchange": row.get("exchange"),
                "creator_close": row.get("creator_close"),
                "migrated_pool": row.get("migrated_pool_exchange")}


class CustomOnchain:
    """Helius + RugCheck + Shyft RPC wrappers for forensic needs."""
    name = "custom_onchain"
    def authorities(self, mint):
        try:
            from aegis import check_authorities
            return check_authorities(mint)
        except Exception:
            return {}
    def rugcheck(self, mint, full=True):
        try:
            from desk_common import rugcheck
            return rugcheck(mint, full=full)
        except Exception:
            return {}


def adapter(name):
    return {"gmgn": GMGN(), "dexscreener": DexScreener(), "pumpfun": PumpFun(),
            "custom_onchain": CustomOnchain()}.get(name)
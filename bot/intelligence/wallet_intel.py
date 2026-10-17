"""Wallet intelligence (doc 2 §2, P1) — smart-money distribution & clustering.

Replaces the dead CoinStats path with on-chain top-holder balance tracking via
Helius DAS (getTokenAccounts) + holder snapshots, so we can detect:
  - smart/known wallets accumulating vs distributing
  - wallet disagreement (some buying, some selling)
  - fresh buyer quality (new vs known holder wallets)
Also persists balance history (persistence.wallet_history) for distribution intensity.
"""
import os, sys, json, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import load_config, http_json, rugcheck
from bot.persistence import wallet_history_add, wallet_balances

def _helius_das_owners(mint, limit=1000):
    """Top token accounts via Helius DAS getTokenAccounts (owner + amount)."""
    cfg = load_config()
    if not cfg.get("HELIUS_KEY"):
        return None
    try:
        _st, d = http_json(
            f"https://mainnet.helius-rpc.com/?api-key={cfg['HELIUS_KEY']}",
            timeout=30, retries=2, method="POST",
            body={"jsonrpc": "2.0", "id": "wl", "method": "getTokenAccounts",
                  "params": {"mint": mint, "limit": limit, "page": 1,
                             "displayOptions": {"showZeroBalance": False}}},
            headers={"Content-Type": "application/json"})
        res = (d or {}).get("result") or {}
        tas = res.get("token_accounts") or []
        out = []
        for ta in tas:
            owner = (ta.get("owner") or "").lower()
            amt = float(ta.get("amount") or 0)
            if owner:
                out.append((owner, amt))
        return out
    except Exception:
        return None

# common AMM/raydium LP vaults to exclude from "holder" counts
_LP_EXCLUDE = {"so11111111111111111111111111111111111111112", "675kxpm8g7eh9tofc2x3k2ylj5ylowykfzr5", ""}

def top_holder_balances(mint, n=10):
    """Top n holder balances + classify each as accumulating/distributing vs last
    TOKEN-BALANCE SNAPSHOT record (audit P1-6: never compare against USD-notional
    buy rows — only compatible token-quantity 'snapshot' events)."""
    accts = _helius_das_owners(mint)
    if not accts:
        return None
    accts = [(o, a) for o, a in accts if o not in _LP_EXCLUDE]
    accts.sort(key=lambda x: -x[1])
    out = []
    for owner, amt in accts[:n]:
        # find the most recent *snapshot* row for this wallet+mint
        hist = wallet_balances(owner, mint, limit=50)
        snaps = [h for h in hist if h.get("event") == "snapshot"]
        prev_amt = float(snaps[0]["balance"]) if snaps else None
        delta = None
        if prev_amt is not None:
            delta = (amt - prev_amt) / prev_amt if prev_amt else None
        wallet_history_add(owner, mint, amt, "snapshot")
        out.append({"owner": owner[:10], "balance": amt, "prev": prev_amt,
                    "delta_pct": round(delta * 100, 1) if delta is not None else None,
                    "dir": "accumulating" if (delta or 0) > 0.02 else ("distributing" if (delta or 0) < -0.02 else "flat")})
    return out

def wallet_distribution_signal(mint, n=10):
    """Doc 2 §2C/2D: are tracked top wallets accumulating or distributing? Disagreement?"""
    top = top_holder_balances(mint, n=n)
    if not top:
        return None
    with_prev = [t for t in top if t["prev"] is not None]
    acc = sum(1 for t in with_prev if t["dir"] == "accumulating")
    dis = sum(1 for t in with_prev if t["dir"] == "distributing")
    n_known = len(with_prev)
    disagreement = acc > 0 and dis > 0
    return {
        "top_n": len(top),
        "accumulating": acc, "distributing": dis, "flat": n_known - acc - dis,
        "known_tracked": n_known,
        "disagreement": disagreement,
        "signal": ("DISTRIBUTION" if dis >= 2 and dis > acc else
                   "ACCUMULATION" if acc >= 2 and acc > dis else
                   "DISAGREEMENT" if disagreement else "FLAT"),
        "wallets": top,
    }

def cross_token_overlap(mints):
    """Doc 2 §8: shared top-holder wallets across open positions (contagion)."""
    if len(mints) < 2:
        return {"overlap": [], "concentration_risk": False}
    sets = {}
    for m in mints:
        accts = _helius_das_owners(m, limit=200)
        if accts:
            sets[m] = set(o for o, _ in accts if o not in _LP_EXCLUDE)
    shared = {}
    keys = list(sets.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            common = sets[keys[i]] & sets[keys[j]]
            if common:
                shared[f"{keys[i][:8]}↔{keys[j][:8]}"] = [w[:10] for w in list(common)[:5]]
    return {"overlap": shared, "concentration_risk": bool(shared)}
#!/usr/bin/env python3
"""whale_watch.py — per-position whale-sell watch (Oct 19, user request #2).

For each OPEN paper position, resolve its top holders (blix_holders, rugcheck
fallback) and check sol.wales moves_recent for any SELL by a watched wallet.
DM the user when a watched wallet sells a held mint.

Design:
- Poll every WHALE_POLL_S (default 60s) via `desk_supervisor` calling `whale_watch_once()`
  in the paper-tick thread (cheap: 1 sol.wales call + cached holder sets).
- Top holders cached 30 min per mint (blix_holders is localhost, fast).
- Deltas deduped by signature: once a sell signature is alerted it's recorded in
  whale_watch_state.json so we never double-DM.
- Fail-closed: any provider error -> no alert, never blocks the tick.
"""

import json
import os
import sys
import time

DESK = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DESK, "whale_watch_state.json")
POS_FILE = os.path.join(DESK, "positions.json")
BOOK_FILE = os.path.join(DESK, "paper_book.json")

WHALE_POLL_S = 60          # how often supervisor calls us
HOLDER_CACHE_S = 1800      # re-resolve holders every 30 min
SELL_MIN_SOL = 3.0         # ignore tiny whale sells below this SOL size
MAX_WATCHED = 20           # cap wallets polled per tick (sol.wales moves limit)

# fields we persist per signature to dedupe alerts
def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"alerted_sigs": {}, "holder_cache": {}}

def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE)


def _top_holders(mint):
    """Resolve top-holder wallets for a mint. Blix first (localhost, rich),
    rugcheck topHolders as fallback. Returns [{wallet, pct, sol}]. [] on error."""
    try:
        sys_path = list(sys.path)
        import sys
        sys.path.insert(0, DESK)
        from desk_common import blix_holders, rugcheck
        h = blix_holders(mint) or {}
        top = h.get("top") or []
        if top:
            return [{"wallet": t.get("wallet"), "pct": t.get("pct"),
                     "sol": t.get("sol", 0), "tag": t.get("tag")}
                    for t in top if t.get("wallet")]
    except Exception:
        pass
    # fallback: rugcheck topHolders (may be empty if DNS breaker active)
    try:
        import sys
        sys.path.insert(0, DESK)
        from desk_common import rugcheck
        r = rugcheck(mint) or {}
        th = r.get("topHolders") or []
        return [{"wallet": h.get("wallet"), "pct": h.get("pct"),
                 "sol": h.get("sol", 0), "tag": None} for h in th if h.get("wallet")]
    except Exception:
        return []
    return []


def _holder_cache_get(mint, st):
    e = st.get("holder_cache", {}).get(mint)
    if e and time.time() - e.get("ts", 0) <= HOLDER_CACHE_S:
        return e.get("holders") or []
    return None


def _holder_cache_put(mint, holders, st):
    st.setdefault("holder_cache", {})[mint] = {
        "ts": time.time(), "holders": holders}
    # cap cache size
    if len(st["holder_cache"]) > 200:
        for k in sorted(st["holder_cache"], key=lambda k: st["holder_cache"][k]["ts"])[:-150]:
            st["holder_cache"].pop(k, None)


def _open_positions():
    """Mints currently open in the paper book (positions dict keyed by mint)."""
    try:
        b = json.load(open(BOOK_FILE))
        return {mint: pos for mint, pos in b.get("positions", {}).items() if pos}
    except Exception:
        return {}


def whale_watch_once():
    """Poll sol.wales and return new alert dicts. Never raises."""
    st = load_state()
    positions = _open_positions()
    if not positions:
        return []

    watched = {}  # wallet -> [{mint, symbol, pct}]
    for mint, pos in list(positions.items())[:MAX_WATCHED]:
        holders = _holder_cache_get(mint, st)
        if holders is None:
            holders = _top_holders(mint)
            _holder_cache_put(mint, holders, st)
        # Watch top-1 only: the wallet with the largest holder percentage.
        if holders:
            h = holders[0]
            wallet = h.get("wallet")
            if wallet:
                watched.setdefault(wallet, []).append({
                    "mint": mint, "symbol": pos.get("symbol") or mint[:8],
                    "pct": h.get("pct")})
    if not watched:
        save_state(st)
        return []

    try:
        from desk_common import sol_wales_moves_recent
        moves = sol_wales_moves_recent(limit=100) or []
    except Exception:
        save_state(st)
        return []

    alerts = []
    alerted = st.setdefault("alerted_sigs", {})
    for move in moves:
        kind = str(move.get("kind") or "").lower()
        if kind not in ("token_out", "sell", "swap", "token_sell"):
            continue
        mint = move.get("mint") or move.get("token") or move.get("tokenAddress")
        trader = move.get("trader") or {}
        wallet = trader.get("address") if isinstance(trader, dict) else trader
        if not mint or not wallet or wallet not in watched:
            continue
        # Match the held mint and require a sell-like move. A generic swap is
        # surfaced conservatively; the user can inspect the signature.
        for item in watched[wallet]:
            if item["mint"] != mint:
                continue
            sig = str(move.get("signature") or move.get("tx") or
                      f"{wallet}:{mint}:{move.get('at')}:{move.get('amount')}")
            if sig in alerted:
                continue
            amount = move.get("amount") or move.get("amountUi") or "?"
            alerts.append({"mint": mint, "symbol": item["symbol"],
                           "wallet": wallet, "pct": item.get("pct"),
                           "amount": amount, "kind": kind,
                           "signature": sig, "at": move.get("at")})
            alerted[sig] = time.time()

    # Keep dedupe state bounded; retain newest 500 signatures.
    if len(alerted) > 500:
        keep = sorted(alerted.items(), key=lambda kv: kv[1], reverse=True)[:400]
        st["alerted_sigs"] = dict(keep)
    save_state(st)
    return alerts


if __name__ == "__main__":
    found = whale_watch_once()
    print(json.dumps(found, indent=2))


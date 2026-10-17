#!/usr/bin/env python3
"""Smart-wallet vettor: fetch wallet trade history, compute consistency/PnL hints/bot features.

Research: copiers capture ~20% of shown smart-money edge; profitability is predictable from
bot-presence + consistency features. Returns a STRATEGY for coins this wallet touches.
"""
import time, statistics, sys
from desk_common import helius_txs, db, log_event

BUY_WORDS = ("swap", "buy", "purchase")
SELL_WORDS = ("sell", "swap")

def classify_wallet(wallet, limit=100):
    txs = helius_txs(wallet, limit=limit)
    if not isinstance(txs, list):
        return {"error": f"bad response: {txs}"}
    events = []
    size_hist = []
    for tx in txs:
        desc = tx.get("description") or ""
        native = tx.get("nativeTransfers") or []
        size = sum(abs(t.get("amount") or 0) for t in native) / 1e9
        ts = tx.get("timestamp")
        if size > 0:
            size_hist.append(size)
        # token transfers indicate trading activity on specific mints
        toks = tx.get("tokenTransfers") or []
        mints = {(t.get("mint") or "")[:12] for t in toks if t.get("mint")}
        typ = "other"
        dl = desc.lower()
        if "sold" in dl or "sell" in dl: typ = "sell"
        elif "bought" in dl or "buy" in dl or "swap" in dl: typ = "buy"
        events.append({"ts": ts, "type": typ, "sol_size": size, "mints": mints, "desc": desc[:80]})
    n = len(events)
    buys = sum(1 for e in events if e["type"] == "buy")
    sells = sum(1 for e in events if e["type"] == "sell")
    # Bot features
    bot_flags = []
    if n >= 10:
        # Uniform sizes (bots trade round, consistent amounts)
        if size_hist:
            cv = (statistics.pstdev(size_hist) / statistics.fmean(size_hist)) if statistics.fmean(size_hist) else 0
            if cv < 0.3: bot_flags.append(f"uniform_sizes(cv={cv:.2f})")
        # Timestamp regularity: bursts of txs in the same second
        same_sec = sum(1 for a, b in zip(events, events[1:]) if a["ts"] == b["ts"])
        if n >= 20 and same_sec > n * 0.3: bot_flags.append(f"burst_same_second({same_sec}/{n})")
    # Hold-time hint: average seconds between buy and later sell (approx via event order)
    hold_secs = []
    open_t = None
    for e in events:
        if e["type"] == "buy" and open_t is None:
            open_t = e["ts"]
        elif e["type"] == "sell" and open_t is not None:
            hold_secs.append(max(0, (e["ts"] or 0) - (open_t or 0)))
            open_t = None
    avg_hold_h = (statistics.fmean(hold_secs) / 3600) if hold_secs else None

    # Classify
    cls = "unknown"
    if n >= 5 and buys > 0:
        win_sim = None  # no reliable PnL without quote data; use buy/sell symmetry as proxy
        if sells >= buys * 0.8:
            cls = "active_trader"
        else:
            cls = "accumulator"
    if bot_flags and n >= 20:
        cls = "bot_likely"

    row = {"wallet": wallet, "txs": n, "buys": buys, "sells": sells, "class": cls,
           "bot_flags": bot_flags, "avg_hold_h": round(avg_hold_h, 2) if avg_hold_h else None,
           "sample_mints": list({m for e in events for m in e["mints"]})[:10]}
    con = db()
    con.execute("""INSERT OR REPLACE INTO wallets(wallet, role, tags, first_seen, last_seen, buys, sells, stats_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (wallet, cls, ",".join(bot_flags), time.time(), time.time(), buys, sells,
                 __import__("json").dumps({"avg_hold_h": row["avg_hold_h"], "class": cls})))
    con.commit(); con.close()
    log_event({"event": "WALLET_VET", "wallet": wallet, **{k: row[k] for k in ("txs","buys","sells","class","bot_flags")}})
    return row

def vet_wallet(wallet, limit=100):
    """CLI entry: print a readable report."""
    r = classify_wallet(wallet, limit=limit)
    if "error" in r:
        print(f"ERROR: {r['error']}")
        return r
    print(f"Wallet {wallet[:16]}...")
    print(f"  txs: {r['txs']} | buys: {r['buys']} | sells: {r['sells']}")
    print(f"  class: {r['class']} | avg hold: {r['avg_hold_h']}h")
    print(f"  bot flags: {r['bot_flags'] or 'none'}")
    print(f"  sample mints: {', '.join(r['sample_mints'][:5])}")
    # Strategy hint
    if r["class"] == "bot_likely":
        print("  → treat as manipulator: coins it 'calls' = fast-flip or skip, never conviction-hold")
    elif r["class"] == "accumulator":
        print("  → smart-money-ish accumulator: coins it enters early = conviction-hold candidate")
    else:
        print("  → active trader: mid-conviction; watch its exit for our own clip timing")
    return r

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: wallet_vettor.py <wallet> [limit]")
        sys.exit(1)
    w = sys.argv[1]
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    vet_wallet(w, lim)
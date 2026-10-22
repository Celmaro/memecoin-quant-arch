#!/usr/bin/env python3
"""flow_filter_audit.py — re-test Charon's FLOW filter on OUR tape (Oct 18 21:00 → Oct 19 09:00 UTC).

Charon FLOW (backtested 1,415 trades, +3.45 SOL delta): s1h_priceChange >= 0
AND net_buyer_ratio_5m >= 0.2 → keep; else reject. It survived where our
pre-fill push60/bs5m edge didn't (P5 no-look-ahead withdrawal).

Here we compute the SAME two features from the cached PumpAPI replay at CALL
time, using ONLY pre-call tape (no look-ahead):
  chg1h   = last price at call / price 60min before call - 1   (1h price change)
  nbr5m   = buys_5m / (buys_5m + sells_5m)                     (net buyer proxy; true
            Jupiter numNetBuyers needs wallet identity we don't replay — buys
            vs sells count is our closest proxy, flag it in the output)

Outcomes are the tape ladder outcomes (TP2/STOP) from tape_coins_rows.json.
Consistency rule (Charon): feature subset must beat baseline on BOTH pnl-mean
and win rate in FULL + the Oct 18/Oct 19 halves, >= 30 trades per half.
"""
import json, os, sys, time, collections, glob

DESK = os.path.expanduser("~/memecoin-quant")
CACHE = os.path.join(DESK, "_replay_cache")
ROWS = json.load(open(os.path.join(DESK, "tape_coins_rows.json")))

# outcome pnl proxy — same convention as deep_audit P10 (TP2 +19.4%, STOP -14.55%)
def outcome_pnl(outcome):
    if outcome == "TP2": return 19.4
    if outcome == "STOP": return -14.55
    return 0.0

def load_tape():
    """mint -> sorted [(ts_s, action, price, token_sol)] from cached 12h replay."""
    by_mint = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(CACHE, "*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                mint = e.get("mint"); act = e.get("action"); px = e.get("price")
                ts = e.get("timestamp")
                if not mint or act not in ("buy", "sell") or not px or not ts:
                    continue
                ts_s = float(ts) / 1000.0 if float(ts) > 1e12 else float(ts)  # archive is ms
                by_mint[mint].append((ts_s, act, float(px), float(e.get("tokensInPool") or 0)))
    for m in by_mint:
        by_mint[m].sort(key=lambda r: r[0])
    return by_mint

def features(events, call_ts):
    """Pre-call only: 1h price change + 5m buy/sell counts. No look-ahead."""
    # last event at-or-before call
    pre = [e for e in events if e[0] <= call_ts]
    if not pre:
        return None
    last_px = pre[-1][2]
    # price ~60min before
    cutoff_1h = call_ts - 3600
    earlier = [e for e in pre if e[0] <= cutoff_1h]
    if not earlier:
        return None
    old_px = earlier[-1][2]
    chg1h = (last_px / old_px - 1.0) * 100 if old_px > 0 else None
    # 5m window
    w5 = [e for e in pre if e[0] >= call_ts - 300]
    if not w5:
        return None
    b5 = sum(1 for e in w5 if e[1] == "buy")
    s5 = sum(1 for e in w5 if e[1] == "sell")
    nbr5 = b5 / (b5 + s5) if (b5 + s5) else None
    return {"chg1h": chg1h, "nbr5": nbr5, "b5": b5, "s5": s5, "last_px": last_px}

def main():
    tape = load_tape()
    rows = []
    for r in ROWS:
        mint = r.get("mint")
        call_ts = float(r.get("call_ts") or 0)
        if not mint or not call_ts or mint not in tape:
            continue
        f = features(tape[mint], call_ts)
        if not f or f["chg1h"] is None or f["nbr5"] is None:
            continue
        rows.append({**r, **f})
    print(f"audited calls with pre-call tape: {len(rows)} / {len(ROWS)}")

    base_pnl = sum(outcome_pnl(r["outcome"]) for r in rows) / len(rows)
    base_wr = sum(1 for r in rows if r["outcome"] == "TP2") / len(rows)
    print(f"BASELINE: n={len(rows)}  win%={base_wr:.1%}  mean pnl={base_pnl:+.2f}%")

    def split(rows):
        mid = sorted(r["call_ts"] for r in rows)[len(rows)//2]
        return ([r for r in rows if r["call_ts"] <= mid],
                [r for r in rows if r["call_ts"] > mid])

    def stats(rows):
        if not rows: return (None, None, 0)
        pnl = sum(outcome_pnl(r["outcome"]) for r in rows) / len(rows)
        wr = sum(1 for r in rows if r["outcome"] == "TP2") / len(rows)
        return (pnl, wr, len(rows))

    def evaluate(name, pred):
        sub = [r for r in rows if pred(r)]
        if len(sub) < 30: 
            print(f"  {name}: n={len(sub)} (<30, skip)")
            return
        p, w, n = stats(sub)
        h1, h2 = split(sub)
        p1, w1, n1 = stats(h1)
        p2, w2, n2 = stats(h2)
        ok = (p is not None and p > base_pnl and w > base_wr and
              p1 is not None and p1 > base_pnl and w1 > base_wr and
              p2 is not None and p2 > base_pnl and w2 > base_wr and
              n1 >= 30 and n2 >= 30)
        print(f"  {name}: n={n} win%={w:.1%} pnl={p:+.2f}% | H1({n1}) wr={w1:.1%} pnl={p1:+.2f}% | H2({n2}) wr={w2:.1%} pnl={p2:+.2f}%  {'PASS' if ok else 'FAIL'}")

    print("FLOW candidates:")
    evaluate("chg1h >= 0", lambda r: r["chg1h"] >= 0)
    evaluate("chg1h >= +5%", lambda r: r["chg1h"] >= 5)
    evaluate("nbr5 >= 0.5", lambda r: r["nbr5"] >= 0.5)
    evaluate("nbr5 >= 0.6", lambda r: r["nbr5"] >= 0.6)
    evaluate("FLOW (chg1h>=0 & nbr5>=0.5)", lambda r: r["chg1h"] >= 0 and r["nbr5"] >= 0.5)
    evaluate("FLOW (chg1h>=0 & nbr5>=0.2)", lambda r: r["chg1h"] >= 0 and r["nbr5"] >= 0.2)
    evaluate("FLOW strict (chg1h>=5 & nbr5>=0.6)", lambda r: r["chg1h"] >= 5 and r["nbr5"] >= 0.6)
    # our old edge for comparison (no-look-ahead bs5m)
    evaluate("bs5m >= 1.3 (old push proxy)", lambda r: float(r.get("bs5m") or 0) >= 1.3)

if __name__ == "__main__":
    main()

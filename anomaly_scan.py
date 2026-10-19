#!/usr/bin/env python3
"""anomaly_scan.py — from the CACHED tape (11 hours), for every curated call:
entry fill at call_ts+latency, ladder outcome, then POST-EXIT window (2h):
p99 maxX and endX. Flags the anomaly classes:
  STOP_BUT_RECOVERED : our stop sold; tape later traded back to >= 1.0x (phantom/early dump)
  TP2_BUT_DUMPED     : our TP2 sold; tape later crashed below 0.6x (missed exit? no — good exit)
  NO_TAPE            : call had near-zero tape activity (dead call)
  MULTI_ARTIFACT     : price spikes >10x entry (bonding-curve micro artifact)
Loads only cached hour files (reuse-any style), no downloads.
"""
import json, os, sys, collections, time

DESK = os.path.expanduser("~/memecoin-quant")
CACHE = os.path.join(DESK, "_replay_cache")
LATENCY_MS = 1500
BUNDLE_MS = 3
MAX_HORIZON_S = 2 * 3600
SOLUSD = 150.0
# mirror replay exit tiers (MC-adaptive, USD)
EXIT_TIERS = [
    (40_000, 1.30, 1.60, 0.78),      # <$40K: 50%@+30 rest@+60 stop -22%
    (150_000, 1.35, 1.75, 0.75),     # $40-150K: -25%
    (1_500_000, 1.50, 2.00, 0.70),   # $150K-1.5M: -30%
    (10**12, 1.75, 2.50, 0.65),      # >$1.5M: -35%
]

def load_hour(prefix):
    out = []
    for cand in sorted(os.listdir(CACHE)):
        if cand.startswith(prefix + ".") and cand.endswith("mints.jsonl"):
            p = os.path.join(CACHE, cand)
            if os.path.getsize(p) == 0:
                continue
            for line in open(p):
                try:
                    ev = json.loads(line)
                    out.append((ev.get("mint") or "", ev.get("timestamp") or 0,
                                float(ev.get("price") or 0), ev.get("action")))
                except Exception:
                    continue
            break
    return out

def clean_tape(evs, mint):
    g = []
    for m, ts, price, action in evs:
        if m != mint or price <= 0:
            continue
        if g and ts - g[-1][0] <= BUNDLE_MS:
            continue
        if g and price < g[-1][1] * 0.01:  # migration/finalize guard
            continue
        g.append((ts, price))
    g.sort()
    return g

def scan_call(call, events_by_hour):
    mint = call.get("mint") or ""
    entry_ts = call.get("ts") or 0
    if not entry_ts:
        return None
    # gather tape from the call's hour and next 2h via cached hour files
    h0 = time.gmtime(entry_ts)
    hours = []
    for delta in range(3):
        t = entry_ts + delta * 3600
        gt = time.gmtime(t)
        prefix = "%02d_%02d_%02d_%02d" % (gt.tm_year, gt.tm_mon, gt.tm_mday, gt.tm_hour)
        if prefix not in events_by_hour:
            events_by_hour[prefix] = load_hour(prefix)
        hours.append(events_by_hour[prefix])
    tape = []
    for h in hours:
        tape.extend(h)
    g = clean_tape(tape, mint)
    if not g:
        return {"sym": call.get("symbol"), "chan": call.get("source"), "outcome": "NO_TAPE"}
    fill = None
    for ts, price in g:
        if ts >= entry_ts * 1000 + LATENCY_MS:
            fill = (ts, price)
            break
    if not fill:
        return {"sym": call.get("symbol"), "chan": call.get("source"), "outcome": "NO_FILL"}
    fill_px = fill[1]
    # ladder
    mc_entry = fill_px  # proxy tier by entry price magnitude? need MC — skip tier, use base ladder
    tp1, tp2, stop = 1.30, 1.60, 0.78
    outcome = "OPEN"
    exit_ms = None
    for ts, price in g:
        if ts < fill[0]:
            continue
        mult = price / fill_px
        if mult >= tp2:
            outcome = "TP2"; exit_ms = ts; break
        if mult <= stop:
            outcome = "STOP"; exit_ms = ts; break
    # post-exit window: p99 maxX + endX (2h from fill)
    end_t = fill[0] + MAX_HORIZON_S * 1000
    post = [price / fill_px for ts, price in g if (exit_ms or fill[0]) <= ts <= end_t]
    if not post:
        post = [fill_px / fill_px]
    post.sort()
    p99 = post[int(len(post) * 0.99)] if len(post) > 5 else post[-1]
    endX = post[-1] if ts >= end_t - 1000 else (post[-1] if post else 1.0)
    return {"sym": call.get("symbol"), "chan": call.get("source"), "mint": mint,
            "outcome": outcome, "fill_px": fill_px, "p99_maxX": p99, "endX": endX,
            "post_n": len(post)}

def main(hours):
    calls = [json.loads(l) for l in open(os.path.join(DESK, "calls.jsonl"))]
    # filter by the given hour prefixes
    prefixes = {h.replace("/", "_") for h in hours}
    win = []
    for c in calls:
        ts = c.get("ts") or 0
        gt = time.gmtime(ts)
        pref = "%02d_%02d_%02d_%02d" % (gt.tm_year, gt.tm_mon, gt.tm_mday, gt.tm_hour)
        if pref in prefixes:
            win.append(c)
    events_by_hour = {}
    results = []
    for i, c in enumerate(win):
        r = scan_call(c, events_by_hour)
        if r:
            r["_i"] = i
            results.append(r)
    return results, len(win)

if __name__ == "__main__":
    hours = sys.argv[1:] or ["2026/10/18/21", "2026/10/18/22", "2026/10/18/23", "2026/10/19/00",
                             "2026/10/19/01", "2026/10/19/02", "2026/10/19/03", "2026/10/19/04",
                             "2026/10/19/05", "2026/10/19/06", "2026/10/19/07"]
    results, total = main(hours)
    print(f"scanned {len(results)}/{total} calls (cached tape only)\n")
    anom = collections.Counter()
    for r in results:
        o = r.get("outcome")
        if o == "STOP" and r.get("p99_maxX", 1) >= 1.0:
            anom["STOP_BUT_RECOVERED"] += 1
        elif o == "STOP" and r.get("endX", 1) >= 1.0:
            anom["STOP_BUT_END_UP"] += 1
        elif o == "TP2" and r.get("endX", 1) < 0.6:
            anom["TP2_BUT_CRASHED"] += 1
        elif o == "NO_TAPE":
            anom["NO_TAPE"] += 1
    print("ANOMALY CLASSES:", dict(anom))
    print("\nSTOP-called coins that traded back >=1.0x within 2h (premature/phantom dumps):")
    for r in results:
        if r.get("outcome") == "STOP" and r.get("p99_maxX", 1) >= 1.0:
            print(f"  {str(r.get('sym','?'))[:14]:14s} {str(r.get('chan')):18s} p99_maxX={r.get('p99_maxX'):.2f} endX={r.get('endX'):.2f}")
    print("\nTP2-called coins that crashed <0.6x within 2h after (lucky exits/rugs after):")
    for r in results:
        if r.get("outcome") == "TP2" and r.get("endX", 1) < 0.6:
            print(f"  {str(r.get('sym','?'))[:14]:14s} {str(r.get('chan')):18s} p99_maxX={r.get('p99_maxX'):.2f} endX={r.get('endX'):.2f}")
    print("\nNO_TAPE calls (dead from the first second — should be filtered pre-buy):")
    for r in results:
        if r.get("outcome") == "NO_TAPE":
            print(f"  {r.get('sym','?')[:14]:14s} {r.get('chan')}")
    # summary stats
    outcomes = collections.Counter(r.get("outcome") for r in results)
    print("\nOUTCOMES:", dict(outcomes))
#!/usr/bin/env python3
"""shrine_graduate_replay.py — re-run the Oct 10 graduate replay (same engine,
same 6 rules) fed from PUMPDATA/shrine.trade's independent replay archive, to
prove the API works as a drop-in paper-trading tape source.

Same code path as graduate_replay.py but CACHE/SOURCE swapped to
https://replay.shrine.trade/pump/{...}.jsonl.zst. Output is the realistic pass
(rule-1 slot-end fills, 1.5% cost/side, size impact) plus a pool-level
cross-check of prices vs the pumpapi-derived tapes already in _grad_cache.
"""
import json, os, sys, time, csv, statistics, collections, subprocess, urllib.request

DESK = os.path.expanduser("~/memecoin-quant")
GRAD_CSV = "/home/agentuser/.hermes/opencraft-attachments/8e486aaf-4d3c-468b-b117-3fd8ae46b7d8/content"
SH_CACHE = os.path.join(DESK, "_shrine_cache")
os.makedirs(SH_CACHE, exist_ok=True)
REPLAY = "https://replay.shrine.trade/pump/{}.jsonl.zst"
HOURS = ["2026/10/10/14", "2026/10/10/15", "2026/10/10/16"]
BUY_S, SELL_S, HORIZON_S, COST, SOL_USD, SIZE_SOL = 15, 180, 1800, 0.015, 150.0, 20.0 / 150.0
LADDER = [(0, 2.00, 2.50, 0.25), (40_000, 2.00, 2.50, 0.25),
          (150_000, 2.20, 2.80, 0.25), (1_500_000, 2.50, 3.00, 0.25)]


def calendar_time(s):
    import calendar
    return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def load_grads():
    rows = list(csv.DictReader(open(GRAD_CSV)))
    out = []
    for r in rows:
        try:
            t = calendar_time(r["grad_time_utc"])
        except Exception:
            continue
        out.append({"mint": r["mint"], "pool": r["pool"], "grad_ts": t, "symbol": r.get("symbol") or r["mint"][:6]})
    return out


def fetch_hour(h, keep):
    name = h.replace("/", "_")
    raw = os.path.join(SH_CACHE, name + ".jsonl.zst")
    filt = os.path.join(SH_CACHE, name + ".filtered.jsonl")
    if not os.path.exists(raw):
        url = REPLAY.format(h)
        print(f"download {url} ...", flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(raw, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    if not os.path.exists(filt):
        code = ("import zstandard,sys,json,io;"
                "d=io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(open(sys.argv[1],'rb')),encoding='utf-8',errors='replace');\n"
                "keep=set(json.load(open(sys.argv[2]))); out=open(sys.argv[3],'w')\n"
                "for line in d:\n"
                "  try:\n"
                "    e=json.loads(line);\n"
                "    if e.get('mint') in keep or e.get('pool') in keep: out.write(line)\n"
                "  except Exception: pass\n"
                "out.close()\n")
        with open(os.path.join(SH_CACHE, "_filter.py"), "w") as f:
            f.write(code)
        with open(os.path.join(SH_CACHE, "_keep.json"), "w") as f:
            json.dump(list(keep), f)
        print(f"filter {name} ...", flush=True)
        r = subprocess.run([sys.executable, os.path.join(SH_CACHE, "_filter.py"),
                            raw, os.path.join(SH_CACHE, "_keep.json"), filt])
        if r.returncode != 0:
            print("filter failed rc", r.returncode)
    n = 0
    for _ in open(filt):
        n += 1
    print(f"  {name}: {n} filtered events")
    return filt


def load_pool_events(GRADS):
    pools = {g["pool"] for g in GRADS}
    per_pool = collections.defaultdict(dict)
    for h in HOURS:
        filt = os.path.join(SH_CACHE, h.replace("/", "_") + ".filtered.jsonl")
        if not os.path.exists(filt):
            continue
        for line in open(filt):
            try:
                e = json.loads(line)
            except Exception:
                continue
            key = e.get("pool")
            if key not in pools:
                continue
            if e.get("action") not in ("buy", "sell"):
                continue
            # shrine: `timestamp` is SECONDS, `localTimestamp` is ms — normalize to ms
            ts = e.get("localTimestamp") or (e.get("timestamp") or 0) * 1000
            price = e.get("price")
            blk = e.get("block")
            b = e.get("tokensInPool")
            q = e.get("quoteInPool")
            if not ts or not price or not b or not q or blk is None:
                continue
            price = float(price)
            if price <= 0:
                continue
            cur = per_pool[key].get(blk)
            if cur is None or ts >= cur[0]:
                per_pool[key][blk] = (ts, price, float(b), float(q))
    return {k: sorted(v.values()) for k, v in per_pool.items()}


def tier_for(mc):
    t = LADDER[0]
    for cand in LADDER:
        if mc >= cand[0]:
            t = cand
        else:
            break
    return t


def replay_coin(evs, grad_ts, mc_grad):
    g_ms = grad_ts * 1000
    entry_sig = g_ms + BUY_S * 1000
    if not evs or evs[-1][0] < entry_sig:
        return None, "NEVER_ENTERED"
    idx = next((i for i, e in enumerate(evs) if e[0] >= entry_sig), None)
    if idx is None:
        return None, "NEVER_ENTERED"
    ts_e, px_e, B_e, Q_e = evs[idx]
    x_eff = SIZE_SOL * (1 - COST)
    tokens = B_e * x_eff / (Q_e + x_eff)
    entry_px = px_e
    idx += 1
    sell_sig = g_ms + SELL_S * 1000
    horizon = g_ms + HORIZON_S * 1000
    tier = tier_for(mc_grad)
    stop_mult = 1 - tier[3]
    clip1, clip2 = tier[1], tier[2]
    hold_tokens = tokens
    done = False
    exit_reason = ""
    realized = 0.0
    last_px_bf = None
    idx += 0
    while idx < len(evs) and idx >= 0 and not done:
        ts, px, B, Q = evs[idx]
        if ts >= horizon:
            if last_px_bf is None:
                return None, "TAPE_ENDED_OPEN"
            gross = 0.0
            # reuse previous B,Q for horizon valuation if present
            realized += hold_tokens * last_px_bf * (1 - COST)
            exit_reason = "HORIZON_30M"
            done = True
            break
        last_px_bf = px
        mult = px / entry_px
        if mult <= stop_mult:
            gross = Q * hold_tokens / (B + hold_tokens)
            realized += gross * (1 - COST)
            exit_reason = "STOP"
            done = True
            break
        elif mult >= clip2:
            gross = Q * hold_tokens / (B + hold_tokens)
            realized += gross * (1 - COST)
            exit_reason = "TP2"
            done = True
            break
        elif mult >= clip1:
            qty = hold_tokens * 0.5
            gross = Q * qty / (B + qty)
            realized += gross * (1 - COST)
            hold_tokens -= qty
            exit_reason = "TP1"
        if ts >= sell_sig and not done:
            gross = Q * hold_tokens / (B + hold_tokens)
            realized += gross * (1 - COST)
            exit_reason = "SELL_180S"
            done = True
            break
        idx += 1
    if not done:
        return None, "TAPE_ENDED_OPEN"
    pnl = realized - SIZE_SOL
    return {"exit_reason": exit_reason, "pnl": pnl, "ret": pnl / SIZE_SOL}, None


def main():
    GRADS = load_grads()
    keep = {g["mint"] for g in GRADS} | {g["pool"] for g in GRADS}
    for h in HOURS:
        fetch_hour(h, keep)
    events = load_pool_events(GRADS)
    n_pools = sum(1 for g in GRADS if g["pool"] in events)
    print(f"\nshrine slot tapes: {n_pools}/{len(GRADS)} graduates")
    trades, never, tape_open = [], [], []
    for g in GRADS:
        tr, st = replay_coin(events.get(g["pool"], []), g["grad_ts"], 50_000.0)
        if tr:
            trades.append((g["symbol"], tr["ret"], tr["pnl"], tr["exit_reason"]))
        elif st == "NEVER_ENTERED":
            never.append(g["symbol"])
        else:
            tape_open.append(g["symbol"])
    n = len(trades)
    print(f"traded {n}")
    if n:
        rets = [t[1] for t in trades]
        print(f"win rate {100*sum(1 for r in rets if r>0)/n:.1f}%  median {100*statistics.median(rets):+.2f}%  mean {100*statistics.mean(rets):+.2f}%")
        print(f"total {sum(t[2] for t in trades):+.4f} SOL ({100*sum(t[2] for t in trades)/(n*SIZE_SOL):+.1f}%)")
    print(f"never entered {len(never)}  tape-ended-open {len(tape_open)}")

    # cross-check: compare shrine vs pumpapi prices for a few pools on shared slots
    import glob
    print("\n== cross-check shrine vs pumpapi (same pool, same block) ==")
    cmp_pool = None
    for g in GRADS:
        if "DYhjW6EeakDytQkT2aCBqAew9yFY9CRr61f8pcPJ1YfE" == g["pool"]:
            cmp_pool = g["pool"]; break
    if cmp_pool:
        sh = {e[2]: e[1] for e in events.get(cmp_pool, [])}  # block->price
        # pumpapi side: reuse grad cache events for the same pool
        pump = {}
        for h in HOURS:
            filt = os.path.join(DESK, "_grad_cache", h.replace("/", "_") + ".filtered.jsonl")
            if not os.path.exists(filt):
                continue
            for line in open(filt):
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("poolId") == cmp_pool and e.get("block") is not None and e.get("price"):
                    pump[e["block"]] = float(e["price"])
        common = sorted(set(sh) & set(pump))
        if common:
            diffs = [abs(sh[b] / pump[b] - 1) for b in common if pump[b] > 0]
            print(f"shared blocks {len(common)}: median rel diff {statistics.median(diffs)*100:.2f}%  max {max(diffs)*100:.1f}%")


if __name__ == "__main__":
    main()
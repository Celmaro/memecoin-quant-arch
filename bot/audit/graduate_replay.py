#!/usr/bin/env python3
"""graduate_replay.py — Oct 10 2026 graduate slot replay (user-spec).

Replays each graduate of graduates-2026-10-10-1400-1600utc.csv on its OWN slot
tape (pumpapi replay for that day's hours), using the 6 user rules:

  1. fills at END of first slot after the trigger, never at its own price:
     buy signal +15s -> first slot end at/after +15s;
     sell signal +180s -> first slot end at/after +180s;
     stop/TP OBSERVED at slot N end -> fills at slot N+1 end (one slot later)
  2. costs per side: 1% PumpSwap fee + 0.5% priority/slippage = 1.5%
  3. size moves price (constant-product): after costs x SOL -> B*x/(Q+x) tokens
     (B=tokensInPool, Q=quoteInPool at fill slot end); n tokens ->
     Q*n/(B+n) SOL, minus sell costs
  4. stop/TP checked at each slot end vs the entry fill price
     (live desk ladder: stop 0.75, clip1 2.00 sells 50%, clip2 2.50 sells rest)
  5. still holding at +30 min -> sell at last price before it; tape ends while
     a trade is open -> leave that coin out
  6. no slot after the entry signal -> that coin never reached the entry

Also runs the idealized comparison: same rules and coins filled at the signal's
own price (the last price seen), NO costs, NO size impact.
"""
import json, os, sys, time, csv, statistics, collections
import urllib.request, subprocess

DESK = os.path.expanduser("~/memecoin-quant")
GRAD_CSV = "/home/agentuser/.hermes/opencraft-attachments/8e486aaf-4d3c-468b-b117-3fd8ae46b7d8/content"
ODYSSEY_REF = "/home/agentuser/.hermes/opencraft-attachments/75704aeb-92dd-41a7-9f6b-7a2154eaf9b4/content"
CACHE = os.path.join(DESK, "_grad_cache")
os.makedirs(CACHE, exist_ok=True)
REPLAY = "https://replay.pumpapi.io/{}.jsonl.zst"
HOURS = ["2026/10/10/14", "2026/10/10/15", "2026/10/10/16"]

BUY_S = 15
SELL_S = 180
HORIZON_S = 1800
COST = 0.015
SOL_USD = 150.0
SIZE_SOL = 20.0 / SOL_USD
# live desk ladder (position_monitor.EXIT_TIERS, Oct 20 tape retune)
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
        out.append({"mint": r["mint"], "pool": r["pool"], "grad_ts": t,
                    "touched_minus80": int(r["touched_minus80_10m"] or 0),
                    "touched_2x": int(r["touched_2x_10m"] or 0),
                    "cap_sol_at_grad": float(r["cap_sol_at_grad"] or 0)})
    return out


def fetch_hour(h):
    """Download + stream-decompress + filter to our graduate pools/mints."""
    name = h.replace("/", "_")
    raw = os.path.join(CACHE, name + ".jsonl.zst")
    filt = os.path.join(CACHE, name + ".filtered.jsonl")
    if not os.path.exists(filt) and not os.path.exists(raw):
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
        py = sys.executable
        code = ("import zstandard,sys,json,io;"
                "d=io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(open(sys.argv[1],'rb')),encoding='utf-8',errors='replace');\n"
                "keep=set(json.load(open(sys.argv[2]))); out=open(sys.argv[3],'w')\n"
                "for line in d:\n"
                "  try:\n"
                "    e=json.loads(line);\n"
                "    if e.get('poolId') in keep or e.get('mint') in keep: out.write(line)\n"
                "  except Exception: pass\n"
                "out.close()\n")
        with open(os.path.join(CACHE, "_filter.py"), "w") as f:
            f.write(code)
        keep = [r["pool"] for r in GRADS] + [r["mint"] for r in GRADS]
        with open(os.path.join(CACHE, "_keep.json"), "w") as f:
            json.dump(keep, f)
        print(f"filter {name} ...", flush=True)
        r = subprocess.run([py, os.path.join(CACHE, "_filter.py"), raw,
                            os.path.join(CACHE, "_keep.json"), filt])
        if r.returncode != 0:
            print("filter failed rc", r.returncode)


def load_pool_events():
    """pool -> list[(ts_ms, price, B, Q)] where B=tokensInPool, Q=quoteInPool,
    ONE row per block (slot) = slot-END state (last event of that block)."""
    pools = {g["pool"] for g in GRADS}
    per_pool = collections.defaultdict(dict)  # pool -> {block: (ts, price, B, Q)}
    for h in HOURS:
        filt = os.path.join(CACHE, h.replace("/", "_") + ".filtered.jsonl")
        if not os.path.exists(filt):
            continue
        for line in open(filt):
            try:
                e = json.loads(line)
            except Exception:
                continue
            key = e.get("poolId")
            if key not in pools:
                continue
            if e.get("action") not in ("buy", "sell"):
                continue
            ts = e.get("timestamp")
            price = e.get("price")
            b = e.get("tokensInPool")
            q = e.get("quoteInPool")
            blk = e.get("block")
            if not ts or not price or not b or not q or blk is None:
                continue
            price = float(price)
            if price <= 0:
                continue
            # last event per block = slot-end state
            cur = per_pool[key].get(blk)
            if cur is None or ts >= cur[0]:
                per_pool[key][blk] = (ts, price, float(b), float(q))
    out = {}
    for k, blk in per_pool.items():
        out[k] = sorted(blk.values())
    return out


def tier_for(mc):
    t = LADDER[0]
    for cand in LADDER:
        if mc >= cand[0]:
            t = cand
        else:
            break
    return t


def replay_coin(evs, grad_ts, mc_grad, realistic=True):
    """Replay one graduate. evs = sorted slot-ends (ts_ms, price, B, Q).
    realistic=True: rule-1 fills, 1.5% costs, size impact.
    realistic=False: fill at signal's own price (last price seen), no costs/impact.
    Returns (trade, status) or (None, status): status in
    {NEVER_ENTERED, TAPE_ENDED_OPEN, TRADED}."""
    g_ms = grad_ts * 1000
    entry_sig = g_ms + BUY_S * 1000
    # rule 6: no slot at/after entry signal -> never reached the entry
    if not evs or evs[-1][0] < entry_sig:
        return None, "NEVER_ENTERED"
    # ---- entry fill ----
    if realistic:
        # rule 1: first slot end at/after the +15s signal
        idx = next((i for i, e in enumerate(evs) if e[0] >= entry_sig), None)
        if idx is None:
            return None, "NEVER_ENTERED"
        ts_e, px_e, B_e, Q_e = evs[idx]
        x_eff = SIZE_SOL * (1 - COST)
        tokens = B_e * x_eff / (Q_e + x_eff)   # rule 3 buy, after costs
        entry_px = px_e
        idx += 1
    else:
        # idealized: fill at the signal's own price = LAST price seen at/before
        # the signal (the price your decision actually saw)
        idx = next((i for i, e in enumerate(evs) if e[0] >= entry_sig), None)
        if idx is None:
            return None, "NEVER_ENTERED"
        if idx == 0:
            ts_e, px_e = evs[0][0], evs[0][1]
        else:
            ts_e, px_e = evs[idx - 1][0], evs[idx - 1][1]
        tokens = SIZE_SOL / px_e              # no costs, no impact
        entry_px = px_e
        # idealized starts scanning exits from the entry-signal slot onward
        idx = next((i for i, e in enumerate(evs) if e[0] >= entry_sig), None)
        idx = (idx + 1) if idx is not None else len(evs)

    # ---- exit walk ----
    hold_tokens = tokens
    done = False
    exit_reason = ""
    # seat state
    sell_sig = g_ms + SELL_S * 1000
    horizon = g_ms + HORIZON_S * 1000
    tier = tier_for(mc_grad)
    stop_mult = 1 - tier[3]
    clip1 = tier[1]
    clip2 = tier[2]
    realized = 0.0  # SOL proceeds (net)
    last_px_before_horizon = None     # (px, B, Q) of last slot strictly before +30min
    # pending stop/TP fill (observed at slot N end -> fill at slot N+1 end)
    pending_exit = None  # ("stop"|"clip1"|"clip2"|"sell")
    while idx < len(evs) and not done:
        ts, px, B, Q = evs[idx]
        # rule 5: sell at last price BEFORE +30min if still holding
        if ts >= horizon:
            # fill at the last slot end strictly before horizon
            if realistic:
                if last_px_before_horizon is None:
                    # even the entry slot is beyond horizon (pool died) -> can't price
                    return None, "TAPE_ENDED_OPEN"
                p_h, B_h, Q_h = last_px_before_horizon
                gross = Q_h * hold_tokens / (B_h + hold_tokens)
                realized += gross * (1 - COST)
            else:
                p_h = last_px_before_horizon[0]
                realized += hold_tokens * p_h
            exit_reason = "HORIZON_30M"
            done = True
            break
        # record last seen price/pool state for horizon fallback
        last_px_before_horizon = (px, B, Q)
        mult = px / entry_px

        if pending_exit is not None:
            # fill the pending exit at THIS slot end (the next slot after observation)
            if pending_exit == "TP1":
                # ladder clip1: sell HALF of remaining
                frac = 0.5
            else:
                frac = 1.0
            qty = hold_tokens * frac
            if realistic:
                gross = Q * qty / (B + qty)
                realized += gross * (1 - COST)
            else:
                realized += qty * px
            hold_tokens -= qty
            if hold_tokens <= 1e-12 or pending_exit != "TP1":
                exit_reason = pending_exit
                done = True
                break
            else:
                # half still riding; clear the pending and keep walking
                exit_reason = pending_exit
                pending_exit = None

        if realistic:
            # observe stop/TP at this slot end (rule 4) -> pending for next slot
            if mult <= stop_mult:
                pending_exit = "STOP"
            elif mult >= clip2:
                pending_exit = "TP2"
            elif mult >= clip1:
                pending_exit = "TP1"
        else:
            # idealized: fill at the signal's own price = THIS slot's own px
            if mult <= stop_mult:
                realized += hold_tokens * px
                exit_reason = "STOP"
                done = True
                break
            elif mult >= clip2:
                realized += hold_tokens * px
                exit_reason = "TP2"
                done = True
                break
            elif mult >= clip1:
                # clip1 sells half at 2.0, rest keeps riding to 2.5/+180
                frac = 0.5
                qty = hold_tokens * frac
                realized += qty * px
                hold_tokens -= qty
                exit_reason = "TP1"
        # rule 1: +180s sell signal -> first slot end at/after it
        if ts >= sell_sig and not done and pending_exit is None:
            qty = hold_tokens
            if realistic:
                gross = Q * qty / (B + qty)
                realized += gross * (1 - COST)
            else:
                realized += qty * px
            hold_tokens -= qty
            exit_reason = "SELL_180S"
            if hold_tokens <= 1e-12:
                done = True
                break
            # (TP1 already sold half; rest liquidated here -> done)
        idx += 1

    if not done:
        # tape ended while open
        return None, "TAPE_ENDED_OPEN"
    pnl_sol = realized - SIZE_SOL
    ret = pnl_sol / SIZE_SOL
    return {"exit_reason": exit_reason, "pnl_sol": pnl_sol, "ret": ret,
            "entry_px": entry_px, "exit_pos": idx}, "TRADED"


def main():
    global GRADS
    GRADS = load_grads()
    if "--fetch" in sys.argv:
        for h in HOURS:
            fetch_hour(h)
    events = load_pool_events()
    n_pools = sum(1 for g in GRADS if g["pool"] in events)
    print(f"pool slot tapes: {n_pools}/{len(GRADS)} graduates have events in replay")

    # 0) validate pumpapi tape vs the odyssey reference
    print("\n== validation vs odyssey reference file ==")
    ody = [g for g in GRADS if g["mint"] == "Ff41mqYhF7CqcVBCkFifKUnzavpf9qxByHtrcgbFpump"]
    if ody:
        pool = ody[0]["pool"]
        mine = events.get(pool, [])
        ref = []
        for line in open(ODYSSEY_REF):
            if line.startswith("#") or line.startswith("t,"):
                continue
            p = line.rstrip("\n").split(",")
            ref.append((float(p[0]), float(p[7])))
        matched = 0
        worst = 0.0
        for t_ref, px_ref in ref:
            cand = [m for m in mine if abs(m[0] - t_ref) < 5000]
            if not cand:
                continue
            m = min(cand, key=lambda x: abs(x[0] - t_ref))
            rel = abs(m[1] / px_ref - 1)
            worst = max(worst, rel)
            matched += 1
        print(f"  odyssey ref {len(ref)} rows vs pumpapi {len(mine)} slot-ends: "
              f"{matched} matched ({100*matched/len(ref):.0f}%), worst price err {worst*100:.1f}%")

    # first-10-min graduate stats from the CSV itself
    print("\n== first 10 minutes (graduates CSV columns) ==")
    n80 = sum(1 for g in GRADS if g["touched_minus80"])
    n2x = sum(1 for g in GRADS if g["touched_2x"])
    print(f"  touched_minus80_10m=1: {100*n80/len(GRADS):.1f}% ({n80}/{len(GRADS)})")
    print(f"  touched_2x_10m=1:      {100*n2x/len(GRADS):.1f}% ({n2x}/{len(GRADS)})")

    # run both passes
    for label, realistic in (("REALISTIC (slot-end fills, 1.5% cost/side, size impact)",
                              True),
                             ("IDEALIZED (signal's own price, no costs, no impact)",
                              False)):
        trades, never, tape_open = [], [], []
        for g in GRADS:
            evs = events.get(g["pool"], [])
            tr, st = replay_coin(evs, g["grad_ts"], g["cap_sol_at_grad"], realistic)
            if st == "TRADED":
                trades.append((g["symbol"] if g.get("symbol") else g["mint"][:6],
                               tr["ret"], tr["pnl_sol"], tr["exit_reason"]))
            elif st == "NEVER_ENTERED":
                never.append(g["mint"][:6])
            else:
                tape_open.append(g["mint"][:6])
        n = len(trades)
        print(f"\n== {label} ==")
        print(f"coins traded: {n}")
        if n:
            rets = [t[1] for t in trades]
            wins = sum(1 for r in rets if r > 0)
            total = sum(t[2] for t in trades)
            print(f"win rate: {100*wins/n:.1f}%")
            print(f"median return/trade: {100*statistics.median(rets):+.2f}%   mean: {100*statistics.mean(rets):+.2f}%")
            print(f"total PnL: {total:+.4f} SOL  ({100*total/(n*SIZE_SOL):+.1f}% of deployed {n*SIZE_SOL:.2f} SOL)")
            worst = sorted(trades, key=lambda t: t[1])[:3]
            print("worst 3:")
            for w in worst:
                print(f"   {w[0]}: {100*w[1]:+.1f}% ({w[2]:+.4f} SOL, {w[3]})")
        print(f"coins that never reached the entry: {len(never)} {never[:12]}")
        print(f"coins skipped (tape ended open): {len(tape_open)} {tape_open[:12]}")


if __name__ == "__main__":
    main()
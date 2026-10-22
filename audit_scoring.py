#!/usr/bin/env python3
"""Scoring-anomaly audit (Oct 20, after sniper + call-mc-decay fixes).

For every unique mint scanned in the last N hours:
  - scan score/verdict/flags from scan_log
  - call MC from coins.mc_at_call (the channel's call-time MC)
  - current MC via market_for (PumpData feed FIRST, DexScreener paced fallback)
  - outcome: mult vs call MC and vs scan-time MC

Anomalies:
  - FALSE LIVE: score >= 60 but mult_call < 0.5 (blessed then died) or < 0.1 (dead)
  - FALSE RISK: score < 40 but mult_call >= 1.5 (condemned then pumped) or >= 3 (big)
  - flag-level lifts (which flags co-occur with death)
Usage: .venv/bin/python audit_scoring.py [hours=24] [max=400]
"""
import sqlite3, json, time, sys, statistics
from collections import defaultdict

sys.path.insert(0, "/home/agentuser/memecoin-quant")
from desk_common import market_for, pumpdata_market

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace(".", "").isdigit() else 24.0
MAX = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 400

db = sqlite3.connect("/home/agentuser/memecoin-quant/aegis.db")
db.row_factory = sqlite3.Row
now = time.time()
cut = now - HOURS * 3600

# latest scan per mint + coins call meta
scan_rows = [dict(r) for r in db.execute(
    "SELECT mint, ts, score, verdict, flags_json, checks_json FROM scan_log WHERE ts >= ?", (cut,))]
latest = {}
for r in scan_rows:
    if r["mint"] not in latest or r["ts"] > latest[r["mint"]]["ts"]:
        latest[r["mint"]] = r
print(f"unique mints scanned in last {HOURS:.0f}h: {len(latest)}")

call_mc = {}
for r in db.execute("SELECT mint, mc_at_call, call_ts FROM coins WHERE mc_at_call IS NOT NULL"):
    if r["mc_at_call"]:
        call_mc[r["mint"]] = (r["mc_at_call"], r["call_ts"])

rows = []
todo = sorted(latest.items(), key=lambda kv: kv[1]["ts"], reverse=True)[:MAX]

# mark cache: persist resolved marks so re-runs don't re-hit APIs (429 lesson)
CACHE = "/home/agentuser/memecoin-quant/.audit_scoring_cache.json"
try:
    with open(CACHE) as _f:
        _c = json.load(_f)
except Exception:
    _c = {}
ok = fail = 0
for i, (mint, s) in enumerate(todo):
    if mint in _c and _c[mint].get("cur_mc") is not None:
        rows.append(_c[mint])
        continue
    cur_mc = None
    try:
        mkt = market_for(mint, timeout=8, retries=1) or {}
        cur_mc = mkt.get("marketCap") or mkt.get("mc")
    except Exception:
        pass
    try:
        cj = json.loads(s["checks_json"]) if isinstance(s["checks_json"], str) else (s["checks_json"] or {})
        scan_mc = (cj.get("market") or {}).get("mc") or 0
    except Exception:
        scan_mc = 0
    cm = call_mc.get(mint)
    mult_call = (cur_mc / cm[0]) if (cur_mc and cm and cm[0]) else None
    mult_scan = (cur_mc / scan_mc) if (cur_mc and scan_mc) else None
    flags = json.loads(s["flags_json"]) if s["flags_json"] else []
    rows.append({"mint": mint, "ts": s["ts"], "score": s["score"], "verdict": s["verdict"],
                 "flags": flags, "cur_mc": cur_mc, "scan_mc": scan_mc,
                 "call_mc": cm[0] if cm else None, "call_age_h": (now - cm[1]) / 3600 if cm and cm[1] else None,
                 "mult_call": mult_call, "mult_scan": mult_scan})
    if cur_mc:
        ok += 1
    else:
        fail += 1
    if (i + 1) % 60 == 0:
        print(f"  marked {i+1}/{len(todo)} (ok={ok} dead={fail})", flush=True)

# persist marks for instant re-analysis
try:
    for r in rows:
        _c[r["mint"]] = r
    with open(CACHE, "w") as _f:
        json.dump(_c, _f)
except Exception:
    pass

resolvable = [r for r in rows if r["cur_mc"]]
print(f"resolvable marks: {len(resolvable)}/{len(rows)} (dead/unlisted={len(rows)-len(resolvable)})")

base_dead = sum(1 for r in resolvable if (r["mult_call"] or 0) < 0.5) / max(len(resolvable), 1)
print(f"\n=== OVERALL: {len(resolvable)} coins, median mult_call = "
      f"{statistics.median([r['mult_call'] or 0 for r in resolvable]):.2f}x, "
      f"{(base_dead*100):.0f}% below 0.5x of call MC ===")

# score-band table
bands = [(60, 100, "LIVE 60-100"), (40, 60, "CAUTION 40-60"), (0, 40, "RISK 0-40")]
print("\n=== SCORE BAND vs OUTCOME ===")
for lo, hi, name in bands:
    b = [r for r in resolvable if lo <= (r["score"] or 0) < hi]
    if not b:
        continue
    mc = [r["mult_call"] or 0 for r in b]
    alive = sum(1 for x in mc if x >= 1.0)
    dead = sum(1 for x in mc if x < 0.5)
    print(f"  {name:16s} n={len(b):3d}  med={statistics.median(mc):.2f}x  ≥1.0x={100*alive/len(b):4.1f}%  <0.5x={100*dead/len(b):4.1f}%")

# FALSE LIVE / FALSE RISK anomalies
print("\n=== FALSE LIVE (score>=60 but died) — top 12 ===")
fl = [r for r in resolvable if (r["score"] or 0) >= 60 and (r["mult_call"] is not None) and r["mult_call"] < 0.5]
for r in sorted(fl, key=lambda x: x["mult_call"] or 0)[:12]:
    print(f"  {r['mint'][:14]}… s={r['score']:.0f} {r['verdict']} "
          f"call=${r['call_mc']:,.0f} now=${r['cur_mc']:,.0f} mult={r['mult_call']:.2f}x "
          f"call_age={r['call_age_h']:.1f}h flags={[f[:28] for f in r['flags'] if not f.startswith('deep_')][:6]}")

print("\n=== FALSE RISK (score<40 but pumped) — top 12 ===")
fr = [r for r in resolvable if (r["score"] or 0) < 40 and (r["mult_call"] is not None) and r["mult_call"] >= 1.5]
for r in sorted(fr, key=lambda x: -(x["mult_call"] or 0))[:12]:
    print(f"  {r['mint'][:14]}… s={r['score']:.0f} {r['verdict']} "
          f"call=${r['call_mc']:,.0f} now=${r['cur_mc']:,.0f} mult={r['mult_call']:.2f}x "
          f"flags={[f[:28] for f in r['flags'] if not f.startswith('deep_')][:6]}")

# flag-level lift (vs base dead rate)
print("\n=== FLAG LIFTS (co-occurrence with death <0.5x) — n>=6 ===")
flag_stats = defaultdict(lambda: {"n": 0, "dead": 0})
for r in resolvable:
    if r["mult_call"] is None:
        continue
    for f in r["flags"]:
        if f.startswith("deep_") or f.startswith("learned"):
            continue
        flag_stats[f]["n"] += 1
        if r["mult_call"] < 0.5:
            flag_stats[f]["dead"] += 1
for f, st in sorted(flag_stats.items(), key=lambda kv: kv[1]["dead"] / max(kv[1]["n"], 1)):
    if st["n"] >= 6:
        lift = 100 * st["dead"] / st["n"] - 100 * base_dead
        print(f"  {f[:55]:57s} n={st['n']:3d} dead={100*st['dead']/st['n']:4.0f}% lift={lift:+5.1f}pp")
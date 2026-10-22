import sys, json, time
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import dex_pair

NOW = time.time()
print("=" * 64)
print("PAPER ENGINE AUDIT — entries missed & monitoring gaps")
print("=" * 64)

b = json.load(open('/home/agentuser/memecoin-quant/paper_book.json'))
print(f"\ncash: ${b.get('cash',0):.2f} | equity: ${b.get('equity', b.get('cash',0)):.2f} | open: {len(b['positions'])}")
print(f"realized pnl: ${b.get('realized',0):+.2f}")
print("\ntrade log (closed):")
for t in b.get("closed", []):
    print("  ", t)

# 1) Which fresh calls (<5m at the time) did the engine NOT buy?
print("\n--- ENTRIES: all calls vs what was bought ---")
calls = []
for line in open('/home/agentuser/memecoin-quant/calls.jsonl'):
    try:
        d = json.loads(line)
    except Exception:
        continue
    if d.get("dt"):
        calls.append(d)
print(f"total calls recorded: {len(calls)}")
bought = {p["mint"] for p in b["positions"].values()} | {c.get("mint") for c in b.get("closed", [])} | {t.get("mint") for t in b.get("trades", []) if isinstance(t, dict)}
# reconstruct: everything we ever had = closed log + open
ever = set()
for t in b.get("trades", []) if isinstance(b.get("trades"), list) else []:
    ever.add(t.get("mint"))
for p in b["positions"].values():
    ever.add(p["mint"])
for c in b.get("closed", []) if isinstance(b.get("closed"), list) else []:
    if isinstance(c, dict):
        ever.add(c.get("mint"))
# closed trades were positions popped without logging mint list — reconstruct from journal
import glob
missed = []
bought_syms = set()
for c in calls:
    m = c["mint"]
    sym = c.get("symbol") or "?"
    if m in ever:
        bought_syms.add(sym)
        continue
    # was it fresh (<5m) when first seen by desk? check if recorded within 5m of post
    def _dt(ts):
        import time as _t
        return _t.mktime(_t.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - _t.timezone if ts else None
    post = _dt(c.get("dt"))
    rec = c.get("ts")
    lag = (rec - post) / 60 if post and rec else None
    missed.append((sym, m[:10], lag))

print(f"\ncalls NEVER bought (no position record): {len(missed)}")
fresh_and_fast = [x for x in missed if x[2] is not None and x[2] < 6]  # recorded within 6m of post = was buyable fresh
print(f"  of those, fresh-caught (<6m lag, engine SHOULD have bought): {len(fresh_and_fast)}")
for sym, m, lag in fresh_and_fast[:15]:
    print(f"    MISSED ${sym} {m} (caught {lag:.1f}m after post)")
late = [x for x in missed if x[2] is not None and x[2] >= 6]
print(f"  caught late (6m+ lag, correctly skipped as stale): {len(late)}")

# 2) monitoring gaps: desk_tick analyzes positions.json — paper positions need 'size' + 'symbol'
print("\n--- MONITORING GAPS ---")
import json as _j
pos = _j.load(open('/home/agentuser/memecoin-quant/positions.json'))
gaps = []
for m, p in pos.items():
    if "size" not in p: gaps.append(f"{m[:10]}: missing 'size'")
    if "symbol" not in p: gaps.append(f"{m[:10]}: missing 'symbol'")
print(f"positions.json entries with schema gaps: {len(gaps)}")
for g in gaps: print("   ", g)
# book vs positions.json sync
book_mints = {p["mint"] for p in b["positions"].values()}
pos_mints = set(pos.keys())
print(f"book mints: {len(book_mints)}, positions.json mints: {len(pos_mints)}")
print(f"  in book but not monitored: {book_mints - pos_mints}")
print(f"  monitored but not in book: {pos_mints - book_mints}")
# paper_tick bug check: closed trades don't update 'trades' log
print(f"\nclosed-trade log present in book: {'trades' in b or 'closed' in b}")
print("  (book pops positions on close WITHOUT recording the trade -> P&L history lost!)")

import sys, json, time, collections
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import db

print("=" * 64)
print("SIGNAL QUALITY AUDIT — what did calls become?")
print("=" * 64)

NOW = time.time()
con = db()
rows = con.execute("select mint, symbol, channel, call_ts, mc, aegis_score, verdict from call_metrics order by call_ts desc limit 60").fetchall()
print(f"calls tracked: {len(rows)}")
# outcome snapshot for calls >=45m old (enough time to have played out)
from desk_common import dex_pair
aged = outcomes = wins = stops = flat = dead = 0
for r in rows:
    age_min = (NOW - r['call_ts']) / 60
    if age_min < 45:
        continue
    aged += 1
    p = dex_pair(r['mint']) or {}
    mc = p.get('marketCap') or 0
    if not mc:
        dead += 1
        continue
    outcomes += 1
    call_mc = r['mc'] or 0
    if call_mc and mc >= call_mc * 1.3:
        wins += 1
    elif call_mc and mc <= call_mc * 0.7:
        stops += 1
    else:
        flat += 1
print(f"\ncalls aged 45m+: {aged}")
print(f"  dead (no market): {dead}")
print(f"  up 30%+ from call: {wins}")
print(f"  down 30%+ from call: {stops}")
print(f"  flat: {flat}")
if outcomes:
    print(f"  hit rate among live: {wins}/{outcomes} = {wins/outcomes*100:.0f}%")

# per-channel scoreboard
print("\nper-channel performance (calls aged 45m+):")
chans = collections.defaultdict(lambda: {"n": 0, "win": 0, "stop": 0, "dead": 0})
for r in rows:
    age_min = (NOW - r['call_ts']) / 60
    if age_min < 45:
        continue
    ch = r['channel']
    p = dex_pair(r['mint']) or {}
    mc = p.get('marketCap') or 0
    call_mc = r['mc'] or 0
    c = chans[ch]
    c["n"] += 1
    if not mc:
        c["dead"] += 1
    elif call_mc and mc >= call_mc * 1.3:
        c["win"] += 1
    elif call_mc and mc <= call_mc * 0.7:
        c["stop"] += 1
for ch, c in sorted(chans.items(), key=lambda kv: -kv[1]["n"]):
    live = c["n"] - c["dead"]
    wr = f'{c["win"]}/{live} ({c["win"]/max(live,1)*100:.0f}%)' if live else "n/a"
    print(f"  {ch:22s} calls={c['n']:2d} win30={wr:12s} stopped={c['stop']} dead={c['dead']}")

# AEGIS score vs outcome correlation (the filter test)
print("\nAEGIS score vs outcome (does the score predict?):")
buckets = collections.defaultdict(lambda: [0, 0])  # score bucket -> [win, total]
for r in rows:
    age_min = (NOW - r['call_ts']) / 60
    if age_min < 45 or not r['aegis_score']:
        continue
    p = dex_pair(r['mint']) or {}
    mc = p.get('marketCap') or 0
    call_mc = r['mc'] or 0
    if not mc or not call_mc:
        continue
    b = int(r['aegis_score'] // 10) * 10
    buckets[b][1] += 1
    if mc >= call_mc * 1.3:
        buckets[b][0] += 1
for b in sorted(buckets):
    w, t = buckets[b]
    print(f"  score {b}-{b+9}: {w}/{t} won 30%+ ({w/max(t,1)*100:.0f}%)")
con.close()

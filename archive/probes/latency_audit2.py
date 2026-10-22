import sys, time, json, sqlite3, statistics
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import db

print("[4] Alert freshness (channel post -> scan_log write):")
con = db()
rows = con.execute("select mint, ts from scan_log order by ts desc limit 40").fetchall()
lags = []
for r in rows:
    cm = con.execute("select call_ts from call_metrics where mint=?", (r['mint'],)).fetchone()
    if cm and cm['call_ts']:
        lags.append((r['ts'] - cm['call_ts']) / 60)
# also: post-dt from calls.jsonl vs scan ts
post_lags = []
try:
    posts = {}
    for line in open('/home/agentuser/memecoin-quant/calls.jsonl'):
        d = json.loads(line)
        if d.get('dt') and d.get('mint'):
            t = time.mktime(time.strptime(d['dt'][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            posts.setdefault(d['mint'], t)
    for r in rows:
        if r['mint'] in posts:
            post_lags.append((r['ts'] - posts[r['mint']]) / 60)
except Exception as e:
    print("   calls.jsonl parse err:", str(e)[:60])
con.close()
if lags:
    print(f"   call_metrics->scan_log: median {statistics.median(lags):.1f}m  min {min(lags):.1f}m  max {max(lags):.1f}m  (n={len(lags)})")
if post_lags:
    fresh = [l for l in post_lags if 0 <= l < 60]
    print(f"   channel post->scan_log: median {statistics.median(post_lags):.1f}m  min {min(post_lags):.1f}m  max {max(post_lags):.1f}m  (n={len(post_lags)}, fresh-only median {statistics.median(fresh):.1f}m)")

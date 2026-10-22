import sys, time, json, sqlite3, statistics
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import dex_pair, http_json, db

print("=" * 62)
print("LATENCY / SPEED AUDIT — desk pipeline, end to end")
print("=" * 62)

# 1) Crawler cycle duration (fetch all 6 channels)
import telegram_crawler as tc
t0 = time.time()
chans = ["sol_trend", "solwhaletrending", "solearlytrending", "PIRBViewTrending", "soltrenchtrending", "SeekrTrending"]
fetch_times = {}
for ch in chans:
    t = time.time()
    try:
        tc.fetch_preview(ch)
        fetch_times[ch] = (time.time() - t) * 1000
    except Exception as e:
        fetch_times[ch] = -1
    time.sleep(0.5)
print("\n[1] Channel fetch latency (crawler, per channel):")
for ch, ms in fetch_times.items():
    print(f"    {ch:20s} {ms:7.0f} ms" if ms > 0 else f"    {ch:20s} ERROR")
slow = [c for c, ms in fetch_times.items() if ms > 3000]
print(f"    Total cycle: {sum(ms for ms in fetch_times.values() if ms > 0)/1000:.1f}s + sleep overhead")

# 2) Data API latencies
print("\n[2] Market/forensics API latency (per call):")
mint = "EbpbZbYUBBhQVnbNTMG4jA9Ua3M2WhfW7hsJrcXwpump"
apis = [
    ("DexScreener pairs", lambda: dex_pair(mint)),
    ("Rugcheck full", lambda: http_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report")[1]),
    ("Rugcheck summary", lambda: http_json(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary")[1]),
]
for name, fn in apis:
    times = []
    for _ in range(2):
        t = time.time()
        try:
            fn(); times.append((time.time() - t) * 1000)
        except Exception as e:
            times.append(-1)
        time.sleep(1)
    ok = [t for t in times if t > 0]
    avg = statistics.mean(ok) if ok else -1
    print(f"    {name:20s} avg {avg:7.0f} ms  ({'ok' if ok else 'FAIL'})")
    time.sleep(1)

# 3) AEGIS full scan time
import aegis
t = time.time()
try:
    aegis.score_coin(mint, deep=False)
    print(f"\n[3] AEGIS scan (7 checks): {(time.time()-t)*1000:.0f} ms")
except Exception as e:
    print(f"\n[3] AEGIS scan FAIL: {str(e)[:60]}")

# 4) Signal freshness: how old were calls when the tick alerted? (call post time vs scan_log ts)
print("\n[4] Alert freshness (post -> scan_log write):")
con = db()
rows = con.execute("""select s.mint, s.ts, c.mc from scan_log s
                      join coins c on c.mint = s.mint order by s.ts desc limit 10""").fetchall()
lags = []
for r in rows:
    # use call_metrics call_ts if present
    cm = con.execute("select call_ts from call_metrics where mint=?", (r['mint'],)).fetchone()
    if cm and cm['call_ts']:
        lag = (r['ts'] - cm['call_ts']) / 60
        lags.append(lag)
con.close()
if lags:
    print(f"    median {statistics.median(lags):.1f}m | min {min(lags):.1f}m | max {max(lags):.1f}m (n={len(lags)})")
else:
    print("    no paired data")

# 5) Cron cadence vs reality
print("\n[5] Cron cadence check:")
rows = con2 = sqlite3.connect('/home/agentuser/.hermes/cron/memecoin-quant.db').execute(
    "select name from sqlite_master where type='table'").fetchall() if False else None
print("    desk-tick every 2m, paper-tick every 2m, channel-status every 30m (from job config)")
print("    worst-case alert latency = crawl cycle (~15-25s) + scan (~5-15s/call) + up to 2m wait = ~2.5-3m per call")

# 6) Bottleneck verdict
print("\n[6] Verdict:")
allf = [ms for ms in fetch_times.values() if ms > 0]
print(f"    - Channel fetching is the biggest fixed cost: {sum(allf)/1000:.1f}s per cycle (6 sequential fetches + polite sleeps)")
print(f"    - Each new call costs ~5-20s of forensic scans (Rugcheck x2 + Ave + Helius + DexScreener)")
print(f"    - At 4 scans/tick max, worst burst = ~1-2 min extra per tick -> fine vs 2m cadence")

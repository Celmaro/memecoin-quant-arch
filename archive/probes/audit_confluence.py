import sys, json, time, collections
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import db

NOW = time.time()
print("=" * 64)
print("CONFLUENCE AUDIT — same mint across channels")
print("=" * 64)

# 1) from calls.jsonl history: how often do multiple channels call the same mint?
hist = collections.defaultdict(lambda: {"chans": set(), "dts": [], "syms": set()})
for line in open('/home/agentuser/memecoin-quant/calls.jsonl'):
    try:
        d = json.loads(line)
    except Exception:
        continue
    m = d.get("mint")
    if not m:
        continue
    hist[m]["chans"].add(d.get("channel"))
    if d.get("dt"):
        hist[m]["dts"].append(d["dt"])
    hist[m]["syms"].add(d.get("symbol"))

multi = {m: v for m, v in hist.items() if len(v["chans"]) >= 2}
print(f"total distinct mints called: {len(hist)}")
print(f"mints called by 2+ channels: {len(multi)} ({len(multi)/max(len(hist),1)*100:.0f}%)")
print("\nmulti-channel calls (confluence events):")
for m, v in sorted(multi.items(), key=lambda kv: -len(kv[1]["chans"]))[:12]:
    print(f"  {','.join(sorted(v['chans'])):50s} {'/'.join(str(s) for s in v['syms']):12s} {m[:10]}")

# 2) time gap between first and second channel call (was 2nd call made while still tradeable?)
def _dt(ts):
    import time as _t
    try:
        return _t.mktime(_t.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None
print("\nchannel lag (1st call -> 2nd call):")
for m, v in list(multi.items())[:10]:
    dts = sorted(x for x in (_dt(d) for d in v["dts"]) if x)
    if len(dts) >= 2:
        print(f"  {m[:10]}: {(dts[-1]-dts[0])/60:.0f}m between first and last channel call")

# 3) does desk-tick ALERT confluence? (check recent output files)
import glob, os
crons = sorted(glob.glob("/home/agentuser/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239/cron/output/df82762e0864/*.md"))
conf_alerts = sum(1 for f in crons if "CONFLUENCE" in open(f, errors="ignore").read())
print(f"\ndesk-tick outputs mentioning CONFLUENCE: {conf_alerts}/{len(crons)}")
print("(NOTE: crawl_all prints confluence line, but desk_tick's own alert text does NOT include confluence!)")

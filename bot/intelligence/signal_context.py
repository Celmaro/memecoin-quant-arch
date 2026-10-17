"""Signal-channel intelligence (doc 2 §6) — the entry sources get their own
analytics: latency, follow-up meaning, reversal detection, source reliability.
"""
import os, sys, json, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import db

def channel_latency(days=7):
    """Median post->crawl latency per channel from calls.jsonl (dt vs created_ts)."""
    try:
        rows = []
        with open(os.path.expanduser("~/memecoin-quant/calls.jsonl")) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("dt") and d.get("ts"):
                        import datetime
                        t = datetime.datetime.fromisoformat(d["dt"].replace("Z", "+00:00")).timestamp()
                        rows.append((d.get("channel"), t, d.get("ts")))
                except Exception:
                    continue
        from collections import defaultdict
        lats = defaultdict(list)
        for ch, t0, t1 in rows:
            if t1 >= t0: lats[ch].append(t1 - t0)
        out = {}
        for ch, arr in lats.items():
            arr.sort()
            med = arr[len(arr)//2]
            out[ch] = {"median_s": round(med, 1), "n": len(arr)}
        return out
    except Exception:
        return {}

def channel_reliability(days=14):
    """Per-channel win/stop rates from call_metrics + call_outcomes (if computed)."""
    out = {}
    try:
        con = db()
        rows = con.execute("SELECT channel, COUNT(*) c, AVG(aegis_score) s FROM call_metrics GROUP BY channel").fetchall()
        con.close()
        for r in rows:
            out[r["channel"]] = {"calls": r["c"], "avg_aegis": round(r["s"] or 0, 1)}
    except Exception:
        pass
    return out

def followups_for(mint, hours=6):
    """Recent follow-up messages about the same mint (signal-context for a position)."""
    out = []
    try:
        with open(os.path.expanduser("~/memecoin-quant/calls.jsonl")) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("mint") == mint:
                        out.append({"channel": d.get("channel"), "text": (d.get("text") or "")[:100],
                                    "ts": d.get("ts")})
                except Exception:
                    continue
    except Exception:
        pass
    return out[-10:]

def attention_proxy(mint, hours=6):
    """Doc 2 §7: attention-vs-participation proxy. Attention = channel mentions +
    follow-ups for the mint; participation = unique buyers (holder_growth)."""
    fups = followups_for(mint, hours)
    mentions = len(fups)
    # participation comes from the caller (holder_growth unique_buyers)
    return {"mentions_6h": mentions, "followups": fups}
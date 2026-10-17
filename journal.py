#!/usr/bin/env python3
"""PRFS journal & follow-up: log pass/reject, re-check rejected tokens at +1h/+24h,
produce filter-precision + trade-performance reports. (Post-Rejection Follow-up Sampling)
"""
import json, os, time, glob, statistics
from desk_common import db, dex_pair, log_event

def schedule_followup(mint, kind, hours, note=""):
    con = db()
    con.execute("INSERT OR REPLACE INTO followups(mint, due_ts, kind, note) VALUES(?,?,?,?)",
                (mint, time.time() + hours * 3600, kind, note))
    con.commit(); con.close()

def run_followups(verbose=True):
    """Check due followups: is the token dead/alive? Record outcome."""
    con = db()
    due = con.execute("SELECT * FROM followups WHERE done=0 AND due_ts<=?", (time.time(),)).fetchall()
    for f in due:
        try:
            pair = dex_pair(f["mint"])
        except Exception:
            pair = None
        if pair:
            mc = pair.get("marketCap") or 0
            liq = (pair.get("liquidity") or {}).get("usd") or 0
            result = f"alive mc=${mc:,.0f} liq=${liq:,.0f}"
            outcome = "SURVIVED"
            if mc < 2000 and liq < 500: outcome = "DEAD"
            elif liq < 300: outcome = "DRAINED"
        else:
            result = "no pair data (provider down or delisted)"
            outcome = "UNKNOWN_DATA"
        con.execute("UPDATE followups SET done=1, result=? WHERE mint=?", (result, f["mint"]))
        log_event({"event": "FOLLOWUP", "mint": f["mint"], "kind": f["kind"], "outcome": outcome, "result": result})
        if verbose:
            print(f"  [{f['kind']}] {f['mint'][:12]}... -> {outcome}: {result}")
    con.commit(); con.close()
    return len(due)

def trade_stats(days=14):
    """Aggregate POSITION_CLOSE events: win rate, avg hold, avg mult, PnL sum."""
    closes = []
    cutoff = time.time() - days * 86400
    for path in glob.glob(os.path.join(os.path.expanduser("~/memecoin-quant/journal"), "*.jsonl")):
        with open(path) as f:
            for line in f:
                try: e = json.loads(line)
                except Exception: continue
                if e.get("event") == "POSITION_CLOSE" and e.get("ts", 0) >= cutoff:
                    closes.append(e)
    if not closes:
        return {"trades": 0}
    wins = [c for c in closes if c.get("realized_usd", 0) > 0]
    mults = [c.get("mult", 1) for c in closes]
    holds = [c.get("hold_min", 0) for c in closes]
    return {
        "trades": len(closes), "wins": len(wins),
        "win_rate": round(len(wins) / len(closes) * 100, 1),
        "avg_mult": round(statistics.fmean(mults), 2) if mults else None,
        "avg_hold_min": round(statistics.fmean(holds), 1) if holds else None,
        "total_pnl": round(sum(c.get("realized_usd", 0) for c in closes), 2),
        "best": round(max((c.get("realized_usd", 0) for c in closes), default=0), 2),
        "worst": round(min((c.get("realized_usd", 0) for c in closes), default=0), 2),
    }

def filter_precision(days=14):
    """PRFS: of scanned coins, did RISK verdicts die while LIVE verdicts survived?"""
    con = db()
    cutoff = time.time() - days * 86400
    rows = con.execute("SELECT * FROM scan_log WHERE ts>=?", (cutoff,)).fetchall()
    if not rows:
        return {"scans": 0}
    buckets = {"LIVE": [], "CAUTION": [], "RISK": []}
    for r in rows:
        b = buckets.get(r["verdict"])
        if b is not None:
            b.append(r["mint"])
    # Alive now?
    def alive_pct(mints, n=8):
        alive = 0
        for m in mints[:n]:
            try:
                if dex_pair(m): alive += 1
            except Exception:
                pass
        return round(alive / max(1, len(mints[:n])) * 100, 1)
    return {"scans": len(rows), "live_bucket": len(buckets["LIVE"]), "caution": len(buckets["CAUTION"]),
            "risk": len(buckets["RISK"]),
            "live_alive_pct": alive_pct(buckets["LIVE"]),
            "risk_alive_pct": alive_pct(buckets["RISK"])}

def report(days=14):
    print(f"=== DESK JOURNAL ({days}d) ===")
    t = trade_stats(days)
    if t["trades"]:
        print(f"Trades: {t['trades']} | win rate {t['win_rate']}% | avg mult {t['avg_mult']}x | avg hold {t['avg_hold_min']}m")
        print(f"PnL: ${t['total_pnl']:+,.2f} | best ${t['best']:+,.2f} | worst ${t['worst']:+,.2f}")
    else:
        print("No closed trades yet.")
    fp = filter_precision(days)
    if fp["scans"]:
        print(f"\nAEGIS scans: {fp['scans']} (LIVE {fp['live_bucket']} / CAUTION {fp['caution']} / RISK {fp['risk']})")
        print(f"Filter precision: LIVE-bucket alive now {fp['live_alive_pct']}% vs RISK-bucket alive {fp['risk_alive_pct']}% (PRFS)")
    attribution_report(days)
    channel_stats(days)
    return {"trades": t, "filter": fp}

def attribution_report(days=14):
    """Tier 3.1 — join closed trades back to playbook-card features (outcome attribution)."""
    con = db()
    cutoff = time.time() - days * 86400
    # load closes
    closes = []
    for path in glob.glob(os.path.join(os.path.expanduser("~/memecoin-quant/journal"), "*.jsonl")):
        with open(path) as f:
            for line in f:
                try: e = json.loads(line)
                except Exception: continue
                if e.get("event") == "POSITION_CLOSE" and e.get("ts", 0) >= cutoff:
                    closes.append(e)
    if not closes:
        print("\n[attribution] No closed trades to attribute yet.")
        return
    cards = {}
    rows = con.execute("SELECT * FROM call_metrics").fetchall()
    for r in rows:
        cards[r["mint"]] = r
    def winrate(items):
        if not items: return None
        w = sum(1 for i in items if i.get("realized_usd", 0) > 0)
        return round(w / len(items) * 100, 1), len(items)
    print("\n=== ATTRIBUTION (closed trades vs playbook card) ===")
    by_fill = {}
    by_verdict = {}
    by_sm = {"smart": [], "no_smart": []}
    for c in closes:
        card = cards.get(c["mint"])
        fc = (card["fill_class"] if card else None) or "unknown"
        vd = (card["verdict"] if card else None) or "unknown"
        sm = bool(card and card["smart_money_count"])
        by_fill.setdefault(fc, []).append(c)
        by_verdict.setdefault(vd, []).append(c)
        by_sm["smart" if sm else "no_smart"].append(c)
    print("By fill_class (slow-grad):")
    for k, v in sorted(by_fill.items(), key=lambda x: -len(x[1])):
        wr, n = winrate(v)
        if wr is not None: print(f"   {k:12s}: win {wr}% ({n} trades)")
    print("By AEGIS verdict:")
    for k, v in sorted(by_verdict.items(), key=lambda x: -len(x[1])):
        wr, n = winrate(v)
        if wr is not None: print(f"   {k:10s}: win {wr}% ({n} trades)")
    for k, v in by_sm.items():
        wr, n = winrate(v)
        if wr is not None: print(f"   smart-money {k:8s}: win {wr}% ({n} trades)")

def channel_stats(days=14):
    """Tier 3.2 — per-channel latency + survival dashboard for the curated list."""
    con = db()
    cutoff = time.time() - days * 86400
    rows = con.execute("SELECT * FROM call_metrics WHERE call_ts>=?", (cutoff,)).fetchall()
    if not rows:
        print("\n[channels] No call metrics yet (run a crawl first).")
        return
    by_ch = {}
    for r in rows:
        ch = r["channel"] or "?"
        d = by_ch.setdefault(ch, {"n": 0, "deltas": [], "fills": [], "smart": 0, "scored": []})
        d["n"] += 1
        if r["mc_delta_pct"] is not None: d["deltas"].append(r["mc_delta_pct"])
        if r["fill_class"]: d["fills"].append(r["fill_class"])
        if r["smart_money_count"]: d["smart"] += 1
        if r["aegis_score"] is not None: d["scored"].append(r["aegis_score"])
    print("\n=== CHANNEL QUALITY (curated list) ===")
    print(f"{'channel':<20} {'calls':>5} {'avgMCΔ%':>8} {'smart':>5} {'avgAEGIS':>8}")
    for ch, d in sorted(by_ch.items(), key=lambda x: -x[1]["n"]):
        avg_d = round(statistics.fmean(d["deltas"]), 1) if d["deltas"] else None
        avg_s = round(statistics.fmean(d["scored"]), 1) if d["scored"] else None
        print(f"{ch:<20} {d['n']:>5} {str(avg_d):>8} {d['smart']:>5} {str(avg_s):>8}")
    print("(negative avgMCΔ = channel calls AFTER price ran; positive = calls before/at move)")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "followups":
        n = run_followups()
        print(f"{n} follow-up(s) processed.")
    else:
        report(int(sys.argv[1]) if len(sys.argv) > 1 else 14)
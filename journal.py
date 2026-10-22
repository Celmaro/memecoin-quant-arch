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

def run_followups(verbose=True, limit=None, deadline_s=75):
    """Check due followups: is the token dead/alive? Record outcome.

    limit: bound rows per run so the supervisor batch never exceeds its
    subprocess timeout (F2, Oct 19); None = all due.
    deadline_s: stop after this many seconds (provider calls can take 20s x 3
    retries each); remaining rows are picked up by the next batch.
    Doc1-5 + Doc2-9 (Blix-parity replay + shadow portfolio): for mints that were
    scanned/called but are NOT currently held, also replay the FIXED paper exit rule
    over the observed move (entry MC from call_metrics/coins) so rejected/observed
    coins score apples-to-apples against held ones — the same rule, same thresholds:
    2x TP / -40% stop / else in-window. This measures false rejects (post-rejection
    trajectory) instead of only judging picks we actually took.
    """
    con = db()
    due = con.execute("SELECT * FROM followups WHERE done=0 AND due_ts<=? ORDER BY due_ts ASC", (time.time(),)).fetchall()
    if limit:
        due = due[:limit]
    deadline = time.time() + deadline_s
    processed = 0
    # fixed rule thresholds (match paper_tick tier + Blix pick rule)
    FIXED_TP = 2.0
    FIXED_STOP = 0.6
    try:
        held = {p.get("mint") for p in json.load(open("/home/agentuser/memecoin-quant/paper_book.json")).get("positions", {}).values()}
    except Exception:
        held = set()
    for f in due:
        if time.time() > deadline:
            break
        processed += 1
        try:
            pair = dex_pair(f["mint"], allow_fallback=False, timeout=8, retries=1)
        except Exception:
            pair = None
        if pair:
            mc = pair.get("marketCap") or 0
            liq = (pair.get("liquidity") or {}).get("usd") or 0
            result = f"alive mc=${mc:,.0f} liq=${liq:,.0f}"
            outcome = "SURVIVED"
            if mc < 2000 and liq < 500: outcome = "DEAD"
            elif liq < 300: outcome = "DRAINED"
            # Doc1-5/Doc2-9: fixed-rule replay for non-held observed mints
            entry_mc = None
            try:
                row = con.execute("SELECT mc, mc_at_call FROM call_metrics WHERE mint=? UNION ALL SELECT NULL, mc_at_call FROM coins WHERE mint=? LIMIT 1",
                                  (f["mint"], f["mint"])).fetchone()
                entry_mc = (row["mc"] or row["mc_at_call"]) if row else None
            except Exception:
                entry_mc = None
            if entry_mc and entry_mc > 0 and f["mint"] not in held:
                mult = mc / entry_mc
                if mult >= FIXED_TP:
                    rule = f"RULE_2X({mult:.2f}x_vs_call)"
                elif mult <= FIXED_STOP:
                    rule = f"RULE_STOPPED({mult:.2f}x_vs_call)"
                else:
                    rule = f"RULE_WINDOW({mult:.2f}x_vs_call)"
                outcome = outcome + "_" + rule.split("(")[0]
                result = result + " | " + rule
                log_event({"event": "RULE_REPLAY", "mint": f["mint"], "kind": f["kind"],
                           "entry_mc": entry_mc, "now_mc": mc, "mult": round(mult, 3), "replay": rule})
        else:
            result = "no pair data (provider down or delisted)"
            outcome = "UNKNOWN_DATA"
        con.execute("UPDATE followups SET done=1, result=?, outcome=? WHERE mint=?", (result, outcome, f["mint"]))
        log_event({"event": "FOLLOWUP", "mint": f["mint"], "kind": f["kind"], "outcome": outcome, "result": result})
        if verbose:
            print(f"  [{f['kind']}] {f['mint'][:12]}... -> {outcome}: {result}")
    con.commit(); con.close()
    return processed

def trade_stats(days=14):
    """Aggregate POSITION_CLOSE events + paper_book trades (audit P2-7: the two
    ledgers must both count, deduped by (mint, ts), so win-rate is unified)."""
    closes = []
    cutoff = time.time() - days * 86400
    seen = set()
    for path in glob.glob(os.path.join(os.path.expanduser("~/memecoin-quant/journal"), "*.jsonl")):
        with open(path) as f:
            for line in f:
                try: e = json.loads(line)
                except Exception: continue
                if e.get("event") == "POSITION_CLOSE" and e.get("ts", 0) >= cutoff:
                    k = (e.get("mint"), round(e.get("ts", 0)))
                    if k not in seen:
                        seen.add(k); closes.append(e)
    # paper_book trades carry real fills from the exit engine
    try:
        pb = json.load(open(os.path.expanduser("~/memecoin-quant/paper_book.json")))
        for t in (pb.get("trades") or []):
            ts = t.get("ts") or 0
            if ts < cutoff:
                continue
            # audit P2 (Oct 18): stage-1 partial clips (closed:false) are NOT
            # position closes — counting them inflated trade count + win rate.
            if not t.get("closed", True):
                continue
            k = (t.get("mint"), round(ts))
            if k in seen:
                continue
            seen.add(k)
            closes.append({"event": "POSITION_CLOSE", "mint": t.get("mint"), "ts": ts,
                           "realized_usd": t.get("pnl"), "mult": t.get("mult", 1),
                           "hold_min": (t.get("hold_min") or 0)})
    except Exception:
        pass
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
    """PRFS: of scanned coins, did RISK verdicts die while LIVE verdicts survived?

    Audit P2-7: dedupe mints (repeated scans must not double-count) and use
    scheduled followup outcomes (SURVIVED vs terminal) when available, falling
    back to current pair existence for coins with no followup yet."""
    con = db()
    cutoff = time.time() - days * 86400
    rows = con.execute("SELECT * FROM scan_log WHERE ts>=? ORDER BY ts DESC", (cutoff,)).fetchall()
    if not rows:
        return {"scans": 0}
    buckets = {"LIVE": {}, "CAUTION": {}, "RISK": {}}
    for r in rows:
        b = buckets.get(r["verdict"])
        if b is not None and r["mint"] not in b:
            b[r["mint"]] = r   # keep latest scan per mint
    # terminal outcomes from followups (SURVIVED or DEAD/DRAINED)
    outcome = {}
    try:
        for r in con.execute("SELECT mint, outcome FROM followups WHERE done=1 AND outcome IS NOT NULL"):
            outcome[r["mint"]] = r["outcome"]
    except Exception:
        pass
    con.close()

    def alive_pct(mints, n=20):
        if not mints:
            return None
        alive = 0; counted = 0
        for m in mints[:n]:
            o = outcome.get(m)
            if o == "SURVIVED":
                alive += 1; counted += 1
                continue
            if o in ("DEAD", "DRAINED"):
                counted += 1
                continue
            # no followup yet: live check (note: provider down != dead)
            try:
                if dex_pair(m):
                    alive += 1
                counted += 1
            except Exception:
                pass
        return round(alive / max(1, counted) * 100, 1) if counted else None

    l, c, rk = list(buckets["LIVE"]), list(buckets["CAUTION"]), list(buckets["RISK"])
    return {"scans": sum(len(v) for v in buckets.values()), "live_bucket": len(l), "caution": len(c), "risk": len(rk),
            "live_alive_pct": alive_pct(l),
            "risk_alive_pct": alive_pct(rk)}

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
#!/usr/bin/env python3
"""deep_audit.py — DEEP BACKTEST + ANALYSIS + AUDIT over the cached 12h tape.

Per curated call (12h window Oct 18 21:00 → Oct 19 09:00 UTC) extracts from the
cached PumpAPI replay:
  lag_s            : call_ts - first tape event (launch) — call freshness
  first_trade_s    : first tape event - launch (dead-tape detector)
  push60           : max mult reached within 60s of CALL (winners push fast)
  act60            : trade count in the first 60s after call
  buy_sell_5m      : buy:sell ratio in first 5 min (tape)
  fill_s           : seconds from call to first fillable event (latency proxy)
  outcome/pnl      : ladder replay result (mirrors position_monitor.EXIT_TIERS)
Joins AEGIS score/verdict per mint, then prints:
  1. overall matrix
  2. channel × outcome
  3. AEGIS band × outcome   (score calibration audit)
  4. winner-vs-loser feature shifts (lag/push/act/b:s)
  5. edge-rule simulation: combined gates vs buy-all
Writes deep_audit_rows.json for the full report.
"""
import json, os, sys, time, collections, statistics

DESK = os.path.expanduser("~/memecoin-quant")
CACHE = os.path.join(DESK, "_replay_cache")
LATENCY_MS = 1500
BUNDLE_MS = 3
# 3rd audit 6.4: shared SOL/USD fallback (desk_common.SOLUSD_FALLBACK), not a
# local literal that can drift from pumpdata_feed.
try:
    from desk_common import SOLUSD_FALLBACK as SOLUSD
except Exception:
    SOLUSD = 150.0
# LIVE ladder — single source of truth: position_monitor.EXIT_TIERS (Oct 20 tape
# retune: TP2.0/SL0.75, no 1.10 clip). NEVER copy the ladder into audit scripts.
sys.path.insert(0, DESK)
try:
    from position_monitor import EXIT_TIERS
except Exception:
    EXIT_TIERS = [(0, 2.00, 2.50, 0.25), (40_000, 2.00, 2.50, 0.25), (150_000, 2.20, 2.80, 0.25), (1_500_000, 2.50, 3.00, 0.25)]

_hour_cache = {}

def load_hour(prefix):
    if prefix in _hour_cache:
        return _hour_cache[prefix]
    out = []
    seen_lines = set()
    for cand in sorted(os.listdir(CACHE)):
        if cand.startswith(prefix + ".") and cand.endswith("mints.jsonl"):
            p = os.path.join(CACHE, cand)
            if os.path.getsize(p) == 0:
                continue
            for line in open(p):
                # skip exact duplicate lines across cache files
                if line in seen_lines:
                    continue
                seen_lines.add(line)
                try:
                    ev = json.loads(line)
                    out.append((ev.get("mint") or "", ev.get("timestamp") or 0,
                                float(ev.get("price") or 0), ev.get("action")))
                except Exception:
                    continue
    _hour_cache[prefix] = out
    return out

def hour_prefix(ts):
    gt = time.gmtime(ts)
    return "%02d_%02d_%02d_%02d" % (gt.tm_year, gt.tm_mon, gt.tm_mday, gt.tm_hour)

def gather_tape(mint, entry_ts):
    """Events for mint in [launch hour .. +2h] from cached hours."""
    parts = []
    for delta in range(4):
        parts.append(load_hour(hour_prefix(entry_ts + delta * 3600)))
    evs = []
    for p in parts:
        for m, ts, price, action in p:
            if m == mint:
                evs.append((ts, price, action))
    evs.sort()
    return evs

def clean(evs, mint_launch_guess=None):
    g = []
    prev = None
    dropped_crash = 0
    for ts, price, action in evs:
        if price <= 0:
            continue
        if g and ts - g[-1][0] <= BUNDLE_MS:
            continue
        # 3rd audit 6.9 (Oct 22): the >99% crash filter silently deleted events
        # that could be genuine rugs, not just curve-finalize artifacts. Keep the
        # filter (curve buy-outs are real) but COUNT removed events and append a
        # tail marker so sensitivity is visible instead of hidden.
        if g and price < g[-1][1] * 0.01:
            dropped_crash += 1
            continue
        g.append([ts, price, action])
    if dropped_crash:
        g.append([0, dropped_crash, "FILTERED_CRASH_COUNT"])
    return g

def audit_call(c):
    mint = c.get("mint") or ""
    entry_ts = c.get("ts") or 0
    raw = gather_tape(mint, entry_ts)
    g = clean(raw)
    row = {"mint": mint, "sym": c.get("symbol"), "chan": c.get("channel") or c.get("source") or "", "ts": entry_ts,
           "call_mc": float(c.get("mc") or 0)}
    if not g:
        row.update({"outcome": "NO_TAPE", "lag_s": None, "push60": None, "act60": 0,
                    "first_trade_s": None, "fill_s": None, "pnl": None})
        return row
    launch = g[0][0]
    row["lag_s"] = entry_ts - launch / 1000
    row["first_trade_s"] = 0.0
    # fill: first event at/after call+latency (captures price + ts once)
    fill = None
    fill_ts = None
    for ts, price, action in g:
        if ts >= entry_ts * 1000 + LATENCY_MS:
            fill = price
            fill_ts = ts
            row["fill_s"] = (ts - entry_ts * 1000) / 1000
            break
    if fill is None or fill_ts is None:
        row.update({"outcome": "NO_FILL", "push60": None, "act60": 0, "pnl": None})
        return row
    # features — P5 (Oct 20): NO LOOK-AHEAD. Features must be computable at the
    # modeled fill (call_ts + LATENCY_MS): only tape BEFORE/AT the fill is
    # observable at entry. The old code scanned [call0, t300] — post-fill data —
    # so the 86% edge validated a strategy that could see the future.
    pre_start = max(launch, fill_ts - 300_000)   # last 5m of OBSERVABLE tape
    push60 = 1.0
    act60 = 0
    buys = sells = 0
    for ts, price, action in g:
        if ts < pre_start or ts > fill_ts:      # strictly pre-fill window
            continue
        mult = price / fill
        if ts >= fill_ts - 60_000:              # the 60s of tape right before fill
            push60 = max(push60, mult)
            act60 += 1
        if action == "buy":
            buys += 1
        elif action == "sell":
            sells += 1
    row["push60"] = round(push60, 3)   # pre-fill push (what entry actually sees)
    row["act60"] = act60
    row["bs5m"] = round(buys / max(sells, 1), 2)
    row["buys5m"], row["sells5m"] = buys, sells
    # ladder outcome — P5: walk starts AT the fill (the desk buys at fill, then
    # the ladder runs forward). The old walk started at call0 (pre-fill prices).
    # 3rd audit 6.8: this file imported EXIT_TIERS but STILL hardcoded the dead
    # 1.10/1.30/0.15 ladder — the walk and the pnl proxy were both stale.
    # 4th audit 8.1 (Oct 23): round 3 left `EXIT_TIERS[0]` for EVERY trade; the
    # LIVE monitor picks the tier from CURRENT MC each evaluation (tier_for).
    # Select at the call's USD market cap so research matches the live policy.
    try:
        from position_monitor import EXIT_TIERS, tier_for
        _t = tier_for(float(row.get("call_mc") or 0))
        tp1, tp2, stop_frac = float(_t[1]), float(_t[2]), float(_t[3])
    except Exception:
        tp1, tp2, stop_frac = 2.00, 2.50, 0.25  # fallback mirrors live base tier
    stop_mult = 1 - stop_frac
    outcome = "OPEN"
    for ts, price, action in g:
        if ts < fill_ts:
            continue
        mult = price / fill
        if mult >= tp2:
            outcome = "TP2"; break
        if mult <= stop_mult:
            outcome = "STOP"; break
    row["outcome"] = outcome
    # pnl proxy from outcome — P10 (Oct 20) + 3rd audit 6.8: mirror the LIVE
    # ladder exactly. STOP pnl is stop_frac below entry (mult 1-stop_frac), not
    # (stop−1) = −85%: the old proxy booked every stop as an −82% catastrophe.
    # Ladder: 50% clips at tp1, rest exits at tp2, 3% fee on PROCEEDS (4th
    # audit 8.2 — (mult-1)*(1-fee) overstates; correct is mult*(1-fee)-1).
    FEE = 0.03
    if outcome == "TP2":
        clip = 0.5 * (tp1 * (1 - FEE) - 1.0)      # first half sold at +tp1
        rest = 0.5 * (tp2 * (1 - FEE) - 1.0)      # second half sold at +tp2
        row["pnl"] = round((clip + rest) * 100, 2)
    elif outcome == "STOP":
        row["pnl"] = round((stop_mult * (1 - FEE) - 1.0) * 100, 2)
    else:
        row["pnl"] = 0.0   # OPEN — no realized exit in window
    return row

def main():
    calls = [json.loads(l) for l in open(os.path.join(DESK, "calls.jsonl"))]
    import calendar
    w0 = calendar.timegm(time.strptime("2026/10/18 21:00", "%Y/%m/%d %H:%M"))
    w1 = calendar.timegm(time.strptime("2026/10/19 09:00", "%Y/%m/%d %H:%M"))
    win = [c for c in calls if w0 <= (c.get("ts") or 0) <= w1]
    # dedupe same mint+ts (multiple channels may repeat) — audit per unique call
    seen = set()
    uniq = []
    for c in win:
        key = (c.get("mint"), int(c.get("ts") or 0))
        if key in seen: continue
        seen.add(key)
        uniq.append(c)
    rows = []
    for c in uniq:
        try:
            r = audit_call(c)
            rows.append(r)
        except Exception as e:
            rows.append({"mint": c.get("mint"), "sym": c.get("symbol"), "chan": c.get("source"),
                          "outcome": "ERR:" + str(e)[:30]})
    json.dump(rows, open(os.path.join(DESK, "deep_audit_rows.json"), "w"))
    print(f"audited {len(rows)} unique calls -> deep_audit_rows.json")

if __name__ == "__main__":
    main()
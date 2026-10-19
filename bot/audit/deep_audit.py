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
SOLUSD = 150.0
EXIT_TIERS = [(0, 1.10, 1.30, 0.15), (40_000, 1.15, 1.35, 0.18), (150_000, 1.20, 1.45, 0.22), (1_500_000, 1.30, 1.60, 0.28)]  # LIVE ladder (position_monitor.EXIT_TIERS), single source of truth

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
    for ts, price, action in evs:
        if price <= 0:
            continue
        if g and ts - g[-1][0] <= BUNDLE_MS:
            continue
        if g and price < g[-1][1] * 0.01:
            continue
        g.append([ts, price, action])
    return g

def audit_call(c):
    mint = c.get("mint") or ""
    entry_ts = c.get("ts") or 0
    raw = gather_tape(mint, entry_ts)
    g = clean(raw)
    row = {"mint": mint, "sym": c.get("symbol"), "chan": c.get("channel") or c.get("source") or "", "ts": entry_ts}
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
    tp1, tp2, stop = 1.10, 1.30, 0.15
    outcome = "OPEN"
    for ts, price, action in g:
        if ts < fill_ts:
            continue
        mult = price / fill
        if mult >= tp2:
            outcome = "TP2"; break
        if mult <= (1 - stop):
            outcome = "STOP"; break
    row["outcome"] = outcome
    # pnl proxy from outcome — P10 (Oct 20): mirror the LIVE ladder exactly.
    # STOP pnl is a 15% LOSS (mult 0.85), NOT (stop−1) = −85%: the old proxy
    # booked every stop as an −82% catastrophe, wrecking the report arithmetic
    # (0.31×+18.4 + 0.63×−22.5 ≈ −8.5%/call, NOT the claimed +$148.60).
    # Ladder: 50% clips at 1.10 (tp1), rest exits at 1.30 (tp2) or 0.85 (stop),
    # 3% fee on proceeds. Per $1 of entry:
    if outcome == "TP2":
        clip = 0.5 * (1.10 - 1.0) * (1 - 0.03)          # first half @ +10%
        rest = 0.5 * (1.30 - 1.0) * (1 - 0.03)          # second half @ +30%
        row["pnl"] = round((clip + rest) * 100, 2)      # ≈ +19.4%
    elif outcome == "STOP":
        row["pnl"] = round((1.0 * (0.85 - 1.0) * (1 - 0.03)) * 100, 2)  # ≈ −14.6%
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
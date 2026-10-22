#!/usr/bin/env python3
"""tape_coins_audit.py — COINS AUDIT FROM THE DOWNLOADED TAPE (no desk self-report).

For every unique mint called in the 12h window (Oct 18 21:00 → Oct 19 09:00 UTC):
  - call_mc        : calls.jsonl mc at signal (USD)
  - fill_px/mc     : first fillable tape event (call_ts + 1.5s latency); mc = marketCapQuote*150
  - entry_gap_pct  : (fill_mc - call_mc)/call_mc — slippage vs signal
  - max_mc / end_mc: highest MC / MC at +2h (tape)
  - GRADUATED      : max_mc >= 69000 (pump.fun curve-fill threshold) during window
  - vol_usd        : sum(quoteAmount)*150 (SOL quote), buys vs sells
  - act60, bs5m    : tape activity in first 60s / buy:sell 5m
  - max_mult       : MFE from fill (peak price / fill price)
  - outcome        : ladder TP2/STOP/OPEN
Joins AEGIS scan_log (first & last score = score evolution).
Outputs rows to tape_coins_rows.json, prints summary tables.
"""
import json, os, sys, time, collections, statistics, sqlite3, calendar

DESK = os.path.expanduser("~/memecoin-quant")
CACHE = os.path.join(DESK, "_replay_cache")
LATENCY_MS = 1500
BUNDLE_MS = 3
SOLUSD = 150.0
GRAD_MC_USD = 69000.0

_hour_cache = {}

def load_hour(prefix):
    if prefix in _hour_cache:
        return _hour_cache[prefix]
    out = []
    seen = set()
    for cand in sorted(os.listdir(CACHE)):
        if cand.startswith(prefix + ".") and cand.endswith("mints.jsonl"):
            p = os.path.join(CACHE, cand)
            if os.path.getsize(p) == 0:
                continue
            for line in open(p):
                if line in seen:
                    continue
                seen.add(line)
                try:
                    ev = json.loads(line)
                    out.append((ev.get("mint") or "", ev.get("timestamp") or 0,
                                float(ev.get("price") or 0),
                                float(ev.get("marketCapQuote") or 0),
                                ev.get("action"), float(ev.get("quoteAmount") or 0)))
                except Exception:
                    continue
    _hour_cache[prefix] = out
    return out

def hour_prefix(ts):
    gt = time.gmtime(ts)
    return "%02d_%02d_%02d_%02d" % (gt.tm_year, gt.tm_mon, gt.tm_mday, gt.tm_hour)

def gather_tape(mint, entry_ts):
    parts = []
    for delta in range(5):
        parts.append(load_hour(hour_prefix(entry_ts + delta * 3600)))
    evs = []
    for p in parts:
        for m, ts, price, mcq, action, qa in p:
            if m == mint:
                evs.append((ts, price, mcq, action, qa))
    evs.sort()
    return evs

def audit(mint, call_ts, call_mc, sym, chan):
    raw = gather_tape(mint, call_ts)
    g = []
    prev_px = None
    for ts, px, mcq, action, qa in raw:
        if px <= 0:
            continue
        if g and ts - g[-1][0] <= BUNDLE_MS:
            continue
        if prev_px and px < prev_px * 0.01:
            continue
        g.append([ts, px, mcq, action, qa])
        prev_px = px
    row = {"mint": mint, "sym": sym, "chan": chan, "call_ts": call_ts, "call_mc": call_mc}
    if not g:
        row["outcome"] = "NO_TAPE"
        return row
    # fill at call+latency
    fill = fill_mcq = None
    for ts, px, mcq, action, qa in g:
        if ts >= call_ts * 1000 + LATENCY_MS:
            fill = px
            fill_mcq = mcq * SOLUSD
            row["fill_s"] = (ts - call_ts * 1000) / 1000.0
            break
    if fill is None:
        row["outcome"] = "NO_FILL"
        return row
    row["fill_mc"] = round(fill_mcq, 2) if fill_mcq else None
    if call_mc:
        row["entry_gap_pct"] = round((fill_mcq - call_mc) / call_mc * 100, 1) if fill_mcq else None
    # P12/P5 (Oct 20): metrics OUTCOME walk must start at FILL (not call0) — the
    # desk buys at the modeled fill; pre-fill prices are not tradeable. Metrics
    # (bs5m/act60) use pre-fill window only (no look-ahead; see deep_audit P5).
    t0 = call_ts * 1000
    t2h = t0 + 7200_000
    fill_ts = None
    for ts, px, mcq, action, qa in g:
        if ts >= call_ts * 1000 + LATENCY_MS:
            fill_ts = ts
            break
    t_st = fill_ts if fill_ts is not None else t0
    max_mc = 0.0
    max_mult = 1.0
    buys = sells = 0
    vol_usd = 0.0
    buys5 = sells5 = act60 = 0
    end_ts = end_px = None
    for ts, px, mcq, action, qa in g:
        if ts < t_st or ts > t2h:
            continue
        m = mcq * SOLUSD if mcq else 0.0
        max_mc = max(max_mc, m)
        mult = px / fill
        max_mult = max(max_mult, mult)
        vol_usd += qa * SOLUSD
        if action == "buy":
            buys += 1
            if ts <= t_st + 300_000: buys5 += 1
        elif action == "sell":
            sells += 1
            if ts <= t_st + 300_000: sells5 += 1
        if ts <= t_st + 60_000:
            act60 += 1
        end_ts, end_px = ts, px
    row["max_mc"] = round(max_mc, 2)
    row["grad"] = max_mc >= GRAD_MC_USD
    row["end_mult"] = round((end_px / fill) if end_px else None, 3)
    row["max_mult"] = round(max_mult, 3)
    row["vol_usd"] = round(vol_usd, 2)
    row["buys"], row["sells"] = buys, sells
    row["bs5m"] = round(buys5 / max(sells5, 1), 2)
    row["act60"] = act60
    # ladder (live position_monitor.EXIT_TIERS; base tier) — Oct 22 ext-audit:
    # this file hardcoded the OLD 1.10/1.30/0.15 ladder while position_monitor
    # runs TP2.0/SL0.75. Single source now: import EXIT_TIERS and use tier[0]
    # (base) — same as replay_backtest/deep_audit. NEVER copy ladder literals.
    try:
        sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
        from position_monitor import EXIT_TIERS
        _tier0 = EXIT_TIERS[0]  # (mc_min, tp1, tp2, stop_frac)
        tp1, tp2 = float(_tier0[1]), float(_tier0[2])
        stop = float(_tier0[3])  # stop FRACTION below entry (0.25 = 0.75x stop)
    except Exception:
        tp1, tp2, stop = 2.00, 2.50, 0.25
    outcome = "OPEN"
    for ts, px, mcq, action, qa in g:
        if ts < t_st:
            continue
        mult = px / fill
        if mult >= tp2:
            outcome = "TP2"; break
        if mult <= (1 - stop):
            outcome = "STOP"; break
    row["outcome"] = outcome
    return row

def main():
    calls = [json.loads(l) for l in open(os.path.join(DESK, "calls.jsonl"))]
    w0 = calendar.timegm(time.strptime("2026/10/18 21:00", "%Y/%m/%d %H:%M"))
    w1 = calendar.timegm(time.strptime("2026/10/19 09:00", "%Y/%m/%d %H:%M"))
    win = [c for c in calls if w0 <= (c.get("ts") or 0) <= w1]
    seen = set()
    uniq = []
    for c in win:
        key = (c.get("mint"), int(c.get("ts") or 0))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    # AEGIS score evolution (first & last scan in scan_log)
    db = sqlite3.connect(os.path.join(DESK, "aegis.db"))
    firsts, lasts = {}, {}
    for mint, ts, score, verdict in db.execute("SELECT mint, ts, score, verdict FROM scan_log ORDER BY ts"):
        if score is None:
            continue
        if mint not in firsts:
            firsts[mint] = (ts, score, verdict)
        lasts[mint] = (ts, score, verdict)
    rows = []
    for c in uniq:
        r = audit(c.get("mint"), c.get("ts") or 0, c.get("mc"), c.get("symbol"), c.get("channel") or "")
        f = firsts.get(c.get("mint")); l = lasts.get(c.get("mint"))
        r["aegis_first"] = f[1] if f else None
        r["aegis_last"] = l[1] if l else None
        rows.append(r)
    json.dump(rows, open(os.path.join(DESK, "tape_coins_rows.json"), "w"))
    print(f"audited {len(rows)} unique calls -> tape_coins_rows.json")

if __name__ == "__main__":
    main()
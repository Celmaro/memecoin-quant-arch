#!/usr/bin/env python3
"""replay_backtest.py — replay the PumpAPI historical tape against the desk's
exact entry/exit rules, using the real curated calls from calls.jsonl.

Usage (from ~/memecoin-quant, with the desk venv for zstandard):
  .venv/bin/python replay_backtest.py --hours 2026/10/19/05 2026/10/19/06
  .venv/bin/python replay_backtest.py --hours 2026/10/19/05 --mint <CA> --verbose

Pipeline:
  1. Load curated calls (calls.jsonl) whose post time falls inside the hour(s).
  2. Stream the replay archives (replay.pumpapi.io/...jsonl.zst) once per hour,
     keep events for the target mints (early-stop per mint via substring filter).
  3. Sort events by timestamp (ms). Group same-mint events within ~3ms as one
     Jito-bundle (can't mid-insert) — treat the FIRST price as the fill.
  4. Simulate entry at call_ts (+LATENCY buffer), then walk events forward:
     - hard stop: mult <= 1 - stop  -> sell ALL at that price
     - clip1: mult >= tp1           -> sell 50%, mark clip1 done
     - clip2: mult >= tp2           -> sell remaining, done
     - horizon: > 2h               -> sell at last price (day-trade horizon)
     Fees 3% on every sell (Doc1-1).
  5. Report per-mint: entry/exit mults, PnL, vs actual live outcome (paper book).
"""
import json, os, sys, time, collections, argparse, urllib.request, hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DESK = os.path.expanduser("~/memecoin-quant")
REPLAY = "https://replay.pumpapi.io/{}.jsonl.zst"
LATENCY_MS = 1500          # conservative execution latency buffer (PumpAPI guidance)
BUNDLE_MS = 3              # same-token events within 3ms = one Jito bundle
FEE_PCT = 3.0              # Doc1-1 3% paper fee per sell
MAX_HORIZON_S = 2 * 3600   # quick-flip horizon
# 3rd audit 6.4: single provenance-tagged SOL/USD fallback from desk_common —
# old literal 150.0 disagreed with pumpdata_feed's 104.0 init; wrong conversion
# can pick the wrong USD MC tier.
try:
    from desk_common import SOLUSD_FALLBACK as SOLUSD
except Exception:
    SOLUSD = 150.0
# Only these event fields are needed for replay — slimming cuts RAM ~10x (Oct 19 OOM fix)
SLIM_KEYS = ("mint", "timestamp", "price", "marketCapQuote", "action", "quoteAmount")


def _slim(ev):
    return {k: ev.get(k) for k in SLIM_KEYS}


def fetch_zst(path, mint_set, limit=8_000_000, reuse_any=False):
    """Stream .jsonl.zst, keep events whose mint is in mint_set. Bounded."""
    try:
        import zstandard
    except ImportError:
        sys.stderr.write("zstandard missing — run with: .venv/bin/python replay_backtest.py ...\n")
        return []
    url = REPLAY.format(path)
    cache_dir = os.path.join(DESK, "_replay_cache")
    # P7 (Oct 20): key by mint-SET hash, not len(mint_set). Two runs with different
    # 179-mint sets both produced ".179mints.jsonl" → the second run silently read
    # the FIRST file as if it were its own full tape (partial filtered cache).
    mint_key = hashlib.sha256(",".join(sorted(mint_set)).encode()).hexdigest()[:16]
    cache_file = os.path.join(cache_dir, path.replace("/", "_") + f".{mint_key}.jsonl")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            out = []
            for line in open(cache_file):
                try:
                    out.append(_slim(json.loads(line)))
                except Exception:
                    continue
            return out
        # Oct 19 (user "from the tape"): reuse ANY existing cache file for this
        # hour even if it was keyed by a different mint-count — avoids re-
        # downloading hours we already have on disk.
        # P7 (Oct 20): merge ALL per-hour cache files, not the first. Each old
        # file holds a DIFFERENT filtered slice (one mint set); taking one loses
        # the other mints' events (partial cache misread as full tape).
        if reuse_any:
            prefix = path.replace("/", "_")
            merged = {}
            for cand in sorted(os.listdir(cache_dir)):
                if cand.startswith(prefix + ".") and cand.endswith(".jsonl"):
                    candp = os.path.join(cache_dir, cand)
                    if os.path.getsize(candp) > 0:
                        for line in open(candp):
                            try:
                                e = _slim(json.loads(line))
                                # P7: dedupe by (mint, timestamp) — the same event
                                # can appear in overlapping cache slices; keep every
                                # DISTINCT event (the full price path, not one per
                                # mint — "last event wins" destroyed the tape).
                                merged[(e["mint"], e.get("timestamp"))] = e
                            except Exception:
                                continue
            out = sorted(merged.values(), key=lambda e: e.get("timestamp", 0) or 0)
            if out:
                return out
    except Exception:
        cache_file = None
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    out = []
    with urllib.request.urlopen(req, timeout=120) as r:
        dctx = zstandard.ZstdDecompressor()
        reader = dctx.stream_reader(r, read_size=1_048_576)
        buf = b""
        seen = set()
        while True:
            chunk = reader.read(1_048_576)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                # substring pre-filter on raw bytes (fast)
                hit = False
                for m in mint_set:
                    if m.encode() in line:
                        hit = True
                        break
                if not hit:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                m = ev.get("mint") or ""
                if m in mint_set:
                    out.append(_slim(ev))
                    if len(out) >= limit:
                        break
    try:
        if cache_file and out:
            with open(cache_file, "w") as cf:
                for ev in out:
                    cf.write(json.dumps(ev) + "\n")  # slimmed now (smaller caches)
    except Exception:
        pass
    return out


def replay_mint(call, events, verbose=False):
    """Simulate one call. Returns (pnl_pct, outcome, detail) or (None,...) if no tape."""
    mint = call["mint"]
    entry_ts = call.get("ts") or 0
    if not entry_ts:
        return None, "no_ts", ""
    evs = [e for e in events if e.get("mint") == mint]
    if not evs:
        return None, "no_tape", ""
    evs.sort(key=lambda e: e.get("timestamp") or 0)
    # dedupe Jito bundles: group consecutive same-mint events within BUNDLE_MS
    grouped = []
    _crash_dropped = 0  # 3rd audit 6.9: count (not silently drop) filtered crashes
    for e in evs:
        ts = e.get("timestamp") or 0
        if grouped and ts - grouped[-1]["ts"] <= BUNDLE_MS:
            continue  # same bundle — skip (can't mid-insert)
        price = e.get("price")
        try:
            price = float(price)
        except Exception:
            price = None
        if price is not None and price <= 0:
            continue  # zero/negative price = data glitch, not a fillable trade (Oct 19)
        # migration artifact guard: a price that collapses below 1% of the running
        # real price = bonding-curve finalize / curve-empty buy-out, not a trade.
        # 3rd audit 6.9: count (not just silently drop) so genuine rugs removed
        # by this filter are visible in the run's summary output.
        if price is not None and grouped and grouped[-1]["price"]:
            prev_real = grouped[-1]["price"]
            if price < prev_real * 0.01:
                _crash_dropped += 1
                CRASH_DROPPED["count"] += 1
                continue
        grouped.append({"ts": ts, "price": price, "mc": e.get("marketCapQuote")})
    # entry fill: first event AT/AFTER call_ts + latency
    fill_idx = None
    fill_px = None
    for i, g in enumerate(grouped):
        if g["ts"] >= (entry_ts * 1000) + LATENCY_MS:
            if g["price"]:
                fill_idx, fill_px = i, g["price"]
                break
    if fill_idx is None or fill_px is None:
        return None, "no_fill", ""
    # exit ladder (mirrors position_monitor.EXIT_TIERS + tier_exits)
    # 3rd audit 6.8: use the LIVE tier_for() helper, not a local copy of the loop.
    mc_entry = float(grouped[fill_idx].get("mc") or 0) * SOLUSD  # SOL -> USD
    tier = tier_for(mc_entry)
    _, tp1, tp2, stop = tier
    share = 1.0
    clip1_done = False
    pnl_acc = 0.0  # P6 (Oct 20): accumulate clip profit across the walk
    for g in grouped[fill_idx + 1:]:
        if g["price"] is None:
            continue
        mult = g["price"] / fill_px
        # hard stop
        if mult <= (1 - stop):
            # P6: fee on PROCEEDS (share*mult), not principal share; add prior clip profit
            pnl = pnl_acc + share * (mult - 1) * 100 - share * mult * FEE_PCT
            return pnl, "STOP", f"mult={mult:.2f} stop={stop}"
        # tp2 (sell remaining)
        if clip1_done and mult >= tp2:
            pnl = pnl_acc + share * (mult - 1) * 100 - share * mult * FEE_PCT
            return pnl, "TP2", f"mult={mult:.2f} tp2={tp2}"
        # tp1 (clip 50%)
        if not clip1_done and mult >= tp1:
            pnl_clip = 0.5 * (mult - 1) * 100 - 0.5 * mult * FEE_PCT
            pnl_acc += pnl_clip  # P6: carry the realized clip into later outcomes
            share = 0.5
            clip1_done = True
        # horizon
        if g["ts"] - (entry_ts * 1000) >= MAX_HORIZON_S * 1000:
            pnl = pnl_acc + share * (mult - 1) * 100 - share * mult * FEE_PCT
            return pnl, "HORIZON", f"mult={mult:.2f}"
    # no exit condition hit: value at last price
    last = grouped[-1]["price"]
    if last is None:
        return None, "no_last", ""
    mult = last / fill_px
    pnl = pnl_acc + share * (mult - 1) * 100 - share * mult * FEE_PCT
    return pnl, "OPEN", f"mult={mult:.2f}"


def load_live_outcome(mint):
    """Best-effort: compare against the paper book's realized PnL for that mint."""
    try:
        b = json.load(open(os.path.join(DESK, "paper_book.json")))
        trades = [t for t in (b.get("trades") or []) if (t.get("mint") or t.get("symbol") or "") in mint or t.get("mint") == mint]
        if trades:
            return round(sum(float(t.get("pnl") or 0) for t in trades), 2), len(trades)
        if mint in (b.get("positions") or {}):
            return "open", 0
    except Exception:
        pass
    return None, 0


# Oct 20 OFF tape retune — mirrors position_monitor.EXIT_TIERS (single source).
# Old ladder was stop-then-run suicide on this tape (65% dip <0.85 before 1.30);
# TP2.0/SL0.75 flips -3.9%/call -> +5.2%. No 1.10 clip.
# 3rd audit 6.8: import the LIVE ladder instead of keeping a copy that can drift.
try:
    sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
    from position_monitor import EXIT_TIERS, tier_for
except Exception:
    EXIT_TIERS = [
        (0,        2.00, 2.50, 0.25),
        (40_000,   2.00, 2.50, 0.25),
        (150_000,  2.20, 2.80, 0.25),
        (1_500_000,2.50, 3.00, 0.25),
    ]
    def tier_for(mc):
        t = EXIT_TIERS[0]
        for cand in EXIT_TIERS:
            if mc >= cand[0]:
                t = cand
            else:
                break
        return t

# 3rd audit 6.9: module-level crash-filter sink so run summaries show how many
# >99% events were dropped as suspected curve-finalize artifacts (genuine rugs
# hidden by the filter become visible).
CRASH_DROPPED = {"count": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", nargs="+", required=True, help="UTC hour paths e.g. 2026/10/19/05")
    ap.add_argument("--reuse-any", action="store_true", help="reuse ANY existing per-hour cache file (even different mint-count) instead of downloading")
    ap.add_argument("--mint", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # calls in these hours
    calls = []
    try:
        for line in open(os.path.join(DESK, "calls.jsonl")):
            try:
                c = json.loads(line)
            except Exception:
                continue
            if not c.get("mint"):
                continue
            ts = c.get("ts") or 0
            if not ts:
                continue
            h = time.gmtime(ts)
            key = f"{h.tm_year:04d}/{h.tm_mon:02d}/{h.tm_mday:02d}/{h.tm_hour:02d}"
            if key in args.hours and (not args.mint or args.mint in c["mint"]):
                calls.append(c)
    except FileNotFoundError:
        pass

    # dedupe by mint (first sighting in window)
    seen = {}
    for c in calls:
        seen.setdefault(c["mint"], c)
    calls = list(seen.values())
    print(f"calls in {args.hours}: {len(calls)} unique mints")

    mint_set = {c["mint"] for c in calls}
    if not mint_set:
        return 0

    # OOM fix (Oct 19): process ONE hour at a time with a 1-hour lookahead so a
    # 12h run never holds all hours in RAM. Slim events cut per-hour cost ~10x.
    import gc
    calls_by_hour = collections.defaultdict(list)
    for c in calls:
        h = time.gmtime(c["ts"])
        calls_by_hour[f"{h.tm_year:04d}/{h.tm_mon:02d}/{h.tm_mday:02d}/{h.tm_hour:02d}"].append(c)

    results = []
    hours_sorted = sorted(args.hours)
    for i, hour in enumerate(hours_sorted):
        print(f"fetching {hour} ...", flush=True)
        events = fetch_zst(hour, mint_set, reuse_any=args.reuse_any)
        # lookahead: next hour's tape for exits crossing the hour boundary
        if i + 1 < len(hours_sorted):
            nxt = hours_sorted[i + 1]
            print(f"fetching {nxt} (lookahead) ...", flush=True)
            events.extend(fetch_zst(nxt, mint_set, reuse_any=args.reuse_any))
        print(f"  {len(events)} events for {len(calls_by_hour.get(hour, []))} calls in {hour}", flush=True)
        for c in calls_by_hour.get(hour, []):
            pnl, outcome, detail = replay_mint(c, events, verbose=args.verbose)
            live, ntrades = load_live_outcome(c["mint"])
            results.append((c, pnl, outcome, detail, live, ntrades))
        del events
        gc.collect()

    print(f"\n=== REPLAY RESULTS ({len(results)} calls) ===")
    print(f"{'SYM':10s} {'outcome':9s} {'pnl%':>8s} {'live':>8s} {'detail':22s} chan")
    tot = 0; n = 0
    for c, pnl, outcome, detail, live, ntrades in results:
        sym = (c.get("symbol") or c["mint"][:6])[:10]
        p = "n/a" if pnl is None else f"{pnl:+.1f}%"
        lv = "n/a" if live is None else f"${live}"
        print(f"{sym:10s} {str(outcome):9s} {p:>8s} {lv:>8s} {detail:22s} {c.get('channel','')[:22]}")
        if pnl is not None:
            tot += pnl; n += 1
    if n:
        print(f"\nAVG PnL per call (with tape): {tot/n:+.1f}%  (n={n})")
    if CRASH_DROPPED["count"]:
        print(f"[3rd audit 6.9] {CRASH_DROPPED['count']} events >99% below prior price were filtered "
              f"as suspected curve-finalize artifacts — genuine rugs hidden by this filter are counted here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

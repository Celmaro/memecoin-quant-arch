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
import json, os, sys, time, collections, argparse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DESK = os.path.expanduser("~/memecoin-quant")
REPLAY = "https://replay.pumpapi.io/{}.jsonl.zst"
LATENCY_MS = 1500          # conservative execution latency buffer (PumpAPI guidance)
BUNDLE_MS = 3              # same-token events within 3ms = one Jito bundle
FEE_PCT = 3.0              # Doc1-1 3% paper fee per sell
MAX_HORIZON_S = 2 * 3600   # quick-flip horizon


def fetch_zst(path, mint_set, limit=8_000_000):
    """Stream .jsonl.zst, keep events whose mint is in mint_set. Bounded."""
    try:
        import zstandard
    except ImportError:
        sys.stderr.write("zstandard missing — run with: .venv/bin/python replay_backtest.py ...\n")
        return []
    url = REPLAY.format(path)
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
                    out.append(ev)
                    seen.add(m)
                    if len(out) >= limit:
                        return out
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
    for e in evs:
        ts = e.get("timestamp") or 0
        if grouped and ts - grouped[-1]["ts"] <= BUNDLE_MS:
            continue  # same bundle — skip (can't mid-insert)
        price = e.get("price")
        try:
            price = float(price)
        except Exception:
            price = None
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
    mc_entry = float(grouped[fill_idx].get("mc") or 0)
    tier = EXIT_TIERS[0]
    for cand in EXIT_TIERS:
        if mc_entry >= cand[0]:
            tier = cand
        else:
            break
    _, tp1, tp2, stop = tier
    share = 1.0
    clip1_done = False
    for g in grouped[fill_idx + 1:]:
        if g["price"] is None:
            continue
        mult = g["price"] / fill_px
        # hard stop
        if mult <= (1 - stop):
            pnl = share * (mult - 1) * 100 - share * FEE_PCT
            return pnl, "STOP", f"mult={mult:.2f} stop={stop}"
        # tp2 (sell remaining)
        if clip1_done and mult >= tp2:
            pnl = share * (mult - 1) * 100 - share * FEE_PCT
            return pnl, "TP2", f"mult={mult:.2f} tp2={tp2}"
        # tp1 (clip 50%)
        if not clip1_done and mult >= tp1:
            pnl_clip = 0.5 * (mult - 1) * 100 - 0.5 * FEE_PCT
            share = 0.5
            clip1_done = True
        # horizon
        if g["ts"] - (entry_ts * 1000) >= MAX_HORIZON_S * 1000:
            pnl = share * (mult - 1) * 100 - share * FEE_PCT
            return pnl, "HORIZON", f"mult={mult:.2f}"
    # no exit condition hit: value at last price
    last = grouped[-1]["price"]
    if last is None:
        return None, "no_last", ""
    mult = last / fill_px
    pnl = share * (mult - 1) * 100 - share * FEE_PCT
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


EXIT_TIERS = [
    (0,        1.10, 1.30, 0.15),
    (40_000,   1.15, 1.35, 0.18),
    (150_000,  1.20, 1.45, 0.22),
    (1_500_000,1.30, 1.60, 0.28),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", nargs="+", required=True, help="UTC hour paths e.g. 2026/10/19/05")
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

    # fetch archives (early-stop per hour; may still be large)
    events = []
    for hour in args.hours:
        print(f"fetching {hour} ...", flush=True)
        events.extend(fetch_zst(hour, mint_set))
    print(f"loaded {len(events)} events for {len(mint_set)} mints")

    results = []
    for c in calls:
        pnl, outcome, detail = replay_mint(c, events, verbose=args.verbose)
        live, ntrades = load_live_outcome(c["mint"])
        results.append((c, pnl, outcome, detail, live, ntrades))

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
    return 0


if __name__ == "__main__":
    sys.exit(main())

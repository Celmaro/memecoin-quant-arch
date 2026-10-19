#!/usr/bin/env python3
"""exit_verify.py — check every paper exit against the on-chain tape.

For each completed paper trade (from all paper_book*.json files), stream the
PumpAPI replay archive for the exit hour + the following 2 hours, and answer:
  - did the tape reach >=1.5x ABOVE our exit price within 2h after we sold?
  - did it stay there (durable pump) or spike and die (endX < 1.1)?
Baseline = robust exit price (median of first 5 post-exit trades, not the raw
field — micro-price artifacts like INTELLIGENCE's fake 2.34x are filtered via
p99 max and median-of-5 baseline).

Run with the desk venv (zstandard needed):
  .venv/bin/python exit_verify.py [--hours 2026/10/19/06 ...] [--out exit_verify_report.txt]
If --hours is omitted, derives needed hours from the exit timestamps.
"""
import json, os, sys, time, glob, statistics, argparse, collections, calendar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DESK = os.path.expanduser("~/memecoin-quant")
REPLAY = "https://replay.pumpapi.io/{}.jsonl.zst"
SOLUSD = 150.0

def load_exits():
    """All completed paper trades across every book file."""
    books = ['paper_book.json'] + sorted(glob.glob(os.path.join(DESK, 'paper_book_LOST_v*.json')))
    exits = {}
    for bf in books:
        try:
            b = json.load(open(bf))
        except Exception:
            continue
        for t in (b.get('trades') or []):
            if t.get('pnl') is None or not t.get('mint') or not t.get('ts'):
                continue
            exits.setdefault(t['mint'], dict(ts=t['ts'], pnl=t.get('pnl'), sym=t.get('symbol'),
                                             how=t.get('how'), exit_px=t.get('exit_px'),
                                             entry_px=t.get('entry_px')))
    return exits

def stream_hour(path, mint_set):
    """Stream one replay hour, return slim events for the wanted mints."""
    try:
        import zstandard
    except ImportError:
        sys.stderr.write("zstandard missing — run with .venv/bin/python\n")
        return []
    url = REPLAY.format(path)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    out = []
    with urllib.request.urlopen(req, timeout=180) as r:
        dctx = zstandard.ZstdDecompressor()
        reader = dctx.stream_reader(r, read_size=1_048_576)
        buf = b""
        while True:
            chunk = reader.read(1_048_576)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                hit = any(m.encode() in line for m in mint_set)
                if not hit:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                m = ev.get("mint") or ""
                if m in mint_set:
                    p = ev.get("price")
                    try:
                        p = float(p)
                    except Exception:
                        p = None
                    if p is None or p <= 0:
                        continue
                    out.append((ev.get("timestamp") or 0, p, ev.get("action")))
    return out

def robust_exit_price(after):
    """Median of first 5 real post-exit trades — immune to a single micro-price."""
    if not after:
        return None
    seed = [p for _, p, _ in after[:5]]
    if not seed:
        return None
    return statistics.median(seed)

def verify(exits, hours, verbose=False):
    """Stream hours; for each exit, check max/end multiple in the 2h after exit."""
    # bucket exits by their start hour
    by_hour = collections.defaultdict(list)
    for mint, r in exits.items():
        h = time.gmtime(r['ts'])
        by_hour[f"{h.tm_year:04d}/{h.tm_mon:02d}/{h.tm_mday:02d}/{h.tm_hour:02d}"].append((mint, r))
    hours = sorted(set(hours) | set(by_hour.keys()))
    # tape per exit: we accumulate after-exit samples from the exit hour + next 2h
    # simpler: stream each hour once, attach events to exits whose window covers it
    tape = collections.defaultdict(list)  # mint -> [(ts, price, action)]
    for hour in hours:
        mints_needed = set()
        for mint, r in exits.items():
            if hour.startswith(time.strftime('%Y/%m/%d', time.gmtime(r['ts']))):  # same day
                mints_needed.add(mint)
        if not mints_needed:
            continue
        print(f"fetching {hour} [{len(mints_needed)} mints] ...", flush=True)
        for ev in stream_hour(hour, mints_needed):
            mint = None  # stream_hour returns tuples; find matching mint
            # we need mint per event — adjust: stream_hour should return mint too
    return tape

# Redo stream_hour to keep mint (needed for bucketing)
CACHE_DIR = os.path.join(DESK, "_replay_cache")

def stream_hour2(path, mint_set):
    # Reuse local per-hour caches when present (slim reads — Oct 19)
    import glob as _glob
    for cf in _glob.glob(os.path.join(CACHE_DIR, path.replace("/", "_") + ".*.jsonl")):
        try:
            out = collections.defaultdict(list)
            for line in open(cf):
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                m = ev.get("mint") or ""
                if m in mint_set:
                    try:
                        p = float(ev.get("price") or 0)
                    except Exception:
                        continue
                    if p <= 0:
                        continue
                    out[m].append((ev.get("timestamp") or 0, p, ev.get("action")))
            if out:
                return out
        except Exception:
            continue
    try:
        import zstandard
    except ImportError:
        return []
    import urllib.request
    url = REPLAY.format(path)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    out = collections.defaultdict(list)
    with urllib.request.urlopen(req, timeout=180) as r:
        dctx = zstandard.ZstdDecompressor()
        reader = dctx.stream_reader(r, read_size=1_048_576)
        buf = b""
        while True:
            chunk = reader.read(1_048_576)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                if not any(m.encode() in line for m in mint_set):
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                m = ev.get("mint") or ""
                if m not in mint_set:
                    continue
                try:
                    p = float(ev.get("price") or 0)
                except Exception:
                    continue
                if p <= 0:
                    continue
                out[m].append((ev.get("timestamp") or 0, p, ev.get("action")))
    return out

def run(exits, hours, out_path=None, verbose=False):
    results = []
    if hours:
        # Explicit --hours: STRICT restriction (smoke tests, cached windows only)
        need = set(hours)
    else:
        # Auto: for every exit, stream exit hour + 2h lookahead
        need = set()
        for mint, r in exits.items():
            for dh in (0, 1, 2):
                t2 = time.gmtime(r['ts'] + dh * 3600)
                need.add(f"{t2.tm_year:04d}/{t2.tm_mon:02d}/{t2.tm_mday:02d}/{t2.tm_hour:02d}")
    for hour in sorted(need):
        # mints whose exit + 2h window OVERLAPS this hour
        wanted = set()
        h_start_ms = calendar.timegm(time.strptime(hour, '%Y/%m/%d/%H')) * 1000
        h_end_ms = h_start_ms + 3600_000
        for mint, r in exits.items():
            exit_ms = int(r['ts'] * 1000)
            end_ms = exit_ms + 2 * 3600_000
            # overlap: exit window [exit_ms, end_ms] intersects [h_start, h_end]
            if exit_ms < h_end_ms and h_start_ms < end_ms:
                wanted.add(mint)
        if not wanted:
            continue
        print(f"fetching {hour} [{len(wanted)} mints] ...", flush=True)
        hourly = stream_hour2(hour, wanted)
        for mint, evs in hourly.items():
            results.append((mint, hour, evs))
    # per-exit analyze
    detail = []
    for mint, r in exits.items():
        exit_ms = int(r['ts'] * 1000)
        end_ms = exit_ms + 2 * 3600_000
        after = []
        for mint2, hour, evs in results:
            if mint2 == mint:
                after.extend(e for e in evs if e[0] >= exit_ms and e[0] <= end_ms)
        if not after:
            detail.append((mint, r, None, None, None))
            continue
        after.sort(key=lambda e: e[0])
        base = robust_exit_price(after)
        if not base or base <= 0:
            detail.append((mint, r, None, None, None))
            continue
        ps = sorted(p for _, p, _ in after)
        p99 = ps[int(len(ps) * 0.99)] if ps else base
        maxX = max(p99 / base, 1.0)
        endX = after[-1][1] / base
        detail.append((mint, r, maxX, endX, len(after)))
    # report
    lines = []
    lines.append("=== EXIT VERIFICATION (paper exits vs on-chain tape) ===")
    lines.append(f"{'SYM':10s}{'exit':>6s}{'pnl':>8s}{'maxX':>7s}{'endX':>7s}  verdict")
    durable = spike = 0
    for mint, r, maxX, endX, n in sorted(detail, key=lambda d: d[1]['ts']):
        sym = (r['sym'] or mint[:6])[:10]
        hm = time.strftime('%m-%d %H:%M', time.gmtime(r['ts']))
        if maxX is None:
            lines.append(f"{sym:10s}{hm:>12s}{r['pnl']:>8.2f} {'n/a':>6s}{'n/a':>7s}  no tape")
            continue
        if maxX >= 1.5 and endX >= 1.1:
            verdict = "⚡ DURABLE PUMP MISSED"
            durable += 1
        elif maxX >= 1.5:
            verdict = "spiked then died"
            spike += 1
        elif maxX >= 1.25:
            verdict = "near-miss spike"
        else:
            verdict = "no pump"
        lines.append(f"{sym:10s}{hm:>12s}{r['pnl']:>8.2f}{maxX:>7.2f}{endX:>7.2f}  {verdict}")
    lines.append("")
    lines.append(f"TOTAL verified exits: {sum(1 for d in detail if d[2] is not None)}/{len(detail)}")
    lines.append(f"DURABLE pumps missed (maxX>=1.5 and endX>=1.1): {durable}")
    lines.append(f"Spikes (maxX>=1.5 but ended <1.1x): {spike}")
    text = "\n".join(lines)
    print(text)
    if out_path:
        with open(out_path, 'w') as f:
            f.write(text + "\n")
    return durable, spike

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", nargs="+", default=[])
    ap.add_argument("--out", default="exit_verify_report.txt")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    exits = load_exits()
    print(f"loaded {len(exits)} paper exits")
    durable, spike = run(exits, args.hours, out_path=args.out, verbose=args.verbose)
    if durable > 0 or spike > 0:
        print(f"FOUND {durable} durable + {spike} spike premature exits — see {args.out}")
    else:
        print("No premature exits found in verified set")
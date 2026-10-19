#!/usr/bin/env python3
"""unsellable_verify.py — for every UNSELLABLE total-loss trade in the paper
books, show what the on-chain tape actually did at exit time. Proves (or
disproves) the user's complaint: "paper pretended it was dumping but the coin
did not dump that much."

Reads whatever _replay_cache hours exist; fetches missing hours (08/09/10)
for the specific mints. Output: per trade — paper mult/pnl vs tape price
activity + price at exit + tape max multiple over the hour.
"""
import json, os, sys, time, glob, collections, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DESK = os.path.expanduser("~/memecoin-quant")

BOOKS = ['paper_book.json'] + sorted(glob.glob(os.path.join(DESK, 'paper_book_LOST_v*.json')))
SOLUSD = 150.0

def load_unsellable():
    out = []
    for bf in BOOKS:
        try:
            b = json.load(open(bf))
        except Exception:
            continue
        for t in (b.get('trades') or []):
            if t.get('pnl') is None:
                continue
            if t.get('illiq') or 'UNSELLABLE' in str(t.get('reason', '')):
                out.append((bf, t))
    return out

def stream_hour(hour, mints):
    try:
        import zstandard
    except ImportError:
        return collections.defaultdict(list)
    url = f"https://replay.pumpapi.io/{hour}.jsonl.zst"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    out = collections.defaultdict(list)
    with urllib.request.urlopen(req, timeout=240) as r:
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
                if not any(m.encode() in line for m in mints):
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                m = ev.get("mint") or ""
                if m in mints:
                    try:
                        p = float(ev.get("price") or 0)
                    except Exception:
                        continue
                    if p > 0:
                        out[m].append((ev.get("timestamp") or 0, p, ev.get("action")))
    return out

def load_cached(mint):
    """Pull tape for a mint from any existing per-hour cache file."""
    out = []
    for cf in glob.glob(os.path.join(DESK, '_replay_cache', '*.jsonl')):
        for line in open(cf):
            if mint not in line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("mint") == mint:
                try:
                    p = float(ev.get("price") or 0)
                except Exception:
                    continue
                if p > 0:
                    out.append((ev.get("timestamp") or 0, p, ev.get("action")))
    return out

def main():
    uns = load_unsellable()
    print(f"UNSELLABLE trades: {len(uns)}\n")
    # mints + their exit hours
    need_hours = set()
    for bf, t in uns:
        h = time.gmtime(t['ts'])
        need_hours.add(f"{h.tm_year:04d}/{h.tm_mon:02d}/{h.tm_mday:02d}/{h.tm_hour:02d}")
    print(f"hours needed: {sorted(need_hours)}")

    for bf, t in sorted(uns, key=lambda x: x[1]['ts']):
        mint = t['mint']
        exit_ts = t['ts']
        sym = str(t.get('symbol'))[:16]
        mult = t.get('mult')
        pnl = t.get('pnl')
        liq = t.get('liq_at_exit')
        tape = load_cached(mint)
        # also fetch the exit hour if not cached
        h = time.gmtime(exit_ts)
        hour = f"{h.tm_year:04d}/{h.tm_mon:02d}/{h.tm_mday:02d}/{h.tm_hour:02d}"
        if not tape:
            print(f"fetching {hour} for {sym} ...", flush=True)
            got = stream_hour(hour, {mint})
            tape = got.get(mint, [])
        tape.sort()
        exit_ms = int(exit_ts * 1000)
        near = [(ts, p) for ts, p, _ in tape if abs(ts - exit_ms) < 300_000]
        print(f"\n{sym}  exit {time.strftime('%m-%d %H:%M:%S', time.gmtime(exit_ts))}")
        print(f"  paper: mult={mult} pnl=${pnl:.2f} liq_at_exit={liq} reason={t.get('reason')}")
        if not tape:
            print(f"  TAPE: NO EVENTS AT ALL for this mint")
            continue
        lo_t, hi_t = min(tape, key=lambda x: x[1])[1], max(tape, key=lambda x: x[1])[1]
        print(f"  TAPE: {len(tape)} events | price range {lo_t:.3e}..{hi_t:.3e} SOL (${lo_t*SOLUSD:.2f}..${hi_t*SOLUSD:.2f})")
        if near:
            prices = sorted(p for _, p in near)
            med = prices[len(prices)//2]
            print(f"  TAPE at exit±5m: {len(prices)} trades, median px {med:.4e} SOL = ${med*SOLUSD:.6f}")
            print(f"      range {min(prices):.4e}..{max(prices):.4e} SOL")
            # how far is exit price from the hour's peak? (did it dump or was it alive)
            if hi_t > 0:
                print(f"      exit-median vs hour-peak: {med/hi_t*100:.1f}% of peak — coin {'was LIVE/near peak' if med/hi_t > 0.5 else 'had dumped from peak'}")
        else:
            print(f"  TAPE: no trades within ±5m of exit")

if __name__ == "__main__":
    main()
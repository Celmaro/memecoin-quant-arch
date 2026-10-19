#!/usr/bin/env python3
"""PumpAPI replay backfill (Tier 2.3) — free hourly event archives for wash/LPI studies.

Usage:
  python3 replay_backfill.py 2026/04/18  [--mint <CA>] [--limit 50] [--top]
  python3 replay_backfill.py 2026/04/18/01 [--mint <CA>]

Fetches https://replay.pumpapi.io/YEAR/MONTH/DAY/HOUR.jsonl.zst (or DAY for all hours),
decompresses (zstandard via venv: .venv/bin/python), and prints/aggregates events.
"""
import json, os, sys, urllib.request

REPLAY = "https://replay.pumpapi.io/{}.jsonl.zst"
MAX_EVENTS = 200_000  # bounded sample; a full hour can be millions of events

def fetch_zst(path, mint=None, limit=MAX_EVENTS):
    """Stream-decompress a .jsonl.zst archive, early-stop once enough events collected.
    Cheap substring pre-filter on raw lines when mint is given (avoids json parse on non-matches)."""
    try:
        import zstandard
    except ImportError:
        sys.stderr.write("zstandard missing — run with the desk venv: .venv/bin/python replay_backfill.py ...\n")
        return []
    url = REPLAY.format(path)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    events = []
    with urllib.request.urlopen(req, timeout=90) as r:
        dctx = zstandard.ZstdDecompressor()
        reader = dctx.stream_reader(r, read_size=65_536)
        buf = b""
        while True:
            chunk = reader.read(1_048_576)  # 1MB chunks
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                if mint and mint.lower().encode() not in line.lower():
                    continue  # cheap pre-filter, no parse
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
                if len(events) >= limit:
                    return events
    return events


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    path = sys.argv[1].rstrip("/")
    args = sys.argv[2:]
    mint = None
    limit = 50
    top = False
    for i, a in enumerate(args):
        if a == "--mint" and i + 1 < len(args): mint = args[i+1]
        elif a == "--limit" and i + 1 < len(args): limit = int(args[i+1])
        elif a == "--top": top = True

    # DAY path -> fetch each hour 00..23 (streamed, early-stop per hour)
    parts = path.split("/")
    if len(parts) == 3:
        events = []
        for h in range(24):
            events.extend(fetch_zst(f"{path}/{h:02d}", mint=mint, limit=limit))
            if len(events) >= limit:
                break
    else:
        events = fetch_zst(path, mint=mint, limit=limit)
    print(f"loaded {len(events)} events from replay.pumpapi.io/{path}")

    if mint:
        events = [e for e in events if mint.lower() in json.dumps(e).lower()]
        print(f"filtered to {len(events)} events for {mint[:12]}...")
    if not events:
        return 0
    if top:
        # aggregate by mint: volume, buys/sells — for wash/LPI scanning
        agg = {}
        for e in events:
            m = e.get("mint") or (e.get("token") or {}).get("mint") or "?"
            typ = (e.get("txType") or e.get("type") or "?").lower()
            amt = float(e.get("solAmount") or e.get("amount") or 0)
            d = agg.setdefault(m, {"buys": 0, "sells": 0, "vol": 0.0})
            d["vol"] += amt
            if "sell" in typ: d["sells"] += 1
            else: d["buys"] += 1
        topm = sorted(agg.items(), key=lambda x: -x[1]["vol"])[:limit]
        print(f"\n=== TOP {len(topm)} by SOL volume (sample) ===")
        for m, d in topm:
            print(f"  {m[:16]}... buys={d['buys']} sells={d['sells']} vol={d['vol']:.1f} SOL")
    else:
        for e in events[:limit]:
            print(json.dumps({k: e.get(k) for k in ("mint","type","txType","solAmount","amount","marketCapSol","timestamp") if k in e}, default=str)[:200])
    return 0

if __name__ == "__main__":
    sys.exit(main())
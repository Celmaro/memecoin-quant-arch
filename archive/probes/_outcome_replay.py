#!/usr/bin/env python3
"""Outcome replay for the channel-edge audit: per call, fetch pool OHLCV around call_ts,
compute peak-by-45m / peak-by-2h / close-by-45m multiples vs call price."""
import sqlite3, json, time, urllib.request, statistics, collections, sys

BASE = '/home/agentuser/memecoin-quant'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36'

def http(url, retries=4):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == retries - 1:
                return None
            time.sleep(2.5 * (i + 1))

def get_pair(mint):
    d = http(f'https://api.dexscreener.com/latest/dex/tokens/{mint}')
    if not d:
        return None
    pairs = d.get('pairs') or []
    if not pairs:
        return None
    p = max(pairs, key=lambda p: (p.get('liquidity') or {}).get('usd') or 0)
    return p.get('pairAddress')

def get_ohlcv(pair, before):
    d = http(f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/ohlcv/minute?aggregate=1&limit=120&before_timestamp={int(before)}')
    if not d:
        return None
    return (d.get('data') or {}).get('attributes', {}).get('ohlcv_list') or []

def main():
    now = time.time()
    con = sqlite3.connect(BASE + '/aegis.db')
    con.row_factory = sqlite3.Row
    cms = [dict(r) for r in con.execute(
        'select * from call_metrics where ?-call_ts between 3600*2 and 3600*24 order by call_ts desc', (now,)).fetchall()]
    con.close()
    # take up to 50, spread across channels evenly-ish (sort by ts, take 50)
    cms = cms[:20]
    print(f'target calls: {len(cms)}', file=sys.stderr)
    res = []
    for c in cms:
        mint = c['mint']; call_ts = c['call_ts']; call_px = c.get('price') or 0
        if not call_px:
            continue
        pair = get_pair(mint); time.sleep(1.0)
        if not pair:
            continue
        # window rows: from call_ts-60 up to call_ts+45m, call_ts+2h
        rows45 = get_ohlcv(pair, call_ts + 2700 + 60) or []
        rows2h = get_ohlcv(pair, call_ts + 7200 + 60) or []
        time.sleep(1.0)
        # rows are descending; keep rows with ts >= call_ts-30 (i.e. at/after call)
        def peak(rows, until):
            h = None
            for r in rows:
                ts, o, hi, lo, cl, vol = r
                if ts > until:
                    continue
                if ts < call_ts - 30:
                    break
                if hi and (h is None or hi > h):
                    h = hi
            return h
        def close_at(rows, until):
            for r in rows:
                ts, o, hi, lo, cl, vol = r
                if ts <= until and ts >= call_ts - 30:
                    return cl
            return None
        pk45 = peak(rows45, call_ts + 2700)
        pk2h = peak(rows2h, call_ts + 7200)
        cl45 = close_at(rows45, call_ts + 2700)
        res.append({
            'symbol': c.get('symbol'), 'channel': c.get('channel'), 'score': c.get('aegis_score'),
            'call_ts': call_ts, 'call_px': call_px,
            'pk45': (pk45 / call_px) if pk45 else None,
            'pk2h': (pk2h / call_px) if pk2h else None,
            'cl45': (cl45 / call_px) if cl45 else None,
        })
    print(f'resolved: {len(res)}', file=sys.stderr)
    def rate(xs, fn):
        vals = [fn(x) for x in xs if x is not None]
        return round(100 * sum(1 for v in vals if v) / max(1, len(vals)))
    ok = res
    print(f'\n== ALL ({len(ok)}) ==')
    print(f'peak+45m >=1.3x: {rate([r for r in ok if r["pk45"] is not None], lambda r: r["pk45"] >= 1.3)}%')
    print(f'peak+45m >=1.15x: {rate([r for r in ok if r["pk45"] is not None], lambda r: r["pk45"] >= 1.15)}%')
    print(f'peak+2h >=1.3x: {rate([r for r in ok if r["pk2h"] is not None], lambda r: r["pk2h"] >= 1.3)}%')
    print(f'close+45m >=1.0x: {rate([r for r in ok if r["cl45"] is not None], lambda r: r["cl45"] >= 1.0)}%')
    print(f'close+45m <0.7x: {rate([r for r in ok if r["cl45"] is not None], lambda r: r["cl45"] < 0.7)}%')
    print(f'median pk45: {statistics.median(r["pk45"] for r in ok if r["pk45"]) if any(r["pk45"] for r in ok) else None}')
    print(f'\n== BY CHANNEL ==')
    for ch in sorted(set(r['channel'] for r in ok)):
        g = [r for r in ok if r['channel'] == ch]
        if not g:
            continue
        p45 = [r['pk45'] for r in g if r['pk45']]
        med = statistics.median(p45) if p45 else 0
        print(f"{ch}: n={len(g)} pk45med={med:.2f} "
              f"pk45win={rate([r for r in g if r['pk45'] is not None], lambda r: r['pk45'] >= 1.3)}% cl45win={rate([r for r in g if r['cl45'] is not None], lambda r: (r['cl45'] or 0) >= 1.0)}% "
              f"cl45loss={rate([r for r in g if r['cl45'] is not None], lambda r: (r['cl45'] or 0) < 0.7)}%")
    print(f'\n== BY AEGIS ==')
    for b, lo, hi in [('0-29', 0, 30), ('30-49', 30, 50), ('50+', 50, 200)]:
        g = [r for r in ok if (r.get('score') or 0) >= lo and (r.get('score') or 0) < hi]
        if not g:
            continue
        print(f"AEGIS {b}: n={len(g)} pk45win={rate([r for r in g if r['pk45'] is not None], lambda r: r['pk45'] >= 1.3)}% cl45win={rate([r for r in g if r['cl45'] is not None], lambda r: (r['cl45'] or 0) >= 1.0)}%")
    json.dump({'res': res}, open(BASE + '/_outcome_replay.json', 'w'), indent=1)

if __name__ == '__main__':
    main()

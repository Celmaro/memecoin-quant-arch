import sys, json, time
sys.path.insert(0, '/home/agentuser/memecoin-quant')
from desk_common import dex_pair, db
con = db()
cols = [r[1] for r in con.execute("PRAGMA table_info(call_metrics)")]
sel = "mint, symbol, channel, call_ts, mc" + (", aegis_score" if "aegis_score" in cols else "") + (", verdict" if "verdict" in cols else "")
recent = con.execute(f"select {sel} from call_metrics order by call_ts desc limit 12").fetchall()
now = time.time()
for r in recent:
    age = (now - r['call_ts']) / 60
    p = dex_pair(r['mint']) or {}
    mc = p.get('marketCap') or 0
    chg = (p.get('priceChange') or {})
    liq = ((p.get('liquidity') or {}).get('usd'))
    sc = r['aegis_score'] if 'aegis_score' in r.keys() else '?'
    vd = r['verdict'] if 'verdict' in r.keys() else '?'
    print(f"{r['symbol'] or '?':10s} {age:5.1f}m ago {r['channel'][:14]:14s} aegis={sc} {str(vd):8s} callMC={r['mc'] or 0:>10,.0f} liveMC={mc:>10,.0f} 5m={chg.get('m5')}% liq={liq}")
con.close()

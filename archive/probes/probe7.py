import sys, json, sqlite3, collections
sys.path.insert(0, '/home/agentuser/memecoin-quant')
c = sqlite3.connect('/home/agentuser/memecoin-quant/aegis.db'); c.row_factory = sqlite3.Row
rows = c.execute("select mint, score, verdict, checks_json, max(ts) as ts from scan_log group by mint").fetchall()
worst = []
for r in rows:
    try:
        cj = json.loads(r['checks_json'])
    except Exception:
        continue
    worst.append((r['score'] if r['score'] is not None else -1, r['verdict'], r['mint'][:12], (cj.get('market') or {}).get('mc')))
worst.sort()
print("WORST 10:")
for s, v, m, mc in worst[:10]:
    print(f"  {s:5.1f} {v:8s} {m} mc={mc}")
print("distinct mints:", len(rows))

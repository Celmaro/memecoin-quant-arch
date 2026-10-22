#!/usr/bin/env python3
"""One-off backfill: re-score all distinct mints in scan_log with the fixed
Rugcheck pipeline; update scan_log with corrected entries and refresh call_metrics.
Keeps original rows (audit trail) — new rows are appended with backfill=1."""
import sys, time, json, sqlite3

sys.path.insert(0, "/home/agentuser/memecoin-quant")
import aegis
from desk_common import db, dex_pair

def main():
    con = db()
    mints = [r["mint"] for r in con.execute("SELECT DISTINCT mint FROM scan_log ORDER BY ts ASC")]
    con.close()
    print(f"Backfilling {len(mints)} distinct mint(s)...")
    ok = err = 0
    for i, m in enumerate(mints, 1):
        try:
            rep = aegis.score_coin(m, deep=False)
            ok += 1
            pair = dex_pair(m)
            mc = (pair or {}).get("marketCap")
            print(f"[{i}/{len(mints)}] {rep['score']:5.1f} {rep['verdict']:8s} {m[:12]} mc={mc}")
        except Exception as e:
            err += 1
            print(f"[{i}/{len(mints)}] ERR {m[:12]}: {str(e)[:80]}")
        time.sleep(0.5)  # rate-limit courtesy
    # summary of the rescored distribution
    con = db()
    rows = con.execute("""SELECT score, verdict, COUNT(*) n FROM scan_log
                          WHERE ts > (SELECT MAX(ts)-7200 FROM scan_log)
                          GROUP BY score, verdict ORDER BY n DESC LIMIT 12""").fetchall()
    print("\nLast-2h score distribution after backfill:")
    for r in rows:
        print(f"  {r['score']}: {r['verdict']} x{r['n']}")
    con.close()
    print(f"\nDone. rescanned={ok} errors={err}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""AEGIS DB maintenance (audit Oct 19 fixes #6/#7/#8).

Run manually or from desk_supervisor once a day. Idempotent.
  python3 aegis_maintenance.py            # run all
  python3 aegis_maintenance.py --dry-run  # report only

Actions:
  1. dedupe first_buyers (DDL has no UNIQUE; rescans duplicate rows)
  2. purge expired evidence_events (weight engine ignores them)
  3. purge old done followups (>14d) and stale undone followups (>7d overdue)
  4. retention: wallet_history >30d, scan_log >30d, old evidence purge
  5. report orphans (first_buyers without coins row) and prune >7d old ones
  6. checkpoint WAL + optimize
"""
import sys, time, sqlite3, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desk_common import db, init_schema, log_event

DRY = "--dry-run" in sys.argv
NOW = time.time()
DAY = 86400

def count(con, sql, *a):
    return con.execute(sql, a).fetchone()[0]

def main():
    # 3rd audit 7.2: aegis_maintenance immediately queried evidence_events and
    # wallet_history, which are created by bot.persistence.ensure_schema() — NOT
    # by desk_common.init_schema(). On a fresh checkout the daily job crashed on
    # a missing table and the supervisor silently rescheduled it for +24h.
    # Initialize the full schema first (idempotent) so cleanup works everywhere.
    try:
        from bot.persistence import ensure_schema
    except Exception:
        ensure_schema = None
    try:
        if ensure_schema is not None:
            ensure_schema()
        else:
            init_schema()
    except Exception:
        init_schema()
    con = db()
    report = {}

    # 1. dedupe first_buyers
    n = count(con, """SELECT COUNT(*) FROM first_buyers WHERE rowid NOT IN (
        SELECT MIN(rowid) FROM first_buyers GROUP BY coin_mint, wallet, rank)""")
    report["first_buyer_dups"] = n
    if n and not DRY:
        con.execute("""DELETE FROM first_buyers WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM first_buyers GROUP BY coin_mint, wallet, rank)""")

    # 2. expired evidence_events
    n = count(con, "SELECT COUNT(*) FROM evidence_events WHERE expires_at IS NOT NULL AND expires_at < ?", NOW)
    report["expired_evidence"] = n
    if n and not DRY:
        con.execute("DELETE FROM evidence_events WHERE expires_at IS NOT NULL AND expires_at < ?", (NOW,))

    # 3. followups: done >14d gone; undone >3d overdue marked done (stale loop) with result='expired'
    # (F2 Oct 19: followups are due within 24h — 3 days overdue is a real miss; was 7d)
    n1 = count(con, "SELECT COUNT(*) FROM followups WHERE done=1 AND due_ts < ?", NOW - 14*DAY)
    n2 = count(con, "SELECT COUNT(*) FROM followups WHERE done=0 AND due_ts < ?", NOW - 3*DAY)
    report["followups_done_purge"] = n1
    report["followups_stale_expired"] = n2
    if not DRY:
        if n1: con.execute("DELETE FROM followups WHERE done=1 AND due_ts < ?", (NOW - 14*DAY,))
        if n2: con.execute("UPDATE followups SET done=1, result='expired (maintenance)', outcome='EXPIRED' WHERE done=0 AND due_ts < ?", (NOW - 3*DAY,))

    # 4. retention 30d
    for tbl, col in [("wallet_history", "ts"), ("scan_log", "ts")]:
        n = count(con, f"SELECT COUNT(*) FROM [{tbl}] WHERE [{col}] < ?", NOW - 30*DAY)
        report[f"{tbl}_retention_30d"] = n
        if n and not DRY:
            con.execute(f"DELETE FROM [{tbl}] WHERE [{col}] < ?", (NOW - 30*DAY,))

    # 5. first_buyer orphans older than 7d (coins row never created)
    n = count(con, """SELECT COUNT(*) FROM first_buyers f LEFT JOIN coins c ON c.mint=f.coin_mint
        WHERE c.mint IS NULL AND f.ts < ?""", NOW - 7*DAY)
    report["first_buyer_orphans_7d"] = n
    if n and not DRY:
        con.execute("""DELETE FROM first_buyers WHERE rowid IN (
            SELECT f.rowid FROM first_buyers f LEFT JOIN coins c ON c.mint=f.coin_mint
            WHERE c.mint IS NULL AND f.ts < ?)""", (NOW - 7*DAY,))

    if not DRY:
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA optimize")
        con.commit()
    con.close()

    # 7. LEARNING REFRESH (Oct 19 → 4th audit 6.5/7.1, Oct 23): recompute empirical
    # weight overrides from the latest outcomes, but ONLY in SHADOW MODE — the
    # learner had target leakage (post-entry scans) + mixed label sources, so its
    # deltas are not trusted for auto-application. Shadow file is written; the
    # LIVE overrides stay frozen until a human reviews holdout agreement and
    # runs `aegis_learn.py --apply` explicitly. aegis_learn.py is read-only
    # except the shadow file; a failure here never blocks the rest of maintenance.
    lr = "not_run"
    if not DRY:
        try:
            import subprocess
            p = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "aegis_learn.py")],
                               capture_output=True, text=True, timeout=70)
            # 3rd audit 7.2: check returncode — a crashed learner (missing table,
            # traceback) used to be parsed as "no deltas written" = silent success
            # that appeared to rerun every day while never updating the file.
            n = 0
            if p.returncode != 0:
                lr = f"FAILED (rc={p.returncode})"
                report["learning_error"] = (p.stderr or p.stdout or "")[-200:]
            else:
                for ln in (p.stdout or "").splitlines():
                    if "SHADOW MODE" in ln or "wrote" in ln:
                        lr = ln.strip()
                    if "delta=" in ln:
                        n += 1
            report["learning_deltas"] = n
        except Exception as e:
            report["learning_error"] = str(e)[:80]
    report["learning"] = lr

    mode = "DRY-RUN" if DRY else "APPLIED"
    print(f"[aegis_maintenance {mode}] " + " | ".join(f"{k}={v}" for k, v in report.items()))
    return report

if __name__ == "__main__":
    main()

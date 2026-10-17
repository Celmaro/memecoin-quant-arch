"""Decision audit + wallet history persistence (doc 1: persistence/decision_audit.py, wallet_history.py)."""
import json, os, sys, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import db

DECISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT, mint TEXT, ts REAL,
    action TEXT, sell_fraction REAL, confidence REAL,
    reason_codes TEXT, thesis_state TEXT,
    evidence_for TEXT, evidence_against TEXT,
    data_quality TEXT, reason TEXT, checks_json TEXT);
CREATE TABLE IF NOT EXISTS wallet_history(
    wallet TEXT, mint TEXT, ts REAL, balance REAL,
    event TEXT, PRIMARY KEY(wallet, mint, ts));
CREATE INDEX IF NOT EXISTS idx_decisions_mint ON decision_audit(mint);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decision_audit(ts);
CREATE TABLE IF NOT EXISTS evidence_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL, ts REAL NOT NULL, observed_at REAL NOT NULL,
    feature TEXT NOT NULL, signal_text TEXT, half_life_s REAL, expires_at REAL,
    confidence REAL, severity TEXT, provenance TEXT);
CREATE INDEX IF NOT EXISTS idx_ee_mint ON evidence_events(mint, observed_at);
"""

def ensure_schema():
    con = db()
    con.executescript(DECISION_SCHEMA)
    con.commit(); con.close()

def log_decision(decision, mint):
    """Persist a doc-1 decision contract. decision is a dict with position_id,
    action, sell_fraction, confidence, reason_codes, evidence_timestamp,
    thesis_state, evidence_for, evidence_against, data_quality, reason."""
    ensure_schema()
    con = db()
    con.execute(
        "INSERT INTO decision_audit(position_id, mint, ts, action, sell_fraction, confidence,"
        " reason_codes, thesis_state, evidence_for, evidence_against, data_quality, reason, checks_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (decision.get("position_id"), mint, decision.get("evidence_timestamp") or time.time(),
         decision.get("action"), decision.get("sell_fraction"), decision.get("confidence"),
         json.dumps(decision.get("reason_codes") or []),
         decision.get("thesis_state"),
         json.dumps(decision.get("evidence_for") or []),
         json.dumps(decision.get("evidence_against") or []),
         decision.get("data_quality"), decision.get("reason"),
         json.dumps(decision.get("checks") or {})))
    con.commit(); con.close()

def recent_decisions(mint=None, limit=50):
    ensure_schema()
    con = db()
    if mint:
        rows = con.execute("SELECT * FROM decision_audit WHERE mint=? ORDER BY ts DESC LIMIT ?", (mint, limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM decision_audit ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]

def log_evidence(mint, signal_text, observed_ts=None, severity="info", provenance="position_monitor"):
    """Append-only evidence event with TTL fields (spec §3). feature/half_life from
    evidence_ttl.HALF_LIVES; expires_at = observed + hard expiry."""
    ensure_schema()
    from bot.decision_engine.evidence_ttl import HALF_LIVES, feature_of
    observed = observed_ts or time.time()
    feature = feature_of(signal_text)
    half, expires = HALF_LIVES.get(feature, (300, 1800))
    con = db()
    con.execute(
        "INSERT INTO evidence_events(mint, ts, observed_at, feature, signal_text, half_life_s, expires_at, confidence, severity, provenance)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (mint, observed, observed, feature, signal_text[:200], half, observed + expires, 1.0, severity, provenance))
    con.commit(); con.close()

def recent_evidence(mint, window_s=3600, limit=200):
    ensure_schema()
    con = db()
    rows = con.execute(
        "SELECT signal_text, observed_at, feature FROM evidence_events WHERE mint=? AND observed_at>=? ORDER BY observed_at DESC LIMIT ?",
        (mint, time.time() - window_s, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]

def wallet_history_add(wallet, mint, balance, event):
    con = db()
    con.execute("INSERT OR REPLACE INTO wallet_history(wallet, mint, ts, balance, event) VALUES(?,?,?,?,?)",
                (wallet, mint, time.time(), balance, event))
    con.commit(); con.close()

def wallet_balances(wallet, mint=None, limit=20):
    con = db()
    if mint:
        rows = con.execute("SELECT * FROM wallet_history WHERE wallet=? AND mint=? ORDER BY ts DESC LIMIT ?",
                           (wallet, mint, limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM wallet_history WHERE wallet=? ORDER BY ts DESC LIMIT ?",
                           (wallet, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]
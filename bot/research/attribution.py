"""Replay & attribution (spec §7 / blueprint §10): bi-temporal event store +
closed-position attribution metrics + DVA vs fixed baseline.

Bi-temporal rule: every event carries event_time (on-chain) AND ingested_at
(when WE saw it). Replay at T_sim may only use rows with ingested_at <= T_sim —
no future leak.

Attribution for closed positions (doc formulas):
  MFE/MAE: (peak/trough - entry)/entry from position high-water marks
  PRR:     realized pnl / (initial_atoms * peak - cost_basis)   [profit retained]
  IS_exit: (filled * P_decision - net_proceeds) / (filled * P_decision)
  DVA:     autonomous policy pnl - fixed-baseline policy pnl on same price path
Baseline (doc): fixed 50% sell at 2x, 50% at 4x, -35% hard stop.
"""
import os, sys, json, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

DB = os.path.expanduser("~/memecoin-quant/aegis.db")

REPLAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL, event_time REAL NOT NULL, ingested_at REAL NOT NULL,
    kind TEXT, payload_json TEXT);
CREATE INDEX IF NOT EXISTS idx_me_mint ON market_events(mint, ingested_at);
CREATE TABLE IF NOT EXISTS position_attribution(
    symbol TEXT, mint TEXT, closed_at REAL,
    entry_px REAL, peak_px REAL, trough_px REAL, exit_px REAL,
    mfe REAL, mae REAL, prr REAL, is_exit REAL,
    realized_pnl REAL, baseline_pnl REAL, dva REAL,
    detail_json TEXT, PRIMARY KEY(symbol, closed_at));
"""

def ensure_schema():
    import sqlite3
    con = sqlite3.connect(DB)
    con.executescript(REPLAY_SCHEMA)
    con.commit(); con.close()

def record_event(mint, kind, payload, event_time=None, ingested_at=None):
    """Bi-temporal event record (market_events)."""
    ensure_schema()
    import sqlite3
    con = sqlite3.connect(DB)
    con.execute("INSERT INTO market_events(mint, event_time, ingested_at, kind, payload_json) VALUES(?,?,?,?,?)",
                (mint, event_time or time.time(), ingested_at or time.time(), kind, json.dumps(payload)[:4000]))
    con.commit(); con.close()

def replay_window(mint, t_start, t_end):
    """Rows visible at each simulated time: strictly ingested_at <= T_sim.
    Returns list of (event_time, kind, payload) visible by t_end."""
    ensure_schema()
    import sqlite3
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT event_time, kind, payload_json FROM market_events"
        " WHERE mint=? AND ingested_at<=? AND ingested_at>=? ORDER BY ingested_at",
        (mint, t_end, t_start)).fetchall()
    con.close()
    return [(r[0], r[1], json.loads(r[2] or "{}")) for r in rows]

def baseline_policy(price_path):
    """Fixed baseline: sell 50% at 2x, 50% of rest at 4x, -35% stop. price_path:
    chronological [(ts, px)] from entry. Returns realized pnl multiple (per 1 USD)."""
    entry = price_path[0][1]
    if entry <= 0:
        return 0.0
    sold1 = sold2 = False
    pnl = 0.0
    for ts, px in price_path:
        mult = px / entry
        if not sold1 and mult >= 2.0:
            pnl += 0.5 * (2.0 - 1); sold1 = True
        elif sold1 and not sold2 and mult >= 4.0:
            pnl += 0.5 * (4.0 - 1); sold2 = True
        elif mult <= 0.65:
            remaining = 1.0 - (0.5 if sold1 else 0.0) - (0.5 if sold2 else 0.0)
            pnl += remaining * (mult - 1)
            return pnl
    # path ended without stop: mark remaining to last price
    remaining = 1.0 - (0.5 if sold1 else 0.0) - (0.5 if sold2 else 0.0)
    mult = price_path[-1][1] / entry
    pnl += remaining * (mult - 1)
    return pnl

def attribute(symbol, mint, pos, realized_pnl, exit_px, decision_px=None, price_path=None):
    """Compute and persist attribution for a CLOSED position.
    pos must carry entry_px, peak/trough (or fallbacks), initial_usd."""
    ensure_schema()
    entry = float(pos.get("entry_px") or 0)
    peak = float(pos.get("peak_price") or max([p for _, p in (price_path or [])] + [entry]))
    trough = float(pos.get("trough_price") or min([p for _, p in (price_path or [])] + [entry]))
    initial = float(pos.get("initial_usd") or pos.get("entry_usd") or 0)
    mfe = (peak - entry) / entry if entry else 0.0
    mae = (trough - entry) / entry if entry else 0.0
    # PRR: retained share of the favorable excursion value
    favorable_usd = initial * mfe
    prr = (realized_pnl / favorable_usd) if favorable_usd > 0 else (1.0 if realized_pnl > 0 else 0.0)
    # IS_exit: shortfall vs price at decision time
    is_exit = 0.0
    if decision_px and exit_px and decision_px > 0:
        expected = exit_px  # placeholder if filled==decision; real case uses atoms
        is_exit = (decision_px - exit_px) / decision_px if decision_px else 0.0
    # DVA vs baseline on same path
    dva = None
    if price_path and len(price_path) >= 2:
        base = baseline_policy(price_path) * initial
        dva = realized_pnl - base
    import sqlite3
    con = sqlite3.connect(DB)
    con.execute(
        "INSERT OR REPLACE INTO position_attribution(symbol, mint, closed_at, entry_px, peak_px,"
        " trough_px, exit_px, mfe, mae, prr, is_exit, realized_pnl, baseline_pnl, dva, detail_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (symbol, mint, time.time(), entry, peak, trough, exit_px,
         round(mfe, 4), round(mae, 4), round(prr, 4), round(is_exit, 4),
         round(realized_pnl, 4), round((baseline_policy(price_path) * initial) if price_path else 0, 4),
         round(dva, 4) if dva is not None else None,
         json.dumps({"path_points": len(price_path or [])})))
    con.commit(); con.close()
    return {"mfe": round(mfe, 3), "mae": round(mae, 3), "prr": round(prr, 3),
            "is_exit": round(is_exit, 3), "dva": round(dva, 3) if dva is not None else None}

def attribution_report(limit=20):
    ensure_schema()
    import sqlite3
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM position_attribution ORDER BY closed_at DESC LIMIT ?",
                       (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]

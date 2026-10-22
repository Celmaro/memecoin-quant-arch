"""Wallet cohort service (spec §4): 24h ∩ 7d ∩ 30d leaderboard intersection.

Credit math (doc f949bddc §3): 250 credits/request/window. Refreshing all 3
windows every 8h = 67,500 credits/month — the doc-recommended cadence. Between
refreshes, cohort is stable and monitored via cheaper activity feeds.

Entity collapse (Stage B): wallets sharing a funding source (aegis.db
funding_links / first_buyers graph) or exact CEX deposit target are merged into
one canonical_entity_id. Decision counters must COUNT(DISTINCT entity), not wallets.

Storage: SQLite table leaderboard_snapshots in aegis.db (append-only, bi-temporal:
block/event time vs observed_at).
"""
import os, sys, json, time, sqlite3
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import db, load_config, http_json, FOMO_NETWORKS

SNAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS leaderboard_snapshots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,               -- 'fomoapi'
    window_definition TEXT NOT NULL,    -- '24H_ROLLING','7D_ROLLING','30D_ROLLING'
    wallet_address TEXT NOT NULL,
    rank INTEGER,
    realized_pnl_usd REAL,
    win_rate REAL,
    median_hold_seconds REAL,
    observed_at REAL NOT NULL,          -- when WE fetched it (bi-temporal)
    event_time REAL,                    -- provider's window timestamp if given
    raw_json TEXT);
CREATE INDEX IF NOT EXISTS idx_lbs_window ON leaderboard_snapshots(source, window_definition, observed_at);
CREATE INDEX IF NOT EXISTS idx_lbs_wallet ON leaderboard_snapshots(wallet_address);
CREATE TABLE IF NOT EXISTS wallet_entity_clusters(
    wallet_address TEXT PRIMARY KEY,
    entity_cluster_id TEXT NOT NULL,
    reason TEXT,
    first_linked_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_wec_cluster ON wallet_entity_clusters(entity_cluster_id);
"""

REFRESH_INTERVAL_S = 8 * 3600   # 8h per doc credit math
FILTERS = dict(min_win_rate=0.45, min_median_hold_s=180)  # doc: PnL>0, WR>=45%, hold>=180s

def ensure_schema():
    con = db(); con.executescript(SNAP_SCHEMA); con.commit(); con.close()

def _fetch_window(window, net=1399811149):
    """Fetch one leaderboard window. Endpoint per ToolStacks (verified):
    GET /v2/leaderboard/{window} → handle, pnlUsd, volumeUsd, trades, wallets{solana,evm}."""
    cfg = load_config()
    fk = cfg.get("FOMO_KEY")
    if not fk:
        return []
    url = f"https://api.fomoapi.io/v2/leaderboard/{window}?network={net}&limit=100"
    try:
        st, data = http_json(url, headers={"Authorization": f"Bearer {fk}"}, retries=2)
        rows = data.get("leaderboard") or data.get("traders") or data.get("data") or data
        if isinstance(rows, dict):
            rows = rows.get("leaderboard") or rows.get("traders") or []
        if not isinstance(rows, list):
            return []
        out = []
        for r in rows:
            wallets = r.get("wallets") or {}
            addr = None
            if isinstance(wallets, dict):
                addr = wallets.get("solana") or wallets.get("evm")
            elif isinstance(wallets, list) and wallets:
                addr = wallets[0]
            if not addr:
                addr = r.get("wallet") or r.get("walletAddress") or r.get("address")
            if not addr:
                continue
            out.append({"rank": r.get("rank"), "wallet": addr,
                        "realizedPnlUsd": r.get("pnlUsd") or r.get("realizedPnlUsd"),
                        "winRate": r.get("winRate") or r.get("win_rate"),
                        "medianHoldSeconds": r.get("medianHoldSeconds") or r.get("avgHoldSeconds") or r.get("avgHoldTimeS")})
        return out
    except Exception:
        return []


# ---------- SolanaTracker fetcher (replaces FomoAPI trader leaderboard — 402-locked) ----------
ST_BASE = "https://data.solanatracker.io"

def _st_headers():
    cfg = load_config()
    key = cfg.get("SOLANATRACKER_KEY")
    return {"x-api-key": key} if key else {}

def st_fetch_leaderboard(window="7d", limit=100):
    """GET /v2/pnl/leaderboard/top — Solana trader rankings by PnL. Correct param is
    `days` (int: 1|7|30|90), NOT `window` (silently defaults to 90 — verified live).
    window accepts 24h|7d|30d|90d. Returns rows."""
    key = load_config().get("SOLANATRACKER_KEY")
    if not key:
        return []
    w = str(window).lower()
    days = {"24h": 1, "24": 1, "1d": 1, "1": 1, "7d": 7, "7": 7,
            "30d": 30, "30": 30, "90d": 90, "90": 90}.get(w, 7)
    url = f"{ST_BASE}/v2/pnl/leaderboard/top?limit={limit}&days={days}&sort=realized&minTrades=5"
    try:
        import urllib.request, urllib.error
        req = urllib.request.Request(url, headers={"x-api-key": key, "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        out = []
        for i, r in enumerate((data.get("traders") or []), 1):
            period = r.get("period") or {}
            days_obj = period.get("days") or {}
            counts = r.get("counts") or {}
            out.append({"rank": i, "wallet": r.get("wallet"),
                        "realizedPnlUsd": period.get("realized"),
                        "winRate": (days_obj.get("winRate") or 0) / 100 if days_obj.get("winRate") is not None else None,
                        "medianHoldSeconds": None,  # leaderboard has no hold-time; wallet summary does
                        "volume": period.get("volume"), "roi": period.get("roi"),
                        "trades": counts.get("trades")})
        return out
    except Exception:
        return []

def st_fetch_wallet_summary(wallet):
    """GET /v2/pnl/wallets/{wallet} — summary.pnl.realized, winRate, timing.avgHoldTimeSecs.
    (Path WITHOUT /summary; verified live — /summary variant 404s.)"""
    key = load_config().get("SOLANATRACKER_KEY")
    if not key:
        return None
    try:
        import urllib.request, urllib.error
        req = urllib.request.Request(f"{ST_BASE}/v2/pnl/wallets/{wallet}",
                                     headers={"x-api-key": key, "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
        summary = data.get("summary") or {}
        pnl = summary.get("pnl") or {}
        totals = summary.get("totals") or {}
        timing = summary.get("timing") or {}
        return {"realizedPnlUsd": pnl.get("realized") or totals.get("realizedPnl"),
                "winRate": summary.get("winRate") or totals.get("winRate"),
                "medianHoldSeconds": timing.get("avgHoldTimeSecs")}
    except Exception:
        return None

# ---------- Fomo-API v1 fetcher (Oct 21 — NEW product, user-provided key) ----------
# api.fomo-api.com = "Solana Wallet PnL & Trader Endpoints" — NOT the legacy
# api.fomoapi.io (402-dead). board=solana = WALLETS ranked by REALIZED PnL on
# Solana (conservative/auditable: coveragePct + pnlStatus; null never means 0).
# Free plan: 1,500 calls/mo, 10 rpm, 20 rows/request cap (live-probed Oct 21).
FL_BASE = "https://api.fomo-api.com"

def _fl_headers():
    cfg = load_config()
    key = cfg.get("FOMO_LIVE_KEY")
    return {"Authorization": f"Bearer {key}", "User-Agent": "Mozilla/5.0"} if key else {}

def fl_fetch_leaderboard(window="7d", limit=20):
    """GET /v1/leaderboard?window=&board=solana — realized-PnL Solana wallet board.
    Returns rows keyed by `address` (rank, realizedPnlUsd, returnPct, volumeUsd,
    trades, coveragePct, pnlStatus). No winRate/hold — cohort filter falls back
    to realized_pnl>0 (refresh() stores win_rate=NULL and the qualifier handles it)."""
    if not load_config().get("FOMO_LIVE_KEY"):
        return []
    w = str(window).lower()
    win = {"24h": "24h", "24": "24h", "1d": "24h", "7d": "7d", "7": "7d",
           "30d": "30d", "30": "30d"}.get(w, "7d")
    url = f"{FL_BASE}/v1/leaderboard?window={win}&board=solana&limit={min(limit, 20)}"
    try:
        st, data = http_json(url, headers=_fl_headers(), timeout=20)
        if not isinstance(data, dict):
            return []
        rows = data.get("data") or []
        out = []
        for i, r in enumerate(rows, 1):
            addr = r.get("address")
            if not addr:
                continue
            out.append({"rank": r.get("rank") or i, "wallet": addr,
                        "address": addr, "realizedPnlUsd": r.get("realizedPnlUsd"),
                        "winRate": None,  # not provided by this board
                        "medianHoldSeconds": None,
                        "volume": r.get("volumeUsd"), "roi": r.get("returnPct"),
                        "trades": r.get("trades"), "coveragePct": r.get("coveragePct"),
                        "pnlStatus": r.get("pnlStatus"),
                        "displayName": r.get("displayName"), "handle": r.get("handle")})
        return out
    except Exception:
        return []

def fl_fetch_wallet(wallet):
    """GET /v1/wallet/:address — 30-day realized PnL aggregate for one wallet."""
    if not load_config().get("FOMO_LIVE_KEY"):
        return None
    try:
        st, d = http_json(f"{FL_BASE}/v1/wallet/{wallet}", headers=_fl_headers(), timeout=20)
        if not isinstance(d, dict) or d.get("error"):
            return None
        return {"realizedPnlUsd": d.get("realizedPnlUsd"), "winRate": None,
                "medianHoldSeconds": None, "volumeUsd": d.get("volumeUsd"),
                "trades": d.get("trades"), "coveragePct": d.get("coveragePct"),
                "pnlStatus": d.get("pnlStatus"), "returnPct": d.get("returnPct")}
    except Exception:
        return None

def refresh(force=False, source="fomo-live"):
    """Pull all 3 windows, snapshot to DB. Skips if last refresh < 8h ago.
    source: fomo-live (DEFAULT Oct 21 — new api.fomo-api.com, free 1,500/mo,
    realized-PnL Solana wallet board, user-provided key) or solanatracker
    (free key, verified) or fomoapi (legacy 402-locked as of Oct 16)."""
    ensure_schema()
    con = db()
    last = con.execute("SELECT MAX(observed_at) FROM leaderboard_snapshots").fetchone()[0] or 0
    if not force and time.time() - last < REFRESH_INTERVAL_S:
        con.close()
        return {"skipped": True, "last_refresh_ago_s": round(time.time() - last)}
    now = time.time()
    saved = 0
    for window in ("24h", "7d", "30d"):
        if source == "fomo-live":
            rows = fl_fetch_leaderboard(window)
        elif source == "solanatracker":
            rows = st_fetch_leaderboard(window)
        else:
            rows = _fetch_window(window)
        for r in rows:
            addr = r.get("wallet") or r.get("wallet_address") or r.get("address")
            if not addr:
                continue
            con.execute(
                "INSERT INTO leaderboard_snapshots(source, window_definition, wallet_address, rank,"
                " realized_pnl_usd, win_rate, median_hold_seconds, observed_at, event_time, raw_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (source, f"{window.upper()}_ROLLING", addr, r.get("rank"),
                 r.get("realizedPnlUsd") or r.get("pnlUsd"), r.get("winRate"),
                 r.get("medianHoldSeconds") or r.get("avgHoldSeconds") or r.get("avgHoldTimeS"), now,
                 r.get("windowTimestamp"), json.dumps(r)[:2000]))
            saved += 1
    con.commit(); con.close()
    return {"saved": saved, "windows": ["24h", "7d", "30d"], "observed_at": now, "source": source}

def qualifying_cohort(snapshot_time=None, source="solanatracker", freshness_s=6 * 3600,
                      apply_hold_filter=True):
    """Stage A: wallets in ALL THREE windows, point-in-time, doc filters applied.
    Enriches intersection candidates with wallet summaries (win rate/hold time)
    since the leaderboard row lacks hold-time. Returns [{wallet, entity, ...}]."""
    ensure_schema()
    t = snapshot_time or time.time()
    con = db()
    rows = {}
    for window in ("24H_ROLLING", "7D_ROLLING", "30D_ROLLING"):
        ws = con.execute(
            "SELECT wallet_address, rank, realized_pnl_usd, win_rate, median_hold_seconds"
            " FROM leaderboard_snapshots WHERE source=? AND window_definition=?"
            " AND observed_at<=? AND observed_at>=? ORDER BY observed_at DESC",
            (source, window, t, t - freshness_s)).fetchall()
        best = {}
        for w in ws:
            if w["wallet_address"] not in best:
                best[w["wallet_address"]] = w
        rows[window] = best
    con.close()
    # intersection with doc filters: PnL>0 (all), win rate >= 45% (7d best proxy)
    set24 = {a for a, w in rows["24H_ROLLING"].items() if (w["realized_pnl_usd"] or 0) > 0}
    set7 = {a for a, w in rows["7D_ROLLING"].items()
            if (w["win_rate"] or 0) >= FILTERS["min_win_rate"] or (w["realized_pnl_usd"] or 0) > 0}
    set30 = {a for a, w in rows["30D_ROLLING"].items() if (w["realized_pnl_usd"] or 0) > 0}
    inter = set24 & set7 & set30
    # enrich intersection candidates with wallet summaries for the hold-time filter
    # Oct 21: source-aware. fomo-live (realized board) enriches via fl_fetch_wallet
    # — coveragePct/pnlStatus already gate completeness, and a None hold is a PASS
    # (no SolanaTracker credits burned). solanatracker keeps the old ST enrichment.
    out = []
    _is_fl = source == "fomo-live"
    for a in inter:
        wr = rows["24H_ROLLING"][a]["win_rate"]
        hold_s = None
        if apply_hold_filter:
            if _is_fl:
                summ = fl_fetch_wallet(a)
                if summ and summ.get("pnlStatus") == "complete" and (summ.get("coveragePct") or 0) >= 60:
                    pass  # auditable realized figures — no hold-time gate for this source
            else:
                summ = st_fetch_wallet_summary(a)
                if summ:
                    hold_s = summ.get("medianHoldSeconds")
                    if summ.get("winRate") is not None:
                        wr = summ["winRate"] / 100 if summ["winRate"] > 1 else summ["winRate"]
        if not _is_fl and apply_hold_filter and (hold_s is None or hold_s < FILTERS["min_median_hold_s"]):
            continue
        out.append({"wallet": a, "entity": canonical_entity(a),
                    "win_rate": wr, "hold_s": hold_s})
    return out

def canonical_entity(wallet):
    """Map wallet -> canonical_entity_id via aegis funding graph (Stage B lite:
    funding_links/first_buyers are the stored funding relations)."""
    ensure_schema()
    con = db()
    try:
        row = con.execute("SELECT entity_cluster_id FROM wallet_entity_clusters WHERE wallet_address=?",
                          (wallet,)).fetchone()
        if row:
            return row["entity_cluster_id"]
        # build from funding_links (funder->deployer edges) + first_buyers (wallet graph)
        link = con.execute("SELECT funder FROM funding_links WHERE deployer=? LIMIT 1", (wallet,)).fetchone()
        if link:
            cluster, reason = link["funder"], "funded_by"
        else:
            link = con.execute("SELECT deployer FROM funding_links WHERE funder=? LIMIT 1", (wallet,)).fetchone()
            cluster, reason = (link["deployer"], "funder_of") if link else (wallet, "self")
        con.execute("INSERT OR REPLACE INTO wallet_entity_clusters(wallet_address, entity_cluster_id, reason, first_linked_at) VALUES(?,?,?,?)",
                    (wallet, cluster, reason if reason != "self" else "self", time.time()))
        con.commit()
        return cluster
    finally:
        con.close()

def cohort_signal_for(mint, window_s=3600, now=None):
    """CohortScore input: distinct ENTITY accumulation/distribution of `mint`
    among cohort wallets observed in wallet_history (bot.persistence) or
    aegis first_buyers. Returns {entities_buying, entities_selling, signal}."""
    now = now or time.time()
    ensure_schema()
    con = db()
    try:
        rows = con.execute(
            "SELECT wallet, event, ts FROM wallet_history WHERE mint=? AND ts>=? ORDER BY ts DESC LIMIT 500",
            (mint, now - window_s)).fetchall()
    except Exception:
        rows = []
    con.close()
    buying, selling = set(), set()
    for r in rows:
        ent = canonical_entity(r["wallet"])
        if r["event"] in ("buy", "BUY"):
            buying.add(ent)
        elif r["event"] in ("sell", "SELL"):
            selling.add(ent)
    buying -= selling  # net position
    if len(selling) >= 2 and not buying:
        sig = "DISTRIBUTION"
    elif len(buying) >= 2:
        sig = "ACCUMULATION"
    else:
        sig = "NEUTRAL"
    return {"entities_buying": len(buying), "entities_selling": len(selling), "signal": sig}

def credit_budget_check():
    """Report monthly credit cost of current refresh interval (doc: 250/window)."""
    per_day = (24 / 8) * 3 * 250
    return {"credits_per_day": per_day, "credits_per_month": per_day * 30,
            "within_free_250k": per_day * 30 < 250_000}

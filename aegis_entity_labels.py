#!/usr/bin/env python3
"""AEGIS entity-label enrichment: fuse Vybe + Metasleuth label intel into AEGIS memory.

Adopted from apostleoffinance/Solana-Forensic-Analysis-Tool (entity_labeling.py
label-merge pattern, label precedence Vybe > Metasleuth; Flipside CSVs optional).
Answers the desk question the statistical rules can't: WHO is behind this wallet
(CEX, bridge, known rug, project treasury)?

Adds:
  - wallet_labels table (wallet, source, entity, category, confidence, ts)
  - enrich_wallets(wallets) -> {wallet: {entity, category, source}}
  - CLI: python3 aegis_entity_labels.py <wallet...> | --scan-first-buyers [N]

Label precedence (higher wins): metasleuth > vybe > manual.
Category taxonomy (substring, lowercased): exchange / bridge / defi / nft /
rug / airdrop / project / mev / other.
"""
import json, re, sys, time
import urllib.request, urllib.parse
from desk_common import db, init_schema, log_event, CONFIG  # CONFIG: dict from CONFIG.md parse if present

VYBE_BASE = "https://api.vybenetwork.com"
METASLEUTH_BASE = "https://aml.blocksec.com/api/aml/v2"

# Keys read from environment or CONFIG.md-style flat file if provided.
def _key(name):
    import os
    if os.environ.get(name):
        return os.environ[name]
    try:
        with open("CONFIG.md") as f:
            m = re.search(rf"{name}[:=\s]+([A-Za-z0-9_\-\.]+)", f.read())
            return m.group(1) if m else None
    except Exception:
        return None

# ---------- taxonomy ----------
CATEGORY_PATTERNS = [
    ("exchange", r"binance|coinbase|okx|bybit|kraken|kucoin|bitget|gate\.io|mexc|htx|huobi|bithumb|upbit|openbook"),
    ("bridge",   r"bridge|allbridge|wormhole|portal|debridge|mayan|across"),
    ("defi",     r"jupiter|raydium|orca|meteora|kamino|solend|marginfi|drift|phoenix|lifinity|uxd|vault"),
    ("nft",      r"magic eden|magiceden|tensor|nft|digitaleyes"),
    ("rug",      r"\brug\b|rugpull|rug pull|bricked|scam|hacked|exploit|drainer"),
    ("airdrop",  r"airdrop|jito airdrop|pengu"),
    ("mev",      r"jito|mev|arbitrage|sandwich|searcher|snipe bot|sniper bot|first-block entry"),
    ("project",  r"treasury|team|project|foundation|dao|multisig|deployer"),
]

def classify_category(label: str) -> str:
    low = (label or "").lower()
    for cat, pat in CATEGORY_PATTERNS:
        if re.search(pat, low):
            return cat
    return "other"

# ---------- fetchers (mirrors data_fetching.py patterns) ----------
def _get_json(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def vybe_labels(wallets):
    """Vybe known-accounts: GET /addresses/{address} (label, type). One per wallet."""
    key = _key("VYBE_API_KEY")
    if not key:
        return {}
    out = {}
    for w in wallets:
        try:
            d = _get_json(f"{VYBE_BASE}/addresses/{w}", headers={"X-API-KEY": key})
            label = d.get("label") or d.get("name")
            if label:
                out[w] = {"entity": label, "source": "vybe", "raw": d.get("type", "")}
        except Exception:
            continue
    return out

def metasleuth_labels(wallets, chain="solana"):
    """Metasleuth batch address labels: POST /address-labels (100/req).
    Body: {"chain": "solana", "address_list": [...]}. Returns {addr: {name, entity_type}}."""
    key = _key("METASLEUTH_API_KEY")
    if not key:
        return {}
    out = {}
    for i in range(0, len(wallets), 100):
        batch = wallets[i:i+100]
        body = json.dumps({"chain": chain, "address_list": batch}).encode()
        req = urllib.request.Request(
            f"{METASLEUTH_BASE}/address-labels?api-key={key}",
            data=body, headers={"content-type": "application/json"})
        try:
            d = _get_json(req.get_full_url()) if False else json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
            entries = d.get("data") or d.get("result") or {}
            if isinstance(entries, dict):
                for w, info in entries.items():
                    label = (info or {}).get("name") or (info or {}).get("entity")
                    if label:
                        out[w] = {"entity": label, "source": "metasleuth", "raw": json.dumps(info)[:200]}
        except Exception:
            continue
    return out

# ---------- storage ----------
# ---------- self-sourced providers (no external key needed) ----------
def helius_first_funder(wallet, max_tx=100):
    """Funder = source of the wallet's earliest incoming native SOL transfer.
    Uses Helius Enhanced tx API (already in desk stack). Returns (funder, sol, ts) or (None,None,None).
    Note: default 100 txs — the most recent 20 are usually outbound trade traffic."""
    try:
        from desk_common import helius_txs
        txs = helius_txs(wallet, limit=max_tx)
        inc = []
        for t in txs:
            for tr in (t.get("nativeTransfers") or []):
                if tr.get("toUserAccount") == wallet and tr.get("fromUserAccount") != wallet:
                    inc.append((t.get("timestamp", 0), tr["fromUserAccount"], tr.get("amount", 0) / 1e9))
        if not inc:
            return None, None, None
        inc.sort()
        ts, funder, sol = inc[0]
        return funder, sol, ts
    except Exception:
        return None, None, None

def deployer_rug_lookup(funder):
    """Cross-match funder against AEGIS's own deployer memory (fresher than any
    commercial label DB for pump.fun rugs). Returns (label, category) or (None, None)."""
    if not funder:
        return None, None
    con = db()
    row = con.execute("SELECT coin_count, rug_count FROM deployers WHERE deployer = ?", (funder,)).fetchone()
    if row and row[1] > 0:
        return f"AEGIS deployer: {row[1]} rugs of {row[0]} launches", "rug"
    if row:
        return f"AEGIS deployer: {row[0]} launches, no rugs", "project"
    return None, None

def behavioral_labels(wallet, ts_seen=None):
    """Bot/MEV inference from first-buyer ranks (no external data).
    rank = position among the coin's oldest signers; rank 0-1 means the wallet was
    in the launch's first block(s) — bot-grade sniping when repeated across coins."""
    con = db()
    row = con.execute("""SELECT rank, COUNT(*) n FROM first_buyers WHERE wallet = ?
        GROUP BY wallet""", (wallet,)).fetchone()
    if not row:
        return None, None
    best_rank = con.execute("SELECT MIN(rank) FROM first_buyers WHERE wallet = ?", (wallet,)).fetchone()[0]
    n_coins = row[1]
    if best_rank is not None and best_rank <= 1 and n_coins >= 2:
        return f"first-block entry on {n_coins} coins (best rank {best_rank}) — snipe bot", "mev"
    if best_rank is not None and best_rank <= 1:
        return "first-block entry — likely sniper bot", "mev"
    return None, None

def self_labels(wallets, max_probes=25, depth=2):
    """Self-sourced enrichment tier: Helius first-funder + AEGIS deployer memory +
    behavioral bot inference. Cheapest and freshest source; applied first, external
    labels (when keys exist) override.
    depth=2: recurse once — probe the funder's own funder and record the edge in
    funding_links so cohort.py's entity graph gets 2 hops (AEGIS §19 pattern)."""
    out = {}
    for w in wallets[:max_probes]:
        funder, sol, ts = helius_first_funder(w)
        label = cat = None
        if funder:
            label, cat = deployer_rug_lookup(funder)
            if label:
                out[w] = {"entity": label, "source": "self:funder", "confidence": 0.9,
                          "meta": {"funder": funder, "funded_sol": sol}}
            else:
                blabel, bcat = behavioral_labels(w, ts)
                if blabel:
                    out[w] = {"entity": blabel, "source": "self:behavior", "confidence": 0.7}
            # --- depth-2: chase the funder's funder (once), store the edge
            if depth >= 2 and funder:
                try:
                    con = db()
                    seen = con.execute("SELECT 1 FROM funding_links WHERE funder=? AND deployer=?", (funder, w)).fetchone()
                    if not seen:
                        con.execute("INSERT INTO funding_links (funder, deployer, ts) VALUES (?,?,?)", (funder, w, ts or int(time.time())))
                    if not seen:
                        funder2, sol2, ts2 = helius_first_funder(funder)
                        if funder2:
                            con.execute("INSERT OR IGNORE INTO funding_links (funder, deployer, ts) VALUES (?,?,?)",
                                        (funder2, funder, ts2 or int(time.time())))
                            lbl2, cat2 = deployer_rug_lookup(funder2)
                            if lbl2 and w not in out:
                                out[w] = {"entity": f"{lbl2} (via funder {funder2[:6]}…)",
                                          "source": "self:funder2", "confidence": 0.8,
                                          "meta": {"funder": funder, "funder2": funder2}}
                                cat = cat2
                    con.commit()
                except Exception:
                    pass
        else:
            blabel, bcat = behavioral_labels(w, ts)
            if blabel:
                out[w] = {"entity": blabel, "source": "self:behavior", "confidence": 0.7}
    return out

def init_label_schema(con):
    con.execute("""CREATE TABLE IF NOT EXISTS wallet_labels (
        wallet TEXT NOT NULL, source TEXT NOT NULL, entity TEXT NOT NULL,
        category TEXT NOT NULL, confidence REAL DEFAULT 0.8, ts INTEGER,
        PRIMARY KEY (wallet, source))""")
    con.commit()

def save_labels(con, labels: dict):
    now = int(time.time())
    for w, info in labels.items():
        con.execute("""INSERT INTO wallet_labels (wallet, source, entity, category, confidence, ts)
            VALUES (?,?,?,?,?,?) ON CONFLICT(wallet, source) DO UPDATE SET
            entity=excluded.entity, category=excluded.category, ts=excluded.ts""",
            (w, info["source"], info["entity"], classify_category(info["entity"]), info.get("confidence", 0.8), now))
    con.commit()

def enrich_wallets(wallets, use_db=True):
    """Fused labels for a list of wallets. Precedence metasleuth > vybe.
    Returns {wallet: {entity, category, source}} — wallet stays unlabeled if no source knows it."""
    con = db()
    init_label_schema(con)
    result = {}
    if use_db:
        rows = con.execute(
            "SELECT wallet, source, entity, category FROM wallet_labels WHERE wallet IN (%s)"
            % ",".join("?" * len(wallets)), wallets).fetchall()
        prec = {"metasleuth": 2, "vybe": 1}
        for w, src, ent, cat in rows:
            if w not in result or prec.get(src, 0) > prec.get(result[w]["source"], 0):
                result[w] = {"entity": ent, "category": cat, "source": src, "cached": True}
    todo = [w for w in wallets if w not in result]
    if todo:
        fresh = self_labels(todo)          # self-sourced tier first (free, freshest)
        fresh.update(vybe_labels(todo))    # external tiers override when keys exist
        fresh.update(metasleuth_labels(todo))
        if fresh:
            save_labels(con, fresh)
            for w, info in fresh.items():
                result[w] = {"entity": info["entity"], "category": classify_category(info["entity"]),
                             "source": info["source"]}
    return result

def annotate_wallet_risk(wallet_stats: dict):
    """Given {wallet: {'role':..., 'sol':...}} from first-buyer memory, add entity context
    and return AEGIS score adjustments: known-exchange wallet in first buyers = -10
    (distribution, not conviction); known-rug entity involved = -15; MEV = -5."""
    labels = enrich_wallets(list(wallet_stats))
    adj, notes = 0, []
    for w, info in labels.items():
        cat = info["category"]
        if cat == "exchange":
            adj -= 10; notes.append(f"{w[:6]}… is {info['entity']} (exchange) — supply distribution risk")
        elif cat == "rug":
            adj -= 15; notes.append(f"{w[:6]}… labeled {info['entity']} — KNOWN RUG ENTITY")
        elif cat == "mev":
            adj -= 5; notes.append(f"{w[:6]}… is {info['entity']} (MEV)")
    return adj, notes, labels

# ---------- CLI ----------
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    if args[0] == "--scan-first-buyers":
        n = int(args[1]) if len(args) > 1 else 50
        con = db()
        rows = con.execute("""SELECT fb.wallet, fb.mint FROM first_buyers fb
            JOIN coins c ON c.mint = fb.mint
            ORDER BY c.first_seen DESC LIMIT ?""", (n * 5,)).fetchall()
        wallets = sorted({r[0] for r in rows})[:n]
        out = enrich_wallets(wallets)
        print(json.dumps(out, indent=1))
    else:
        print(json.dumps(enrich_wallets(args), indent=1))

"""signal_ingestion/ (doc 1: telegram_listener, channel_allowlist, signal_parser, signal_deduplication).

Adapters over the proven telegram_crawler.py — the curated channels are the ONLY
entry signals (buy-all mandate). These modules document the boundary and expose
the same extraction/dedupe/confluence logic to the architecture.
"""
import os, sys, json, re, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
SOL_MINT = "So11111111111111111111111111111111111111112"

def channel_allowlist():
    """Curated channels from CONFIG.md (entry-signal sources ONLY)."""
    try:
        text = open(os.path.expanduser("~/memecoin-quant/CONFIG.md")).read()
        m = re.search(r"Channels?\s*[:=]\s*(.+)", text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            chans = re.findall(r"@?[A-Za-z0-9_]{4,}", raw)
            return chans[:12]
    except Exception:
        pass
    return ["sol_trend", "solwhaletrending", "solearlytrending",
            "PIRBViewTrending", "soltrenchtrending", "SeekrTrending"]

def valid_mint(s):
    return (32 <= len(s) <= 44 and s != SOL_MINT and not s.startswith("0x") and not s.isdigit())

def parse_ca(text, hrefs=None):
    """Doc-1 signal_parser: extract CAs from message text AND href URLs
    (some channels embed the CA only in links — verified Oct 15)."""
    mints = []
    for t in [text] + (hrefs or []):
        mints += [m for m in MINT_RE.findall(t or "") if valid_mint(m)]
    seen = set(); out = []
    for m in mints:
        if m not in seen:
            seen.add(m); out.append(m)
    return out

def dedupe(calls, seen_mints):
    """Doc-1 signal_deduplication: dedupe by mint, preserving order."""
    out = []
    for c in calls:
        if c.get("mint") not in seen_mints:
            seen_mints.add(c["mint"])
            out.append(c)
    return out

def confluence(call, history):
    """Doc-1 signal strength: same mint called by N channels -> count distinct."""
    m = call.get("mint")
    channels = {call.get("channel")}
    for h in history:
        if h.get("mint") == m:
            channels.add(h.get("channel"))
    return len(channels), sorted(channels)

def telegram_listener_poll(scan=True, max_scan=4):
    """Adapter: run the proven crawler; returns new calls with confluence."""
    import telegram_crawler
    new = telegram_crawler.crawl_all(scan=scan, verbose=False, max_scan=max_scan)
    return new
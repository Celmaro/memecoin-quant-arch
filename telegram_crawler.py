#!/usr/bin/env python3
"""Telegram call crawler — polls curated channels via t.me/s/<name> preview pages.

Extracts mint / symbol / MC / age / wallet; dedupes by mint against AEGIS DB;
runs fast AEGIS scan on NEW calls and prints a desk alert.
"""
import json, re, time, sys
from desk_common import CHANNELS, db, init_schema, dex_pair, log_event
import aegis

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36"
MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
SYM_RE = re.compile(r"\$([A-Z][A-Z0-9_]{1,11})")
MC_RE = re.compile(r"(?:MC|Market Cap|mcap)[:\s]*[$]?\s*([\d.,]+[KMB]?)", re.I)
AGE_RE = re.compile(r"(?:Age|age)[:\s]*([\d]+[mhd])", re.I)

def fetch_preview(channel):
    import urllib.request
    req = urllib.request.Request(f"https://t.me/s/{channel}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")

def fetch_all_parallel(channels):
    """Fetch all channel previews in parallel threads (was sequential ~9s -> now ~2-3s)."""
    from concurrent.futures import ThreadPoolExecutor
    results = {}
    def _get(ch):
        try:
            return ch, fetch_preview(ch)
        except Exception as e:
            return ch, None
    with ThreadPoolExecutor(max_workers=len(channels)) as ex:
        for ch, html in ex.map(_get, channels):
            results[ch] = html
    return results

def parse_msgs(html):
    """Split preview HTML into message blocks, extract text + links + datetime."""
    blocks = re.split(r'tgme_widget_message_wrap', html)[1:]
    out = []
    for b in blocks:
        text = re.sub(r'<[^>]+>', ' ', b)
        text = text.replace('&#036;', '$').replace('&lrm;', '').replace('&amp;', '&')
        text = re.sub(r'\s+', ' ', text).strip()
        dt = re.search(r'datetime="([^"]+)"', b)
        # hrefs from buttons/inline links (CA often hidden in DexScreener/GMGN/Solscan URLs)
        hrefs = re.findall(r'href="([^"]+)"', b)
        out.append({"text": text[:600], "dt": dt.group(1) if dt else None,
                    "hrefs": hrefs, "photo": 'tgme_widget_message_photo' in b})
    return out

def extract_call(msg):
    """From a message -> candidate call dict or None. Scans text AND link URLs."""
    # mints from visible text
    text_mints = [m for m in MINT_RE.findall(msg["text"])
                  if not m.startswith("0x") and len(m) >= 32 and not m.isdigit()]
    # mints from hrefs: dexscreener/solscan/gmgn/jup/rugcheck style URLs + bare base58 paths
    mints = list(text_mints)
    for h in msg.get("hrefs", []):
        mints += [m for m in MINT_RE.findall(h)
                  if not m.startswith("0x") and len(m) >= 32 and not m.isdigit()]
    # dedupe preserving order
    seen_m = set(); mints = [m for m in mints if not (m in seen_m or seen_m.add(m))]
    if not mints:
        return None
    sym = SYM_RE.search(msg["text"])
    mc = MC_RE.search(msg["text"])
    age = AGE_RE.search(msg["text"])
    return {"mint": mints[0], "all_mints": mints[:4], "symbol": sym.group(1) if sym else None,
            "mc_str": mc.group(1) if mc else None, "age": age.group(1) if age else None,
            "from_link": mints[0] not in text_mints,
            "text": msg["text"], "dt": msg["dt"]}

def numify(mc_str):
    if not mc_str: return None
    s = mc_str.replace(",", "").replace("$", "").strip().upper()
    mult = 1
    if s.endswith("B"): mult = 1e9; s = s[:-1]
    elif s.endswith("M"): mult = 1e6; s = s[:-1]
    elif s.endswith("K"): mult = 1e3; s = s[:-1]
    try: return float(s) * mult
    except Exception: return None

def crawl_channel(channel, seen):
    """Returns new calls (mint not seen before) with DexScreener confirmation."""
    try:
        html = fetch_preview(channel)
    except Exception as e:
        print(f"  [{channel}] fetch error: {e}")
        return []
    found = []
    for msg in parse_msgs(html):
        c = extract_call(msg)
        if not c or c["mint"] in seen:
            continue
        # Confirm it's a real tradeable token (dedupe false positives)
        try:
            pair = dex_pair(c["mint"])
        except Exception:
            pair = None
        if not pair:
            continue
        c["mc"] = numify(c["mc_str"]) or (pair.get("marketCap") or 0)
        c["symbol"] = c["symbol"] or (pair.get("baseToken") or {}).get("symbol")
        c["price"] = pair.get("priceUsd")
        c["liq"] = (pair.get("liquidity") or {}).get("usd")
        c["channel"] = channel
        found.append(c)
    return found

def crawl_all(scan=True, verbose=True, max_scan=None):
    """max_scan: cap AEGIS scans per crawl (unscanned calls still logged; next tick picks them up
    because their alert loop re-runs via scan_log dedupe... they are scanned on the next crawl_all
    that sees them still unscanned in call_metrics)."""
    init_schema()
    con = db()
    seen = {r["mint"] for r in con.execute("SELECT mint FROM coins").fetchall()}
    con.close()
    new_calls = []
    # FIX #1: parallel fetch (~9s -> ~2-3s)
    import os as _os
    htmls = fetch_all_parallel(CHANNELS)
    for ch in CHANNELS:
        html = htmls.get(ch)
        if html is None:
            if verbose:
                print(f"[{ch}] fetch error")
            continue
        got = []
        for msg in parse_msgs(html):
            c = extract_call(msg)
            if not c or c["mint"] in seen:
                continue
            try:
                pair = dex_pair(c["mint"])
            except Exception:
                pair = None
            if not pair:
                continue
            c["mc"] = numify(c["mc_str"]) or (pair.get("marketCap") or 0)
            c["symbol"] = c["symbol"] or (pair.get("baseToken") or {}).get("symbol")
            c["price"] = pair.get("priceUsd")
            c["liq"] = (pair.get("liquidity") or {}).get("usd")
            c["channel"] = ch
            got.append(c)
        if verbose:
            print(f"[{ch}] {len(got)} new candidate(s)")
        new_calls.extend(got)
    # Dedupe across channels + CONFLUENCE: count how many channels called each mint
    by_mint = {}
    for c in new_calls:
        prev = by_mint.get(c["mint"])
        if prev:
            chans = set(prev.get("channels") or [prev["channel"]]) | {c["channel"]}
            if (c.get("mc") or 0) > (prev.get("mc") or 0):
                c["channels"] = sorted(chans)
                by_mint[c["mint"]] = c
            else:
                prev["channels"] = sorted(chans)
        else:
            c["channels"] = [c["channel"]]
            by_mint[c["mint"]] = c
    # Cross-crawl confluence: same mint re-called later by another channel (in calls.jsonl history)
    hist_chans = {}
    try:
        with open(__import__("desk_common").CALLS_FILE) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    m = d.get("mint")
                    if m and d.get("channel"):
                        hist_chans.setdefault(m, set()).add(d["channel"])
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    final = list(by_mint.values())
    for c in final:
        allch = set(c["channels"]) | hist_chans.get(c["mint"], set())
        c["channels"] = sorted(allch)
        c["confluence"] = len(allch)
        if c["confluence"] >= 2:
            print(f"  🔥 CONFLUENCE: {c['mint'][:10]}... called by {c['confluence']} channels: {', '.join(c['channels'])}")
    # scan budget is per-crawl, reset at loop start
    # FIX #2: freshness triage — fresh calls (<10m old post) scan first, backlog defers
    def _post_age(c):
        dt = c.get("dt")
        if not dt:
            return 1e9
        try:
            t = time.mktime(time.strptime(dt[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            return time.time() - t
        except Exception:
            return 1e9
    final.sort(key=_post_age)  # freshest first
    FRESH_MS = 600  # 10 minutes
    scanned = 0
    for c in final:
        with open(__import__("desk_common").CALLS_FILE, "a") as f:
            f.write(json.dumps({"ts": time.time(), **c}) + "\n")
        log_event({"event": "CALL_RECEIVED", "mint": c["mint"], "symbol": c["symbol"],
                   "mc": c.get("mc"), "channel": c["channel"]})
        if scan:
            if max_scan is not None and scanned >= max_scan:
                c["score"] = None; c["verdict"] = "DEFERRED"; c["strategy"] = None
                continue
            if _post_age(c) > FRESH_MS and max_scan is not None:
                # backlog: only scan if we have spare budget
                c["score"] = None; c["verdict"] = "DEFERRED"; c["strategy"] = None
                continue
            try:
                rep = aegis.score_coin(c["mint"], deep=False)
                scanned += 1
                c["score"] = rep["score"]; c["verdict"] = rep["verdict"]; c["strategy"] = rep["strategy"]
                c["live_mc"] = rep.get("mc")
            except Exception as e:
                c["score"] = None; c["verdict"] = "SCAN_ERR"; c["strategy"] = None
                print(f"  scan error {c['mint'][:12]}: {e}")
            # mc_delta_pct: how far the call is below/above live MC (per-channel latency stat)
            if c.get("mc") and c.get("live_mc"):
                c["mc_delta_pct"] = round((c["live_mc"] - c["mc"]) / c["mc"] * 100, 1)
            else:
                c["mc_delta_pct"] = None
            # playbook card + smart-money overlay (curated calls ONLY, never signal generation)
            try:
                from signals import token_intel
                intel = token_intel(c["mint"])
                c["fill_class"] = intel.get("fill_class")
                c["fill_min"] = intel.get("fill_min")
                c["fragility"] = intel.get("fragility")
            except Exception:
                pass
            try:
                ov = aegis.fomo_overlay(c["mint"])
                c["smart_money"] = ov.get("tagged", [])
                c["smart_money_count"] = ov.get("smart_money_count", 0)
            except Exception:
                c["smart_money"] = []; c["smart_money_count"] = 0
            _record_call_metrics(c)
    return final

def _record_call_metrics(c):
    """Persist the playbook card -> outcome attribution (Tier 2.1)."""
    try:
        import json as _json
        con = db()
        ave = None; top10 = None; bq = None
        try:
            from desk_common import ave_risk
            ar = ave_risk(c["mint"]) or {}
            ave = ar.get("risk_score")
        except Exception:
            pass
        try:
            from signals import fragility_score
            fr = fragility_score(c["mint"])
            top10 = (fr or {}).get("top10_pct")
        except Exception:
            pass
        pair = dex_pair(c["mint"])
        if pair:
            txns = pair.get("txns") or {}
            m5 = txns.get("m5") or {}
            c["buys5"] = m5.get("buys") or 0
            c["sells5"] = m5.get("sells") or 0
        flags = _json.dumps(c.get("flags", [])) if isinstance(c.get("flags"), list) else None
        con.execute("""INSERT OR REPLACE INTO call_metrics(mint, symbol, channel, call_ts, mc, liq, fill_class, fill_min,
                       aegis_score, verdict, flags_json, ave_risk_score, top10_pct, smart_money_count,
                       buy_sell_b5, buys5, sells5, vol_q_flags, mc_delta_pct, price, created_ts)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (c["mint"], c.get("symbol"), c.get("channel"), time.time(),
                     c.get("mc"), c.get("liq"), c.get("fill_class"), c.get("fill_min"),
                     c.get("score"), c.get("verdict"), flags, ave, top10, c.get("smart_money_count", 0),
                     None, c.get("buys5"), c.get("sells5"), None, c.get("mc_delta_pct"),
                     c.get("price"), time.time()))
        con.commit(); con.close()
    except Exception as e:
        print(f"  metrics err {c['mint'][:12]}: {e}")

def _call_age_str(c):
    """How long ago the call was posted in the channel (human string)."""
    dt = c.get("dt")
    if not dt:
        return None
    try:
        t = time.mktime(time.strptime(dt[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        mins = (time.time() - t) / 60
        stamp = time.strftime("%H:%M", time.gmtime(t)) + " UTC / " + time.strftime("%H:%M", time.localtime(t)) + " CST"
        return f"{stamp} ({mins:.0f}m ago)"
    except Exception:
        return dt[:16] + "Z"


def desk_alert(c):
    print("\n" + "=" * 58)
    conf = c.get("confluence") or 1
    flame = "🔥" * min(conf, 5) if conf >= 2 else "📡"
    print(f"{flame} NEW CALL: ${c.get('symbol') or '?'}  via {c.get('channel')}")
    if conf >= 2:
        print(f"   ⭐ CONFLUENCE: {conf} channels — {', '.join(c.get('channels') or [])}")
    print(f"   mint: {c['mint']}")
    age = _call_age_str(c)
    if age:
        print(f"   posted in channel: {age}")
        if c.get("age"):
            print(f"   age at call time: {c['age']}")
    if c.get("mc"): print(f"   MC (call): ${c['mc']:,.0f}")
    if c.get("live_mc") is not None:
        print(f"   MC (live): ${c['live_mc']:,.0f}")
        if c.get("mc_delta_pct") is not None:
            print(f"   MC delta: {c['mc_delta_pct']:+.1f}% from call")
        if c.get("mc") and c["live_mc"] < c["mc"] * 0.5:
            print(f"   ⚠ STALE CALL: live MC <50% of call MC — channel likely late, token dumping")
    if c.get("price"): print(f"   px: ${c['price']}")
    if c.get("age"): print(f"   age (call): {c['age']}")
    if c.get("fill_class"): print(f"   slow-grad: {c['fill_class']}")
    if c.get("score") is not None: print(f"   AEGIS: {c['score']}/100 [{c['verdict']}] strategy={c['strategy']}")
    if c.get("smart_money_count"): print(f"   🐋 smart-money holders: {c['smart_money_count']}")
    if c.get("smart_money"):
        for t in c["smart_money"][:3]:
            print(f"      - {t.get('handle')} [{t.get('class')}] pnl7d=${t.get('pnl_7d',0):,}")
    print("=" * 58)

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    chans = [only] if only else CHANNELS
    print(f"Crawling {len(chans)} channel(s)...")
    new = crawl_all(scan=True)
    if not new:
        print("No new calls (all seen).")
    for c in new:
        desk_alert(c)

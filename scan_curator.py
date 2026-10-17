#!/usr/bin/env python3
"""scan_curator.py — ONE curated HMQuant card per CA, aggregating EVERY scan bot.

Scanner bots respond when a HUMAN posts a CA in the HMQ group. We collect ALL cards
(wide window + wide pull), parse every field per bot, tally a consensus, merge with
AEGIS, and post ONE curated review card.

BOT AUTO-DISCOVERY: no hardcoded bot list is REQUIRED. Any non-own message that
(a) mentions the CA or (b) appears in the collection window and LOOKS like a scanner
card is collected. Unknown senders are parsed with the generic extractor and registered
into hmq_bots.json (first-seen, card sample) so they are remembered next time.
Specialist parsers exist for the known formats; generic covers everything else.

Never an entry source — analysis/second-opinion only.
No getUpdates on the bot token (gateway owns polling); Telethon human account reads,
sendMessage posts.
"""
import asyncio, json, os, re, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
sys.path.insert(0, DESK)
STATE = os.path.join(DESK, "hmq_curator_state.json")
BOTS_FILE = os.path.join(DESK, "hmq_bots.json")
GROUP = "HMQcheckergroup"
COLLECT_WINDOW = 45        # seconds to wait for scanner cards (bots reply 15-45s; was 90 — cuts latency)
PULL_LIMIT = 500           # wide window so late cards are still caught
MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
SOL_MINT = "So11111111111111111111111111111111111111112"
OWN_BOTS = {"HMQuant_botbot", "treizodemayo"}

# ── known-bot registry (learned, persisted to hmq_bots.json) ─────────────────
_registry = None
def load_bots():
    global _registry
    if _registry is None:
        try: _registry = json.load(open(BOTS_FILE))
        except Exception: _registry = {}
        # seed with specialists first seen in this environment
        for b in ("apecheck_scan_bot", "DeFadeAnalyzerBot", "soul_scanner_bot", "ttfbotbot",
                  "MASTRtrade_bot", "ProficyPriceBot", "Phanes_bot", "GMGNAI_bot",
                  "Alphape_bot", "6126376117", "PIRBView"):
            _registry.setdefault(b.lower(), {"role": "scanner", "first_seen": 0})
        try: json.dump(_registry, open(BOTS_FILE, "w"), indent=1)
        except Exception: pass
    return _registry

def remember_bot(sender, sample=""):
    if not sender or sender in OWN_BOTS: return
    key = sender.lower()
    r = load_bots()
    if key not in r:
        r[key] = {"role": "scanner", "first_seen": time.time(), "sample": sample[:200]}
        try: json.dump(r, open(BOTS_FILE, "w"), indent=1)
        except Exception: pass

def bot_display(sender):
    s = (sender or "?").strip()
    if len(s) > 24: s = s[:21] + "…"
    return s

# ── number/flags helpers ──────────────────────────────────────────────────────
def _num(pat, text, cast=float):
    """Parse a number with optional suffix: 2.36K -> 2360, 1.2M -> 1200000."""
    m = re.search(pat, text, re.I)
    if not m: return None
    s = m.group(1).replace(",", "").replace("$", "").strip()
    mult = 1.0
    if s.endswith("K"): mult = 1000.0; s = s[:-1]
    elif s.endswith("M"): mult = 1e6; s = s[:-1]
    try: return cast(s) * mult
    except Exception: return None

def _flag(kw, text):
    return kw.lower() in text.lower()

CARD_MARKERS = ["/100", "holders", "mc:", "liq:", "liq ", "vol:", "rug", "bundled",
                "wash", "mutable", "deployer", "renounce", "burned", "fdv", "ath",
                "buy", "sell", "sniper", "price", "chart", "ca:",
                "$", "%", "🐋", "🦍", "🔴", "🟡", "🟢", "🚨", "⚠", "📊", "💊", "🪙", "👥", "📈", "💎"]
EMOJI_HEAVY = ["🦍","🔮","🍃","🧲","🧰","☯️","🪙","💊","🔁","🥷","🐍","🦈","👽","🤖","🐸","🦜","🐳","💉"]

def looks_like_card(text, sender=None):
    """Heuristic: scanner cards are dense with metrics/emoji, unlike chat."""
    low = text.lower()
    hits = sum(1 for m in CARD_MARKERS if m.lower() in low)
    if hits >= 3: return True
    if sender and sender.lower() in load_bots():
        return hits >= 1
    if any(e in text for e in EMOJI_HEAVY) and hits >= 2: return True
    return False

# ── generic extractor: works on ANY bot format ────────────────────────────────
def parse_generic(text, sender="?"):
    d = {"bot": bot_display(sender), "verdict": None, "score": None, "_generic": True}
    m = re.search(r"(\d{1,3})\s*/\s*100", text)
    if m: d["score"] = int(m.group(1))
    m2 = re.search(r"Rug probability\s*(\d{1,3})%", text)
    if m2 and d["score"] is None: d["score"] = 100 - int(m2.group(1))
    d["rug_prob"] = _num(r"Rug probability\s*([\d]+)%", text, int)
    if d["rug_prob"] is None:
        m = re.search(r"[Rr]ug[^%]{0,12}([0-9]{1,3})%", text)
        if m: d["rug_prob"] = int(m.group(1))
    d["mc"] = _num(r"\bMC(?:ap)?[:=]?\s*\$?([\d,.]+[KM]?)", text)
    if d["mc"] is None: d["mc"] = _num(r"MC\s*\$?([\d,.]+[KM]?)", text)
    d["liq"] = _num(r"Liq(?:uidity)?[:=]?\s*\$?([\d,.]+[KM]?)", text)
    d["vol"] = _num(r"Vol(?:ume)?[:=]?\s*\$?([\d,.]+[KM]?)", text)
    d["vol_1h"] = _num(r"Vol\s*\$?([\d,.]+[KM]?)\s*\(?1h\)?", text)
    d["holders"] = _num(r"([\d,.]+)\s*holders", text)
    d["top10"] = _num(r"[Tt]op\s?10[^%]{0,10}([\d.]+)%", text)
    d["dev_bundled"] = _num(r"[Dd]ev[^%]{0,14}(?:bundled|holds|bought)[^%]{0,10}([0-9]{1,3})%", text)
    d["ath"] = _num(r"ATH[:=]?\s*\$?([\d,.]+[KM]?)", text)
    d["price"] = _num(r"(\$0\.0+\d+)", text)
    d["age"] = re.search(r"Age[:\s]*([\d.]+[hmdw])", text)
    low = text.lower()
    d["flags"] = []
    for kw in ("mutable metadata", "renounced", "burned", "wash trading", "volume bot",
               "dev bundled", "no kol or smart money", "no reliable price", "rugged",
               "honeypot", "transfer fee", "fake", "cartel", "sniper", "bundled"):
        if kw in low:
            d["flags"].append(kw)
    d["flags"] = d["flags"][:6]
    d["b_s"] = re.search(r"B(?:\s*/\s*S|:)?\s*[:/]\s*([\d,.]+[KM]?)\s*[/:]\s*([\d,.]+[KM]?)", text)
    if "rugged" in low or "dead" in low or "critical" in low: d["verdict"] = "BEARISH"
    elif "moderate" in low: d["verdict"] = "NEUTRAL"
    elif d["score"] is not None and d["score"] >= 60: d["verdict"] = "NEUTRAL"
    elif d["score"] is not None: d["verdict"] = "BEARISH"
    elif d.get("dev_bundled") is not None and d["dev_bundled"] >= 50: d["verdict"] = "BEARISH"
    return d

# ── specialist parsers (known formats; base = generic + extras) ──────────────
def parse_apecheck(text, sender=None):
    del sender
    d = parse_generic(text, "apecheck")
    d["top10"] = _num(r"Top 10 wallets hold ([\d.]+)%", text)
    return d

def parse_defade(text, sender=None):
    del sender
    d = parse_generic(text, "DeFade")
    d["rug_prob"] = _num(r"Rug probability ([\d]+)%", text, int)
    d["top5"] = _num(r"Top 5: ([\d.]+)%", text)
    d["top10"] = _num(r"Top 10: ([\d.]+)%", text)
    d["top20"] = _num(r"Top 20: ([\d.]+)%", text)
    d["mint_auth"] = "revoked" if _flag("mint authority: ✅ revoked", text) else None
    d["freeze_auth"] = "revoked" if _flag("freeze authority: ✅ revoked", text) else None
    d["wallets"] = re.findall(r"▸\s*\d+\.\s*([A-Za-z0-9]{4,})\.\.\.([A-Za-z0-9]{4,})\s*[—-]\s*([\d.]+)%", text)[:5]
    if d["rug_prob"] is not None: d["score"] = 100 - d["rug_prob"]
    if d["verdict"] is None and d["rug_prob"] is not None and d["rug_prob"] >= 50: d["verdict"] = "BEARISH"
    return d

def parse_soul(text, sender=None):
    del sender
    d = parse_generic(text, "soul_scanner")
    d["dev_bundled"] = _num(r"Dev Bundled ([\d.]+)%", text)
    return d

def parse_ttf(text, sender=None):
    del sender
    d = parse_generic(text, "ttf")
    d["mutable_metadata"] = _flag("mutable metadata", text)
    return d

def parse_mastr(text, sender=None):
    del sender
    d = parse_generic(text, "MASTR")
    m = re.search(r"(Critical|High|Medium|Low)\s*risk level", text, re.I)
    d["risk_level"] = m.group(1).lower() if m else None
    m2 = re.search(r"(\d+) of (\d+) checks passed", text)
    d["checks_passed"] = int(m2.group(1)) if m2 else None
    return d

def parse_proficy(text, sender=None):
    del sender
    d = parse_generic(text, "Proficy")
    d["chg_5m"] = re.search(r"5M[^\d]*([\d.+-]+)%", text)
    d["chg_1h"] = re.search(r"1H[^\d]*([\d.+-]+)%", text)
    d["chg_1d"] = re.search(r"1D[^\d]*([\d.+-]+)%", text)
    return d

def parse_phanes(text, sender=None):
    del sender
    d = parse_generic(text, "Phanes")
    d["liq_burned"] = _flag("100% burned", text)
    return d

def parse_gmgn(text, sender=None):
    del sender
    d = parse_generic(text, "GMGN")
    d["sol_pooled"] = _num(r"SOL pooled[:=]?\s*([\d.]+)", text)
    d["pump_to_raydium"] = _flag("pump→raydium", text) or _flag("pump->raydium", text)
    return d

def parse_alphape(text, sender=None):
    del sender
    d = parse_generic(text, "Alphape")
    d["no_smart_money"] = _flag("no kol or smart money", text)
    return d

def parse_rick(text, sender=None):
    del sender
    d = parse_generic(text, "Rick")
    d["first_you"] = _flag("you are first", text)
    d["best_mult"] = re.search(r"best:\s*([\d.]+)x", text)
    d["holder_age_avg"] = re.search(r"avg\s*([\d]+w) old", text)
    d["fresh_1d"] = _num(r"Fresh 1D:\s*([\d.]+)%", text)
    d["fresh_7d"] = _num(r"7D:\s*([\d.]+)%", text)
    return d

SPECIALISTS = {"apecheck_scan_bot": parse_apecheck, "defadeanalyzerbot": parse_defade,
               "soul_scanner_bot": parse_soul, "ttfbotbot": parse_ttf,
               "mastrtrade_bot": parse_mastr, "proficypricebot": parse_proficy,
               "phanes_bot": parse_phanes, "gmgna1_bot": parse_gmgn,
               "alphape_bot": parse_alphape, "6126376117": parse_rick,
               "pirbview": parse_generic}

def parse_card(text, sender="?"):
    fn = SPECIALISTS.get((sender or "").lower(), parse_generic)
    return fn(text, sender)

# ── Telethon helpers ──────────────────────────────────────────────────────────
def creds():
    return json.load(open(f"{DESK}/.tg_credentials.json"))

async def _client(session="tg_session_curator"):
    from telethon import TelegramClient
    # dedicated session file so the curator never fights the listener over tg_session
    c = TelegramClient(f"{DESK}/{session}", creds()["api_id"], creds()["api_hash"])
    await c.connect()
    if not await c.is_user_authorized():
        raise RuntimeError("NOT_AUTHORIZED")
    return c

async def collect_for_mint(client, mint, limit=PULL_LIMIT, after_ts=None):
    """ALL scanner messages about the mint: mentions the CA OR any card-looking message
    in the collection window from a non-own sender (auto-discovers new bots)."""
    g = await client.get_entity(GROUP)
    msgs = await client.get_messages(g, limit=limit)
    out = []
    mint_p = mint[:12]
    mint_head = mint[:4]
    trigger = float(after_ts or 0)
    for m in msgs:
        txt = m.message or ""
        if not txt: continue
        ts = m.date.timestamp() if m.date else 0
        contains = mint_p in txt
        sender = None
        try:
            if m.sender_id:
                sender = (await client.get_entity(m.sender_id)).username or str(m.sender_id)
        except Exception:
            sender = m.sender_id
        if sender in OWN_BOTS: continue
        if contains:
            remember_bot(sender, txt)
            out.append({"id": m.id, "sender": sender, "text": txt,
                        "date": m.date.isoformat() if m.date else None, "ts": ts})
            continue
        # discovery path: within the collection window, card-looking, non-own sender
        if ts and trigger and trigger - 30 <= ts <= trigger + 300:
            if mint_head in txt or looks_like_card(txt, sender):
                remember_bot(sender, txt)
                out.append({"id": m.id, "sender": sender, "text": txt,
                            "date": m.date.isoformat() if m.date else None, "ts": ts})
    return out

# ── curation ──────────────────────────────────────────────────────────────────
def _forensics_block(mint, rep):
    """AEGIS deep-scan section (was the gateway's narrative — now deterministic)."""
    aeg = rep or {}
    checks = aeg.get("checks") or {}
    risks = checks.get("risks") or {}
    auth = checks.get("authorities") or {}
    market = checks.get("market") or {}
    score = aeg.get("score")
    verdict = aeg.get("verdict") or ""
    strat = aeg.get("strategy") or "default"
    flags = aeg.get("flags") or []

    lines = [f"🛡 AEGIS: {score or 'n/a'}/100 — {verdict} · strategy={strat}"]
    if flags:
        shown = flags[:6]
        for f in shown:
            lines.append(f"  ⚠ {f}")
    if aeg.get("deployer"):
        lines.append(f"  👨‍💻 deployer {aeg['deployer'][:8]}…{aeg['deployer'][-4:]}")
    # market tape
    mc = market.get("mc") or aeg.get("mc")
    liq = market.get("liq") or aeg.get("liq")
    vol = market.get("vol_h24")
    b24, s24 = market.get("buys24"), market.get("sells24")
    bits = []
    if mc: bits.append(f"MC ${mc:,.0f}")
    if liq: bits.append(f"liq ${liq:,.0f}")
    if vol: bits.append(f"vol ${vol:,.0f}")
    if b24 is not None or s24 is not None:
        bits.append(f"B/S {b24 or 0}/{s24 or 0}")
    if bits:
        lines.append("  📊 " + " · ".join(bits))
    # holder concentration
    top10 = risks.get("top10_pct")
    top1 = None
    th = risks.get("topHolders") or []
    if th and isinstance(th, list):
        try: top1 = float(th[0].get("pct")) if th[0].get("pct") else None
        except Exception: top1 = None
    hbits = []
    if top10 is not None: hbits.append(f"T10 {top10:.1f}%")
    if top1 is not None: hbits.append(f"top1 {top1:.1f}%")
    if hbits:
        lines.append("  👥 " + " · ".join(hbits))
    # authorities
    abits = []
    if "mint_authority" in auth: abits.append(f"mint {'PRESENT' if auth['mint_authority'] else 'revoked'}")
    if "freeze_authority" in auth: abits.append(f"freeze {'PRESENT' if auth['freeze_authority'] else 'revoked'}")
    if abits:
        lines.append("  🔏 " + " · ".join(abits))
    # action plan (rule-based, mirrors the desk playbook)
    plan = None
    if verdict == "RUGGED" or (score is not None and score < 30):
        plan = "🚫 AVOID — contract verdict overrides tape"
    else:
        insider = any("insider" in (f or "") for f in flags)
        lp_risk = any("lp_" in (f or "") or "unlocked" in (f or "") for f in flags)
        if intent := insider or lp_risk:
            plan = ("flip sizing · stop −22% · clip +30% fast · whale-watch if "
                    + ("insider + " if insider else "") + ("partial LP" if lp_risk else "top holder sells"))
        elif score is not None and score >= 60:
            plan = "conviction-candidate: scale slower, respect narrative stop"
        else:
            plan = "default flip: stop −22%, first clip +30%"
    if plan:
        lines.append("  🧭 " + plan)
    return "\n".join(lines)

def _card_bits(d):
    bits = []
    if d.get("rug_prob") is not None: bits.append(f"rug {d['rug_prob']:.0f}%")
    if d.get("dev_bundled") is not None: bits.append(f"dev {d['dev_bundled']:.0f}%")
    if d.get("top10") is not None: bits.append(f"T10 {d['top10']:.0f}%")
    if d.get("liq") is not None: bits.append(f"liq ${d['liq']:,.0f}")
    if d.get("mc") is not None: bits.append(f"mc ${d['mc']:,.0f}")
    if d.get("vol_1h") is not None: bits.append(f"v1h ${d['vol_1h']:,.0f}")
    if d.get("vol") is not None: bits.append(f"vol ${d['vol']:,.0f}")
    if d.get("holders") is not None: bits.append(f"{d['holders']:.0f}h")
    if d.get("mutable_metadata"): bits.append("MUTABLE MD")
    if d.get("mint_auth") == "revoked": bits.append("mint ok")
    if d.get("wallets"): bits.append(f"{len(d['wallets'])} addrs")
    if d.get("risk_level"): bits.append(f"risk:{d['risk_level']}")
    if d.get("checks_passed"): bits.append(f"{d['checks_passed']}/40")
    if d.get("price") is not None: bits.append(f"px {d['price']:.3e}")
    if d.get("sol_pooled") is not None: bits.append(f"SOL {d['sol_pooled']:.2f}")
    if d.get("first_you"): bits.append("you early")
    if d.get("holder_age_avg"): bits.append(f"h-age {d['holder_age_avg'].group(1)}")
    if d.get("liq_burned"): bits.append("LP burned")
    if d.get("no_smart_money"): bits.append("no SM")
    g = d.get("b_s")
    if g:
        bits.append(f"B/S {g.group(1)}/{g.group(2)}")
    for f in (d.get("flags") or [])[:2]:
        if f.upper() not in " ".join(bits).upper():
            bits.append(f.upper())
    return bits[:9]

def curate_card(mint, msgs, aegis_rep, channel=None):
    """ONE card: every scanned bot parsed (known + discovered) + consensus + AEGIS.
    channel = source Telegram channel of the original call (confluence attribution)."""
    by_bot = {}
    for m in msgs:
        sender = m.get("sender") or "?"
        if sender in OWN_BOTS: continue
        d = parse_card(m["text"], sender)
        key = (sender or "?").lower()
        cur = by_bot.get(key)
        # keep the richest parse for this sender
        if cur is None or len([v for v in d.values() if v not in (None, [], "")]) > len([v for v in cur.values() if v not in (None, [], "")]):
            d["_sender"] = sender
            by_bot[key] = d
    parsed = list(by_bot.values())
    verdicts = [d["verdict"] for d in parsed if d.get("verdict")]
    bear = verdicts.count("BEARISH"); neut = verdicts.count("NEUTRAL")
    scores = [d["score"] for d in parsed if d.get("score") is not None]
    avg_bot = round(sum(scores)/len(scores)) if scores else None
    aeg = aegis_rep or {}
    aeg_score = aeg.get("score")
    merged = aeg_score if aeg_score is not None else avg_bot
    if merged is None: merged = 50
    if merged < 30: verdict = "🚫 AVOID"
    elif merged < 55: verdict = "⚠️ CAUTION"
    elif bear >= 3: verdict = "⚠️ CAUTION (scanner consensus bearish)"
    else: verdict = "✅ PROCEED"
    strong = "🔥 STRONG" if bear >= 3 else ("⚡ MODERATE" if bear == 2 else ("✨ WEAK" if (bear + neut) >= 1 else "—"))

    lines = [f"🧠 HMQuant curated scan — ${aeg.get('symbol') or mint[:10]}",
             "━━━━━━━━━━━━━━━━━━"]
    if channel:
        lines.append(f"📡 source: {channel}")
    lines.append(f"💠 CA: {mint}")
    forensics = _forensics_block(mint, aeg)
    if forensics:
        lines.append(forensics)
        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("🔎 SCANNER CONSENSUS:")
    for d in sorted(parsed, key=lambda x: (x.get("verdict") == "BEARISH", -(x.get("score") or 99))):
        v = {"BEARISH": "🔴", "NEUTRAL": "🟡"}.get(d.get("verdict"), "⚪")
        sc = f"{d.get('score')}/100" if d.get("score") is not None else ""
        bits = _card_bits(d)
        extra = (" · " + " · ".join(bits)) if bits else ""
        lines.append(f"  {v} {d.get('bot','?')}: {sc}{extra}")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 {len(parsed)} scanners · 🔴{bear} 🟡{neut} · bot avg {avg_bot or 'n/a'}/100 · {strong}")
    lines.append(f"🧭 VERDICT: {verdict}")
    return "\n".join(lines)


async def run_once(mint):
    """Post CA as human -> collect ALL -> curate -> post ONE card via HMQuant."""
    from desk_common import load_config, http_json
    client = await _client("tg_session_curator_once")  # own session copy: no lock vs daemon
    try:
        me = await client.get_me()
        g = await client.get_entity(GROUP)
        await client.send_message(g, f"scan this: {mint}")
        post_ts = time.time()  # audit F10: pass trigger so auto-discovery branch works
        print(f"posted as human ({me.username or me.id}), waiting {COLLECT_WINDOW}s…", flush=True)
        await asyncio.sleep(COLLECT_WINDOW)
    finally:
        await client.disconnect()
    client = await _client("tg_session_curator_once")  # own session copy: no lock vs daemon
    try:
        msgs = await collect_for_mint(client, mint, after_ts=post_ts)
        print(f"collected {len(msgs)} messages mentioning {mint[:12]}", flush=True)
    finally:
        await client.disconnect()
    import aegis
    rep = aegis.score_coin(mint, deep=True)  # deep: deployer memory + full checks
    card = curate_card(mint, msgs, rep)
    print(card, flush=True)
    tok = load_config().get("HMQ_BOT_TOKEN")
    if tok:
        st, _ = http_json(f"https://api.telegram.org/bot{tok}/sendMessage", method="POST",
                          body={"chat_id": -1004430296219, "text": card},
                          headers={"Content-Type": "application/json"}, retries=1)
        print(f"posted curated card to group: {st}", flush=True)
        st2, _ = http_json(f"https://api.telegram.org/bot{tok}/sendMessage", method="POST",
                           body={"chat_id": 1223241788, "text": card},
                           headers={"Content-Type": "application/json"}, retries=1)
        print(f"sent curated card to DM via bot: {st2}", flush=True)
    return card


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "once":
        asyncio.run(run_once(sys.argv[2]))
    else:
        from scan_curator_daemon import run_daemon
        asyncio.run(run_daemon())
#!/usr/bin/env python3
"""telethon_listener.py — authenticated MTProto Telegram signal listener (doc-2 §2).

Replaces t.me/s/ preview polling where the account has access (incl. private
channels the account is a member of, e.g. signalsolanaby4am).

Design (doc 2):
  - allowlist only (curated channels) — never generates entries beyond the pipeline
  - preserves channel id, message id, timestamp, text, edits
  - extracts mints (text + href/embedded URLs), dedupes vs calls.jsonl
  - writes to telegram_messages.jsonl (full ledger) + calls.jsonl (pipeline)
  - reconnect + catch-up: on start, fetches messages since last seen msg id

Run: .venv/bin/python telethon_listener.py   (daemon; also `backfill` mode)
Credentials: ~/.memecoin-quant/.tg_credentials.json (chmod 600, never in git)
Session: ~/.memecoin-quant/tg_session.session (grants account access — protect it)
"""
import os, sys, json, time, re, asyncio

DESK = os.path.expanduser("~/memecoin-quant")
CRED_FILE = os.path.join(DESK, ".tg_credentials.json")
SESSION_FILE = os.path.join(DESK, "tg_session")
MSG_LEDGER = os.path.join(DESK, "telegram_messages.jsonl")
CALLS = os.path.join(DESK, "calls.jsonl")
STATE_FILE = os.path.join(DESK, "telethon_state.json")

MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
SOL_MINT = "So11111111111111111111111111111111111111112"

def load_allowlist():
    """Curated channel usernames from CONFIG.md §Telegram Call Channels (only that section,
    so OUTPUT groups like HMQcheckergroup are never treated as signal sources)."""
    try:
        text = open(os.path.join(DESK, "CONFIG.md")).read()
        # take only the "## Telegram Call Channels" section (until the next "## ")
        sec = text.split("## Telegram Call Channels", 1)[1] if "## Telegram Call Channels" in text else text
        sec = sec.split("\n## ", 1)[0]
        chans = []
        # @mention form
        for m in re.finditer(r"@([A-Za-z0-9_]{4,})", sec):
            u = m.group(1)
            if u not in chans and not u.isdigit():
                chans.append(u)
        # t.me/<username> URL form (e.g. https://t.me/sol_trend)
        for m in re.finditer(r"t\.me/([A-Za-z0-9_]{4,})", sec):
            u = m.group(1)
            if u not in chans and not u.isdigit():
                chans.append(u)
        return chans
    except Exception:
        return []

def valid_mint(s):
    return 32 <= len(s) <= 44 and s != SOL_MINT and not s.startswith("0x") and not s.isdigit()

def parse_mints(msg):
    """Extract the REAL CA(s) from a message, correctly.

    Rules (learned live Oct 17 — soltrench 'Launch New Trending' cards):
    - mints visible in the body TEXT are card starts (digest messages) — each is a call
    - URL mints: EXCLUDE solscan.io/account/ links (those are wallet addresses: creator,
      bundlers); count only token/chart/bot-start URL mints and take the DOMINANT one
      (the real CA repeats across bot/scan/chart links)
    Returns (text_mints, dominant_url_mint).
    """
    text = msg.message or ""
    text_mints = [m for m in MINT_RE.findall(text) if valid_mint(m)]
    # URL mints with context classification
    url_counts = {}
    for ent, ent_text in msg.get_entities_text():
        url = getattr(ent, "url", None) or ""
        if "/account/" in url:
            continue  # wallet address link, not a token
        for m in MINT_RE.findall(url):
            if valid_mint(m):
                url_counts[m] = url_counts.get(m, 0) + 1
    if msg.buttons:
        for row in msg.buttons:
            for b in row:
                u = getattr(b, "url", None) or ""
                if "/account/" in u:
                    continue
                for m in MINT_RE.findall(u):
                    if valid_mint(m):
                        url_counts[m] = url_counts.get(m, 0) + 1
    dominant = None
    if url_counts:
        dominant = max(url_counts, key=url_counts.get)
    seen, ded = set(), []
    for m in text_mints:
        if m not in seen:
            seen.add(m); ded.append(m)
    return ded, dominant


def parse_mc(s):
    """'$193.2K' -> 193200.0 ; '$1.5M' -> 1500000.0 ; '$2,500' -> 2500.0"""
    if not s:
        return None
    s = s.strip().replace('$', '').replace(',', '')
    mult = 1.0
    if s.endswith('K'): mult, s = 1e3, s[:-1]
    elif s.endswith('M'): mult, s = 1e6, s[:-1]
    elif s.endswith('B'): mult, s = 1e9, s[:-1]
    try:
        return float(s) * mult
    except Exception:
        return None


def parse_signal_fields(text):
    """Extract (symbol, mc, call_kind, skip) from a call message's body.

    call_kind: 'launch' | 'digest' | 'whale' | 'alert' | 'seekr' | 'none'
    skip: reason string if this message is NOT a fresh call (victory lap, rank
    digest, update) — "" means emit.

    Channel formats (audited live Oct 17):
      sol_trend            💊 <CA> ... MC: $X        (CA in text)
      solwhaletrending     🌊🐳 Another Whale Aped $SYM ... 💰 MC: $X   (CA in URLs)
      solearlytrending     🔥 X New Trending ... MC: $X                 (CA in URLs)
      PIRBViewTrending     N️⃣ New Trending Token! <CA> 🟣 $SYM 🧢 $26.50K (CA in text)
      soltrenchtrending    🔥 X Launch New Trending ... MC: $X          (CA in URLs)
      SeekrTrending        X is now on Seekr Trending ... CA: <mint> Market Cap: $X
      signalsolanaby4am    🔔Name | SYMBOL <CA> ... Marketcap: $X       (CA in text)
    """
    if not text:
        return None, None, 'none', 'empty'
    low = text.lower()
    # ---- noise / victory-lap / non-call filters (checked FIRST) ----
    if re.search(r"is up [\d.]+x?%?", low):
        return None, None, 'none', 'victory_lap'
    if re.search(r"x[\d.]+ in \d+", low):            # 🚀 x2.2 in 11m
        return None, None, 'none', 'victory_lap'
    if re.search(r"✅ [\d.]+x", low):                 # PIRB ✅ 2.1x $TOKEN
        return None, None, 'none', 'winner_lap'
    if "entry signal" in low or "join premium" in low or "premium" in low:
        return None, None, 'none', 'promo'
    if low.startswith("update for"):
        return None, None, 'none', 'update'
    if re.search(r"top \w+ trending", low) and re.search(r"•\s*[\d.]+x", low):
        # 🏆 Top Trench Trending — ranked leaderboard, no callable CA
        return None, None, 'none', 'rank_digest'
    # ---- call-type detection ----
    kind = 'none'
    if re.search(r"(launch )?new trending", low) or re.search(r"new trending token", low):
        kind = 'launch'
    elif "whale aped" in low:
        kind = 'whale'
    elif "🔔" in text:
        kind = 'alert'
    elif "now on seekr trending" in low:
        kind = 'seekr'
    elif text.startswith('💊') or "merge" in low or "pump.fun" in low:
        kind = 'launch'
    # ---- symbol extraction (priority) ----
    sym = None
    m = re.search(r"🔔[^|\n]*\|[ \u200e]*([A-Za-z0-9@.]+)", text)   # signalsolanaby4am
    if m:
        sym = m.group(1).upper()
    if not sym:
        m = re.search(r"\$([A-Z][A-Z0-9@._]{1,11})\b", text)         # $TICKER
        if m:
            sym = m.group(1)
    if not sym:
        m = re.search(r"([A-Za-z0-9@._]+)\s+(?:Launch New Trending|New Trending|is now on Seekr Trending)", text)
        if m:
            sym = re.sub(r'[^A-Za-z0-9@]', '', m.group(1)).upper()
    # ---- MC extraction (priority: channel-posted MC = call-time truth) ----
    mc = None
    m = re.search(r"(?:MC|Marketcap|Market Cap|🧢)\s*:?\s*\$([\d.,]+[KMB]?)", text, re.I)
    if m:
        mc = parse_mc(m.group(1))
    if kind == 'none' and not (sym and mc):
        return sym, mc, 'none', 'no_call_marker'
    return sym, mc, kind, ''


def block_fields(block, fallback_sym=None, fallback_mc=None):
    """Parse symbol + MC from a digest card block."""
    sym, mc = fallback_sym, fallback_mc
    m = re.search(r"\$([A-Z][A-Z0-9@._]{1,11})\b", block)
    if m:
        sym = m.group(1)
    else:
        m = re.search(r"┌(.+?)\s*\(([A-Z][A-Z0-9@._]{1,11})\)", block)
        if m:
            sym = m.group(2)
    m = re.search(r"(?:MC|Marketcap|Market Cap|🧢)\s*:?\s*\$([\d.,]+[KMB]?)", block, re.I)
    if m:
        mc = parse_mc(m.group(1))
    return sym, mc

def append_jsonl(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}

def save_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE_FILE)

def is_new_mint(mint):
    try:
        with open(CALLS) as f:
            for line in f:
                if f'"mint": "{mint}"' in line or f'"mint":"{mint}"' in line:
                    return False
    except FileNotFoundError:
        pass
    return True

def guess_symbol(text):
    """Best-effort ticker from message text: $SYMBOL / SYMBOL: / bracketed ticker.
    Requires letter-first, 3+ chars — avoids grabbing amounts like $45 or $5K."""
    if not text:
        return None
    m = re.search(r"\$([A-Z][A-Z0-9]{2,11})\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\bSymbol\s*[:=]\s*([A-Z][A-Z0-9]{2,11})\b", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\bTicker\s*[:=]\s*([A-Z][A-Z0-9]{2,11})\b", text, re.I)
    if m:
        return m.group(1)
    return None

def split_digest_blocks(text, text_mints):
    """Split a digest message into per-card blocks by mint-start lines.
    Returns [{mint, block}] (block = lines from this mint until the next)."""
    lines = text.split('\n')
    idx = {}
    for i, ln in enumerate(lines):
        for m in MINT_RE.findall(ln):
            if valid_mint(m):
                idx[m] = i
                break
    cards = []
    for mint in text_mints:
        i = idx.get(mint)
        if i is None:
            continue
        j = min((v for k, v in idx.items() if v > i), default=len(lines))
        cards.append({"mint": mint, "block": "\n".join(lines[i:j]).strip()})
    return cards


def block_fields(block, fallback_sym=None, fallback_mc=None):
    """Parse symbol + MC from a digest card block."""
    sym, mc = fallback_sym, fallback_mc
    m = re.search(r"\$([A-Z][A-Z0-9]{1,11})\b", block)
    if m:
        sym = m.group(1)
    else:
        m = re.search(r"┌(.+?)\s*\(([A-Z][A-Z0-9]{1,11})\)", block)
        if m:
            sym = m.group(2)
    m = re.search(r"MC:\s*\$([\d.,]+[KMB]?)", block, re.I)
    if m:
        mc = parse_mc(m.group(1))
    return sym, mc


def process_message(msg, channel):
    """Record + forward to pipeline. Returns summary line or None.

    Live-corrected Oct 17:
      - channel cards carry their OWN symbol + MC in the text
      - CAs hide in button/entity URLs (dominant mint, NOT solscan /account/ wallet links)
      - victory laps / rank digests / updates are noise -> skipped
      - FRESHNESS GATE: only calls <5 min old enter calls.jsonl (stale catch-up
        backlog must never reach the pipeline — the 'MC is late'/'old call' bug)
    Ledger (telegram_messages.jsonl) still records EVERYTHING for forensics."""
    if msg is None or getattr(msg, "action", None):
        return None
    text = msg.message or ""
    sym, mc, kind, skip = parse_signal_fields(text)
    # ledger first: record everything (kind + skip tell the audit story)
    rec = {"channel": channel, "channel_id": msg.chat_id, "msg_id": msg.id,
           "dt": msg.date.isoformat() if msg.date else None, "text": text[:2000],
           "mints": None, "dominant_url_mint": None, "kind": kind, "skip": skip or None,
           "edit_ts": msg.edit_date.isoformat() if msg.edit_date else None}
    post_ts = msg.date.timestamp() if msg.date else time.time()
    if skip:
        rec["mints"], rec["dominant_url_mint"] = parse_mints(msg)
        append_jsonl(MSG_LEDGER, rec)
        return None
    # freshness gate: only live calls (<5 min old) become signals
    age_s = time.time() - post_ts
    if age_s > 300 or age_s < -60:
        rec["mints"], rec["dominant_url_mint"] = parse_mints(msg)
        rec["skip"] = f"stale_{age_s:.0f}s"
        append_jsonl(MSG_LEDGER, rec)
        return None
    text_mints, dominant = parse_mints(msg)
    rec["mints"], rec["dominant_url_mint"] = text_mints, dominant
    append_jsonl(MSG_LEDGER, rec)
    lines = []
    calls = []
    if text_mints:
        # digest: one record per visible card, each with its own symbol/MC
        for card in split_digest_blocks(text, text_mints):
            csym, cmc = block_fields(card["block"], sym, mc)
            calls.append({"mint": card["mint"], "symbol": csym, "mc": cmc,
                          "text": card["block"][:300], "msg_id": msg.id})
    elif dominant and kind != 'none':
        # single-card call: the CA is the dominant URL mint; symbol/MC from the body
        calls.append({"mint": dominant, "symbol": sym, "mc": mc,
                      "text": text[:300], "msg_id": msg.id})
    for c in calls:
        fresh = is_new_mint(c["mint"])
        if fresh:
            rec2 = {"mint": c["mint"], "channel": channel, "dt": rec["dt"], "ts": post_ts,
                    "text": c["text"], "source": "telethon", "msg_id": c["msg_id"], "kind": kind}
            if c.get("symbol"):
                rec2["symbol"] = c["symbol"]
            if c.get("mc"):
                rec2["mc"] = c["mc"]
            append_jsonl(CALLS, rec2)
            lines.append(f"NEW ${c.get('symbol') or c['mint'][:10]} from {channel} ({rec['dt']})")
    return "; ".join(lines) if lines else None

async def run():
    from telethon import TelegramClient, events
    creds = json.load(open(CRED_FILE))
    allow = [c.lstrip("@").lower() for c in load_allowlist()]
    print(f"allowlist: {allow}", flush=True)
    client = TelegramClient(SESSION_FILE, creds["api_id"], creds["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT_AUTHORIZED — run `login` mode first", flush=True)
        return
    me = await client.get_me()
    print(f"logged in as {me.username or me.id}", flush=True)
    # heartbeat pidfile so watchdog can check liveness without pgrep self-match
    PID_FILE = os.path.join(DESK, "telethon_listener.pid")
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    print(f"pidfile {PID_FILE}: {os.getpid()}", flush=True)

    state = load_state()
    # resolve allowlist entities; also support private channels by join-link cache
    entities = {}
    for ch in allow:
        try:
            ent = await client.get_entity(ch)
            entities[ch] = ent
            print(f"  resolved {ch} -> id {ent.id}", flush=True)
        except Exception as e:
            print(f"  cannot resolve {ch}: {str(e)[:60]}", flush=True)
    ids_to_name = {e.id: n for n, e in entities.items()}

    def chan_name(cid):
        """Map a Telegram chat_id (-100XXXXXXXXXX for channels) to our username key."""
        if cid in ids_to_name:
            return ids_to_name[cid]
        s = str(cid)
        if s.startswith("-100"):  # strip the channel peer prefix -> entity id
            return ids_to_name.get(int(s[4:]))
        return None

    # catch-up: fetch missed messages since last seen
    for ch, ent in entities.items():
        last = state.get("last_msg", {}).get(ch, 0)
        try:
            async for m in client.iter_messages(ent, limit=30, min_id=last):
                process_message(m, ch)
                if m.id > state.get("last_msg", {}).get(ch, 0):
                    state.setdefault("last_msg", {})[ch] = m.id
        except Exception as e:
            print(f"catchup {ch}: {str(e)[:60]}", flush=True)
    save_state(state)
    print("catch-up done — listening", flush=True)

    @client.on(events.NewMessage(chats=list(ids_to_name.keys())))
    async def handler(ev):
        ch = chan_name(ev.chat_id)
        line = process_message(ev.message, ch)
        state = load_state()
        state.setdefault("last_msg", {})[ch or "?"] = ev.message.id
        save_state(state)
        if line:
            print(line, flush=True)

    await client.run_until_disconnected()

async def login():
    """Interactive login: requests code to the account, user pastes it in chat."""
    from telethon import TelegramClient
    creds = json.load(open(CRED_FILE))
    client = TelegramClient(SESSION_FILE, creds["api_id"], creds["api_hash"])
    await client.connect()
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"ALREADY_AUTHORIZED as {me.username or me.id}")
        return
    sent = await client.send_code_request(creds["phone"])
    print("CODE_SENT — ask the user for the code (and 2FA password if set)")
    # code/password are supplied via files written by the operator, never argv logs
    code_file = os.path.join(DESK, ".tg_code")
    for _ in range(120):
        await asyncio.sleep(5)
        if os.path.exists(code_file):
            code = open(code_file).read().strip()
            os.remove(code_file)
            try:
                await client.sign_in(creds["phone"], code)
            except Exception as e:
                if "password" in str(e).lower() or "Two-steps" in str(e):
                    pw_file = os.path.join(DESK, ".tg_pw")
                    print("2FA_REQUIRED")
                    while not os.path.exists(pw_file):
                        await asyncio.sleep(5)
                    pw = open(pw_file).read().strip()
                    os.remove(pw_file)
                    await client.sign_in(password=pw)
                else:
                    print(f"signin err: {str(e)[:80]}")
                    return
            me = await client.get_me()
            print(f"AUTHORIZED as {me.username or me.id}")
            return
    print("timeout waiting for code")

async def check():
    """Verify session + which allowlist channels are actually readable."""
    from telethon import TelegramClient
    creds = json.load(open(CRED_FILE))
    client = TelegramClient(SESSION_FILE, creds["api_id"], creds["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT_AUTHORIZED")
        return
    me = await client.get_me()
    print(f"authorized as {me.username or me.id}")
    for ch in load_allowlist():
        try:
            ent = await client.get_entity(ch)
            msgs = await client.get_messages(ent, limit=1)
            has = msgs[0].date.isoformat() if msgs and msgs[0] else "no messages"
            print(f"  {ch}: READABLE (id {ent.id}, last msg {has})")
        except Exception as e:
            print(f"  {ch}: NOT READABLE ({str(e)[:50]})")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "login":
        asyncio.run(login())
    elif mode == "check":
        asyncio.run(check())
    else:
        asyncio.run(run())

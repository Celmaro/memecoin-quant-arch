#!/usr/bin/env python3
"""desk_supervisor.py — single daemon replacing the poller crons (user: "we don't have crons anymore").

Jobs (event-driven, no cron):
  1. SUPERVISE: telethon_listener.py (MTProto ingestion), hmq_listener.py (scan group),
     ws_monitor.py (PumpDev WS for open positions). Restart any that die.
  2. SCAN+ALERT: pick up NEW telethon calls from calls.jsonl, AEGIS-scan (bounded),
     notify HMQ group + user DM. Uses notifier_state.json watermark.
  3. PAPER ENGINE: run paper_tick every 60s; deliver actionable lines + equity to DM.

Delivery: `hermes send -t telegram:1223241788` (gateway's own Telegram credentials —
the same path the old crons used, no bot token needed).
"""
import os, sys, json, time, subprocess, signal, logging

DESK = os.path.expanduser("~/memecoin-quant")
sys.path.insert(0, DESK)
LOG_PATH = os.path.join(DESK, "desk_supervisor.log")
DM = "telegram:1223241788"
HMQ_CHAT = -1004430296219

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("supervisor")

PY = sys.executable
VENV_PY = os.path.join(DESK, ".venv", "bin", "python")

# child daemons: (name, [cmd])
CHILDREN = [
    ("telethon", [VENV_PY, os.path.join(DESK, "telethon_listener.py"), "run"]),
    ("hmq", [PY, os.path.join(DESK, "hmq_listener.py")]),
    ("ws", [PY, os.path.join(DESK, "ws_monitor.py")]),
]

_procs = {}   # name -> Popen
_restart_ts = {}  # name -> last restart time (backoff)

def send_dm(text, subject=None):
    """Deliver to the user's DM via hermes send. Returns True on success."""
    try:
        cmd = ["hermes", "send", "-t", DM, "-q"]
        if subject:
            cmd += ["-s", subject]
        p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=40)
        if p.returncode == 0:
            return True
        log.warning("send_dm rc=%s err=%s", p.returncode, p.stderr[:200])
        return False
    except Exception as e:
        log.warning("send_dm exc: %s", e)
        return False

def post_hmq(text):
    """Post into the HMQ scan group via bot token (best-effort; token may be rotated)."""
    try:
        from desk_common import load_config, http_json
        tok = load_config().get("HMQ_BOT_TOKEN")
        if not tok:
            return
        http_json(f"https://api.telegram.org/bot{tok}/sendMessage", method="POST",
                  body={"chat_id": HMQ_CHAT, "text": text},
                  headers={"Content-Type": "application/json"}, retries=1)
    except Exception:
        pass

# ---------- 1. supervision ----------

def ensure_children():
    for name, cmd in CHILDREN:
        p = _procs.get(name)
        if p and p.poll() is None:
            continue
        now = time.time()
        if now - _restart_ts.get(name, 0) < 60:
            log.warning("%s: restart backoff, waiting", name)
            continue
        _restart_ts[name] = now
        log.info("starting %s: %s", name, " ".join(cmd))
        logfile = open(os.path.join(DESK, f"{name}_supervised.log"), "a")
        _procs[name] = subprocess.Popen(cmd, stdout=logfile, stderr=logfile,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
        time.sleep(2)

# ---------- 2. scan+alert for NEW telethon calls ----------

# ---------- outbox dedupe + signal-health counters (delivery stability) ----------

DEDUPE_TTL_S = 600          # one alert per (mint, channel) per 10 min — kills repeat floods
OUTBOX = {}                 # "mint|channel" -> ts of last alert
HEALTH = {}                 # channel -> {emitted, deduped, skip_stale, skip_dead}
HEALTH_EVERY_TICKS = 15     # send a health line every ~15 min (60s loop)
_ledger_pos = 0             # telegram_messages.jsonl offset for noise counting

def outbox_check(mint, chan, now_t=None):
    """True if this (mint, channel) was alerted recently — suppress + count."""
    global OUTBOX
    now_t = now_t or time.time()
    # prune old
    if len(OUTBOX) > 400:
        OUTBOX = {k: v for k, v in OUTBOX.items() if now_t - v < DEDUPE_TTL_S * 2}
    key = f"{mint}|{chan}"
    last = OUTBOX.get(key, 0)
    if now_t - last < DEDUPE_TTL_S:
        return True
    OUTBOX[key] = now_t
    return False

def hbump(chan, field):
    HEALTH.setdefault(chan or "?", {}).setdefault(field, 0)
    HEALTH[chan or "?"][field] += 1

def count_ledger_noise(since_ts):
    """Count ledger records with a skip reason since a watermark (ingestion noise)."""
    global _ledger_pos
    noise = 0
    try:
        with open(os.path.join(DESK, "telegram_messages.jsonl"), "rb") as f:
            if _ledger_pos:
                f.seek(_ledger_pos)
            for line in f:
                _ledger_pos = f.tell()
                try:
                    d = json.loads(line)
                    if d.get("skip"):
                        noise += 1
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return noise

def signal_health_line():
    """Per-channel quality summary for the DM (delivery-stability suggestion 4)."""
    if not HEALTH:
        return None
    noise = count_ledger_noise(0)
    lines = [f"📊 Signal health (last {HEALTH_EVERY_TICKS} ticks):"]
    for ch in sorted(HEALTH):
        h = HEALTH[ch]
        lines.append(f"  {ch}: 📢{h.get('emitted',0)} 🚫deduped {h.get('deduped',0)} "
                     f"⏳stale {h.get('skip_stale',0)} 💀dead {h.get('skip_dead',0)}")
    lines.append(f"  🗑 ingestion noise (victory/promo/update): {noise}")
    HEALTH.clear()
    return "\n".join(lines)

def scan_new_calls(max_scan=3):
    """Scan telethon-sourced calls not yet AEGIS-scanned; alert DM/HMQ.
    Watermark: notifier_state.json last_ts (call_notifier convention).
    QUALITY GATES (user: "Still many $? / $0 MC / Scan deferred"):
      - freshness: only alert calls <5min old (catch-up backlog is NOT live signals)
      - market-data: skip tokens with NO pair AND NO GMGN row (dead/unlisted junk)
      - alert cap: max 5 DMs per tick (no floods)
    Symbol/MC resolved via DexScreener then GMGN fallback."""
    st_path = os.path.join(DESK, "notifier_state.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {}
    last = float(st.get("last_ts") or 0)
    fresh = []
    try:
        with open(os.path.join(DESK, "calls.jsonl")) as f:
            for line in f:
                try:
                    c = json.loads(line)
                except Exception:
                    continue
                ts = float(c.get("ts") or 0)
                if ts > last:
                    fresh.append(c)
    except FileNotFoundError:
        return
    if not fresh:
        return
    # watermark advances past ALL seen (stale included) so backlog never re-alerts
    last = max(float(c.get("ts") or 0) for c in fresh)
    now = time.time()
    FRESH_S = 300          # user mandate: live signals only (<5 min)
    ALERT_CAP = 5          # max DM alerts per tick
    scanned = 0
    alerted = 0
    for c in fresh:
        mint = c.get("mint")
        if not mint:
            continue
        chan = (c.get("channel") or "?").replace("Trending", "").replace("trending", "")
        ts = float(c.get("ts") or 0)
        age = now - ts
        if age > FRESH_S or age <= 0:
            hbump(chan, "skip_stale")
            continue       # stale backlog or clock skew — NOT a live signal
        sym = c.get("symbol")
        if not sym or sym == "?" or sym == "None":
            sym = None
        mc = c.get("mc")   # channel-posted MC first — the REAL MC for bonding-curve tokens
        # Resolve missing ticker/MC: DexScreener then GMGN, only when channel didn't provide
        if sym is None or mc is None:
            try:
                from desk_common import dex_pair
                p = dex_pair(mint)
                if p:
                    sym = sym or ((p.get("baseToken") or {}).get("symbol"))
                    mc = mc or (p.get("marketCap") or None)
            except Exception:
                pass
        if sym is None or mc is None:
            try:
                from desk_common import resolve_token_meta
                meta = resolve_token_meta(mint)
                if meta:
                    sym = sym or meta.get("symbol")
                    mc = mc or meta.get("marketCap")
            except Exception:
                pass
        if sym is None and mc is None:
            hbump(chan, "skip_dead")
            continue       # dead/unlisted — nothing to show, skip alert
        if alerted >= ALERT_CAP:
            continue       # cap DM floods (watermark still advanced)
        # outbox dedupe: one alert per (mint, channel) per 10 min
        if outbox_check(mint, chan, now_t=now):
            hbump(chan, "deduped")
            continue
        alerted += 1
        hbump(chan, "emitted")
        sym = sym or "?"
        if mint and scanned < max_scan:
            try:
                import aegis
                rep = aegis.score_coin(mint, deep=False)
                scanned += 1
                score = rep.get("score")
                strat = rep.get("strategy") or ""
                if sym == "?" and rep.get("symbol"):
                    sym = rep["symbol"]
                if mc is None:
                    mc = rep.get("mc")
                mc = mc or 0
                lines = [f"📢 New call: ${sym} (from {chan}) at ${mc:,.0f} MC",
                         f"   CA: {mint}",
                         f"   AEGIS {score:.0f}/100 [{rep.get('verdict')}] strat={strat}"]
                body = "\n".join(lines)
                post_hmq(body)
                send_dm(body, subject="📡 Fresh signal")
            except Exception as e:
                log.warning("scan err %s: %s", mint[:12], e)
        else:
            # no scan budget left — still notify DM/HMQ without score
            mc_s = f" at ${mc:,.0f} MC" if mc else ""
            body = f"📢 New call: ${sym} (from {chan}){mc_s}\n   CA: {mint}\n   (scan deferred)"
            send_dm(body, subject="📡 Fresh signal")
    # persist watermark
    json.dump({"last_ts": last}, open(st_path + ".tmp", "w"))
    os.replace(st_path + ".tmp", st_path)

# ---------- 3. paper engine ----------

def paper_tick_once():
    """Run paper_tick, capture actionable output, deliver to DM."""
    try:
        p = subprocess.run([PY, os.path.join(DESK, "paper_tick.py")],
                           capture_output=True, text=True, timeout=120)
        out = (p.stdout or "").strip()
        if not out:
            return
        lines = [l for l in out.splitlines()
                 if any(k in l for k in ("STOP", "EXIT", "CLIP", "BUY", "EMERGENCY", "DEAD",
                                         "GATE", "Paper account", "STALE", "no market"))]
        if lines:
            send_dm("\n".join(lines), subject="💼 Paper desk")
    except Exception as e:
        log.warning("paper_tick exc: %s", e)

# ---------- main loop ----------

def main():
    log.info("desk_supervisor starting (pid %s)", os.getpid())
    # initial: notify once that supervisor is up
    send_dm("Desk supervisor online — Telethon ingestion + paper engine + alerts (no crons).",
            subject="🟢 Desk daemon")
    tick = 0
    while True:
        try:
            ensure_children()
            scan_new_calls(max_scan=3)
            # signal-health line every ~15 min (delivery-stability suggestion 4)
            if tick % HEALTH_EVERY_TICKS == 0:
                hline = signal_health_line()
                if hline:
                    send_dm(hline, subject="📊 Signal health")
            if tick % 1 == 0:
                paper_tick_once()   # every ~60s loop iteration
        except Exception as e:
            log.exception("loop err: %s", e)
        tick += 1
        time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        for p in _procs.values():
            try:
                p.terminate()
            except Exception:
                pass
        log.info("supervisor stopped")

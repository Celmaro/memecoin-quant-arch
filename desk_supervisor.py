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
import os, sys, json, time, subprocess, signal, logging, threading
import fcntl

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
    # hmq_listener + ws_monitor retired to archive/retired (Oct 19) — telethon covers ingestion
    ("pumpdata", [VENV_PY, os.path.join(DESK, "pumpdata_feed.py")]),
]

_procs = {}   # name -> Popen
_restart_ts = {}  # name -> last restart time (backoff)
_SUPERVISOR_LOCK = None


def acquire_supervisor_lock():
    """F5: make systemd the sole supervisor owner; reject manual duplicates."""
    global _SUPERVISOR_LOCK
    path = os.path.join(DESK, "desk_supervisor.lock")
    _SUPERVISOR_LOCK = open(path, "a+")
    try:
        fcntl.flock(_SUPERVISOR_LOCK.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("another desk_supervisor already owns %s; exiting", path)
        raise SystemExit(2)
    _SUPERVISOR_LOCK.seek(0)
    _SUPERVISOR_LOCK.truncate()
    _SUPERVISOR_LOCK.write(str(os.getpid()))
    _SUPERVISOR_LOCK.flush()


def release_supervisor_lock():
    global _SUPERVISOR_LOCK
    if _SUPERVISOR_LOCK:
        try:
            fcntl.flock(_SUPERVISOR_LOCK.fileno(), fcntl.LOCK_UN)
            _SUPERVISOR_LOCK.close()
        except Exception:
            pass

def send_dm(text, subject=None):
    """Deliver to the user's DM via hermes send. Returns True on success."""
    # systemd user service has a minimal env — resolve hermes absolutely AND point
    # HERMES_HOME at the active profile so it loads the right TELEGRAM_BOT_TOKEN.
    # Without these, send fails with "Platform 'telegram' is not configured"
    # (looked in default ~/.hermes/.env) — every alert silently lost (Oct 19).
    hermes_bin = next((p for p in ("/home/agentuser/.local/bin/hermes",
                                   "/usr/local/bin/hermes", "/usr/bin/hermes")
                       if os.path.exists(p)), "hermes")
    profile_home = os.environ.get("HERMES_HOME") or \
        "/home/agentuser/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239"
    env = dict(os.environ)
    env["HERMES_HOME"] = profile_home
    try:
        cmd = [hermes_bin, "send", "-t", DM, "-q"]
        if subject:
            cmd += ["-s", subject]
        p = subprocess.run(cmd, input=text, capture_output=True, text=True,
                           timeout=40, env=env)
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
        # F3 (Oct 19): persist call metadata (channel + call time) BEFORE any gate,
        # so coins.source/call_ts are populated even when freshness skips the alert.
        try:
            from desk_common import remember_call_meta
            remember_call_meta(mint, chan, ts, symbol=c.get("symbol"), mc=c.get("mc"))
        except Exception:
            pass
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
        # --- WASH GATE (audit Oct 19 fix #2, RESEARCH.md §5): volume-per-unique-trader.
        # A big 5m volume carried by 1-3 wallets = manufactured tape (LPI/wash), not demand.
        try:
            from desk_common import blix_coin_trades, blix_alive
            if blix_alive():
                trades = blix_coin_trades(mint, limit=300) or []
                now_s = now
                recent = [t for t in trades if now_s - float(t.get("t") or 0) <= 300]
                usd5 = sum(float(t.get("usd") or 0) for t in recent)
                wallets5 = {t.get("wallet") for t in recent if t.get("wallet")}
                # flag when: $5k+ moved in 5m by <=3 wallets (concentrated), OR
                # usd/wallet > $2,500 (whale-only tape) with <8 wallets
                if usd5 >= 5000 and len(wallets5) <= 3:
                    hbump(chan, "skip_wash_concentrated")
                    flags_note = (f"wash-suspect: ${usd5:,.0f}/5m across {len(wallets5)} wallets")
                    with open(os.path.join(DESK, "wash_gate.log"), "a") as wf:
                        wf.write(f"{time.strftime('%FT%T')} {mint} {sym} {flags_note}\n")
                    continue
                if usd5 >= 8000 and len(wallets5) < 8 and usd5 / max(len(wallets5),1) > 2500:
                    hbump(chan, "skip_wash_whaleonly")
                    with open(os.path.join(DESK, "wash_gate.log"), "a") as wf:
                        wf.write(f"{time.strftime('%FT%T')} {mint} {sym} whale-only tape ${usd5:,.0f}/{len(wallets5)}w\n")
                    continue
        except Exception:
            pass  # tape down — never block alerts on the gate
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
                # User choice (Oct 19, option 1): alert AFTER deep analysis so the
                # score/verdict shown matches the forensics in aegis.db. deep_cache
                # (1h TTL) makes repeat scans instant; only the FIRST scan of a new
                # mint pays the ~40s deployer chain-history walk. Runs in the scan
                # thread (fix #3), so it can never delay the paper tick.
                rep = aegis.score_coin(mint, deep=True)
                scanned += 1
                score = rep.get("score")
                strat = rep.get("strategy") or ""
                if sym == "?" and rep.get("symbol"):
                    sym = rep["symbol"]
                if mc is None:
                    mc = rep.get("mc")
                mc = mc or 0
                # data-completeness note: deep layer is ALWAYS included now (option 1).
                # The only variable is the live-quality overlay — show live×N when it
                # fired, or a clear "no live tape yet" (normal for brand-new mints)
                # instead of the misleading "contract-only" (Oct 19 user feedback).
                live_flags = [f for f in (rep.get("flags") or [])
                              if f.startswith(("micro_", "churn_", "fresh_wallet_",
                                               "fill_", "cohort_", "bitquery_"))]
                note = f" [deep·live×{len(live_flags)}]" if live_flags else " [deep, live tape n/a]"
                score_s = f"{score:.0f}" if isinstance(score, (int, float)) else "?"
                verdict_s = rep.get("verdict") or "?"
                lines = [f"📢 New call: ${sym} (from {chan}) at ${mc:,.0f} MC",
                         f"   CA: {mint}",
                         f"   AEGIS {score_s}/100 [{verdict_s}] strat={strat}{note}"]
                body = "\n".join(lines)
                post_hmq(body)
                # Rich Forensics-on card is the sole DM alert path; suppress the
                # duplicate plain Fresh signal DM (user preference).
            except Exception as e:
                log.warning("scan err %s: %s", mint[:12], e)
        else:
            # no scan budget left — still notify HMQ without score (DM skipped:
            # rich Forensics-on card is the sole DM alert path)
            mc_s = f" at ${mc:,.0f} MC" if mc else ""
            body = f"📢 New call: ${sym} (from {chan}){mc_s}\n   CA: {mint}\n   (scan deferred)"
            post_hmq(body)
    # persist watermark — merge, never clobber instant_trigger's instant_sent dedupe
    st = json.load(open(st_path)) if os.path.exists(st_path) else {}
    st["last_ts"] = last
    json.dump(st, open(st_path + ".tmp", "w"))
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
        if not lines:
            return
        # 3-min heartbeat for the summary line only (user: "every 3 minutes, not 1m").
        # Action lines (BUY/EXIT/EMERGENCY/STOP/CLIP/... ) always send immediately.
        now = time.time()
        action_lines = [l for l in lines if "Paper account" not in l]
        summary_lines = [l for l in lines if "Paper account" in l]
        hb_path = os.path.join(DESK, ".paper_summary_hb")
        send_summary = False
        try:
            last = float(open(hb_path).read().strip() or 0)
            send_summary = (now - last) >= 180
        except Exception:
            send_summary = True
        if summary_lines and send_summary:
            try:
                open(hb_path, "w").write(str(now))
            except Exception:
                pass
        deliver = action_lines
        if summary_lines and send_summary:
            deliver = deliver + summary_lines
        if deliver:
            send_dm("\n".join(deliver), subject="💼 Paper desk")
    except Exception as e:
        log.warning("paper_tick exc: %s", e)


def _run_whale_watch():
    """Poll sol.wales for top-holder sell activity on open positions and DM.
    Threaded; never blocks the main loop; state persisted in whale_watch_state.json."""
    try:
        sys.path.insert(0, DESK)
        from whale_watch import whale_watch_once
        alerts = whale_watch_once()
        for a in alerts:
            sym = a.get("symbol") or "?"
            wallet = (a.get("wallet") or "")[:10]
            pct = a.get("pct")
            pct_s = f" ({pct:.1f}% holder)" if isinstance(pct, (int, float)) else ""
            at = a.get("at")
            at_s = f" @ {at}" if at else ""
            body = (f"🐋 Whale sell on ${sym}: top holder {wallet}…{pct_s}\n"
                    f"    sold {a.get('amount')} ({a.get('kind')}){at_s}\n"
                    f"    CA: {a.get('mint')}\n"
                    f"    sig: {a.get('signature')}")
            send_dm(body, subject="🐋 Whale watch")
    except Exception as e:
        log.warning("whale_watch exc: %s", e)

# ---------- main loop ----------

def main():
    acquire_supervisor_lock()
    log.info("desk_supervisor starting (pid %s)", os.getpid())
    # initial: notify once that supervisor is up
    send_dm("Desk supervisor online — Telethon ingestion + paper engine + alerts (no crons).",
            subject="🟢 Desk daemon")
    # Latency audit Oct 19 (fixes 1/3/5): event-driven scan on calls.trigger instead of
    # blind 60s polling; paper tick on its own 60s timer; deadline sleep so a slow scan
    # never delays exits and a call never waits a full tick.
    TRIGGER = os.path.join(DESK, "calls.trigger")
    last_scan_trigger = 0.0      # last mtime we consumed
    next_paper = time.time()     # paper tick due immediately at start
    next_health = time.time() + HEALTH_EVERY_TICKS * 60
    next_followups = time.time() + 1800
    next_maintenance = time.time() + 86400
    _followup_running = [False]
    tick = 0

    def _run_followup_batch():
        """Bounded followups batch in a thread so it can NEVER block the scan/
        paper loop (F2 Oct 19 — running it synchronously stalled paper_tick)."""
        if _followup_running[0]:
            return
        _followup_running[0] = True
        try:
            p = subprocess.run(
                [PY, "-c", "import journal; n=journal.run_followups(verbose=False, limit=60, deadline_s=80); print(f'followups: {n} processed')"],
                capture_output=True, text=True, timeout=95, cwd=DESK)
            if p.stdout.strip():
                log.info("%s", p.stdout.strip())
        except Exception as fe:
            log.warning("followups exc: %s", fe)
        finally:
            _followup_running[0] = False
    while True:
        try:
            ensure_children()
            now = time.time()
            # 1) SCAN on event: telethon touches calls.trigger the moment it appends a
            # fresh call; when the mtime advances past what we consumed, scan now.
            try:
                tm = os.path.getmtime(TRIGGER)
            except Exception:
                tm = 0.0
            if tm > last_scan_trigger:
                # coalesce bursts: consume the trigger, scan once (fresh calls are
                # deduped by watermark anyway). Run in a background thread so a slow
                # deep scan can never delay the paper tick (latency audit fix #3).
                last_scan_trigger = tm
                try:
                    t = threading.Thread(target=scan_new_calls, kwargs={"max_scan": 3}, daemon=True)
                    t.start()
                except Exception as e:
                    log.warning("scan spawn exc: %s", e)
            # 2) PAPER TICK on its own 60s timer (decoupled from scan — a slow deep
            # scan must never hold up exits).
            if now >= next_paper:
                paper_tick_once()
                next_paper = now + 60
            # 2b) WHALE WATCH: poll sol.wales for sells by top holders of open
            # positions; DM on new activity. Runs in a thread (never stalls tick).
            if tick % 3 == 0:
                try:
                    threading.Thread(target=_run_whale_watch, daemon=True).start()
                except Exception as e:
                    log.warning("whale spawn exc: %s", e)
            # 3) signal-health line every ~15 min (wall-clock, not tick count)
            if now >= next_health:
                hline = signal_health_line()
                if hline:
                    send_dm(hline, subject="📊 Signal health")
                next_health = now + HEALTH_EVERY_TICKS * 60
            # 3b) FOLLOWUPS: bounded PRFS/outcome batch (F2, Oct 19). Only desk.py
            # used to run followups, so 354 rows were stuck undone. Runs in a
            # thread so it can never stall the scan/paper loop.
            if now >= next_followups:
                threading.Thread(target=_run_followup_batch, daemon=True).start()
                next_followups = now + 1800   # every 30 min wall-clock
            # daily DB maintenance (audit Oct 19 fix #8): dedupe, retention, WAL care
            # NOTE: tick-based was running ~hourly (2s deadline-sleep), not daily —
            # switched to 24h wall-clock (Oct 19 re-audit).
            if now >= next_maintenance:
                try:
                    import aegis_maintenance
                    rep = aegis_maintenance.main()
                    log.info("db maintenance: %s", rep)
                except Exception as me:
                    log.warning("db maintenance failed: %s", me)
                next_maintenance = now + 86400
        except Exception as e:
            log.exception("loop err: %s", e)
        tick += 1
        # 4) DEADLINE SLEEP: wake up no later than the next paper tick; check the
        # trigger every 2s so fresh calls are picked up near-instantly.
        sleep_s = min(2.0, max(0.2, next_paper - time.time()))
        time.sleep(sleep_s)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        for p in _procs.values():
            try:
                p.terminate()
            except Exception:
                pass
        release_supervisor_lock()
        log.info("supervisor stopped")

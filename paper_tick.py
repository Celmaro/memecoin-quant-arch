#!/usr/bin/env python3
"""Paper-trade tick: mark positions to market, apply MC-adaptive exit engine, execute paper clips/exits."""
import sys, json, time, os
sys.path.insert(0, "/home/agentuser/memecoin-quant")
from desk_common import dex_pair, market_for
import position_monitor as pm

BOOK = "/home/agentuser/memecoin-quant/paper_book.json"
CONSUMED_FILE = "/home/agentuser/memecoin-quant/consumed_calls.jsonl"

# Grad-curve threshold: pump.fun bonding curve fills at ~$69K MC. Below that a coin
# is pre-graduation and reports NO pool liquidity (liq=0) — the $10K liq gate must
# never treat those as dead (audit Oct 20: gate blocked 22/33 of the best cohort).
CURVE_FILL_MC_USD = 69_000
CURVE_GRAD_LIQ_MIN = 10_000

def _is_curve_pre_grad(mkt):
    """True for a pump.fun mint still on the bonding curve (MC < fill) where a
    liq=0 read is EXPECTED, not evidence of death. Those coins report liq $0 until
    the curve fills, so the liq gate must not reject them on liq alone."""
    if not mkt:
        return False
    mc = float((mkt.get("marketCap") or mkt.get("marketCapQuote") or 0) or 0)
    if mc <= 0:
        return False
    # if MC is already above fill, it's graduated (has real pool liq) — gate applies
    return mc < CURVE_FILL_MC_USD

def _note_fill(mint, age_min, ts=None):
    """Record fill-time (call→entry minutes) in the coins table. Oct 20 audit:
    coins.fill_time_min was 0% populated (dead field) — the desk's own entry
    latency audit was blind. Cheap local write, never inside a book txn."""
    try:
        import sqlite3
        con = sqlite3.connect("/home/agentuser/memecoin-quant/aegis.db", timeout=15)
        con.execute("UPDATE coins SET fill_time_min=?, call_ts=COALESCE(call_ts,?) "
                    "WHERE mint=?", (round(float(age_min or 0), 3), ts or time.time(), mint))
        con.commit(); con.close()
    except Exception:
        pass

def _consumed_mints(max_age_s=86400):
    """Mints already consumed as entries (P0.2): durable ledger so a quick stop/clip
    within the freshness window can't re-buy the same curated signal."""
    out = {}
    try:
        cutoff = time.time() - max_age_s
        for line in open(CONSUMED_FILE):
            try:
                c = json.loads(line)
                if c.get("ts", 0) >= cutoff and c.get("mint"):
                    out[c["mint"]] = c
            except Exception:
                continue
    except FileNotFoundError:
        pass
    return out

def load_book():
    return json.load(open(BOOK))

def save_book(b):
    # GUARD (Oct 19, clobber-incident): refuse to write a structurally invalid
    # book. A synthetic/test book missing required keys (start/cash/positions) was
    # once written over the live $92.40 book by a verification test's save_book
    # call. Required keys + sane types; raise instead of silently clobbering.
    required = ("start", "cash", "positions", "trades")
    for k in required:
        if k not in b:
            raise ValueError(f"save_book refused: book missing required key '{k}' "
                             f"(got {sorted(b.keys())}) — refusing to clobber live book")
    if not isinstance(b["positions"], dict) or not isinstance(b["trades"], list):
        raise ValueError("save_book refused: positions must be dict, trades must be list")
    if not isinstance(b.get("cash"), (int, float)) or not isinstance(b.get("start"), (int, float)):
        raise ValueError("save_book refused: cash/start must be numeric")
    # BOOK INVARIANTS (Oct 21, smoke-test-clobber + phantom-+$30.8M incidents):
    # refuse writes that would enshrine a corrupt trade. (1) Every trade mult must
    # be within [0.001, 20] — a $20 paper position booking mult 1.59M (pnl +$30.8M)
    # or my smoke-test $200 fake sale is a corrupted write. (2) Trade pnl must be
    # within [-30, 400] for a ≤$25 basis (3% fee + capped 10x gain bound) — anything
    # beyond is a phantom. (3) The write must carry at least the structural keys.
    for _t in b.get("trades") or []:
        _m = float(_t.get("mult") or 0)
        if abs(_m) > 20.0:
            raise ValueError(f"save_book refused: trade {_t.get('symbol')} mult {_m:.4g} "
                             f"outside [-20, 20] — refusing to book a phantom gain/loss")
        _p = float(_t.get("pnl") or 0)
        if _p > 400.0 or _p < -30.0:
            raise ValueError(f"save_book refused: trade {_t.get('symbol')} pnl {_p:.2f} "
                             f"outside [-30, 400] — refusing to book a phantom gain/loss")
    import tempfile
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, BOOK)

_GATE_EMIT_FILE = os.path.expanduser("~/memecoin-quant/.gate_emit_state.json")

def _gate_emit_ok(sym, reason, how, cooldown_s=300):
    """Dedupe repeated GATE/no-market lines per (symbol, reason, stage-path).

    USER-APPROVED Oct 20 (\"this feels like spam\"): the same blocked position was
    printing 'GATE X: stage engine no-op' + '— nothing booked' EVERY tick for 30+
    minutes. Now the FIRST block of a (sym, reason) prints, then the line is
    suppressed for cooldown_s (300s). When the block clears, the real sell line
    (EMERGENCY/EXIT) still prints — so you lose noise, never signal.
    File-backed because the supervisor spawns a fresh paper_tick subprocess each
    tick — in-memory state would reset every 2 minutes."""
    key = f"{sym}|{reason}|{how}"
    now = time.time()
    try:
        with open(_GATE_EMIT_FILE) as f:
            st = json.load(f)
    except Exception:
        st = {}
    last = float(st.get(key) or 0)
    if now - last < cooldown_s:
        return False
    st[key] = now
    # cap file growth
    if len(st) > 500:
        st = dict(sorted(st.items(), key=lambda kv: kv[1])[-300:])
    tmp = _GATE_EMIT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, _GATE_EMIT_FILE)
    return True

def sell_via_stage(b, pos, sym, mint, stage, mult, px, reason, how="stage", out=None, market_ts=None, liq=None):
    """UNIFIED paper sell: risk gate → reserve → stage-curve execute → attribution.
    Returns True if handled. stage1 = target 50% of original, stage3 = full exit.
    Builds 1/3/4/7: reservation lock, self-healing target curve, risk gate, attribution.
    Doc1-4: `liq` (current pool liquidity) is passed through so sub-$1K exits score as
    unsellable total loss instead of a phantom fill."""
    out = out if out is not None else []
    try:
        from bot.decision_engine.risk_gate import gate
        from bot.position_manager.scaleout import reserve, execute_stage, release, sell_delta
    except Exception as e:
        out.append(f"⚠ stage engine unavailable {sym}: {str(e)[:50]} — legacy path")
        return False
    p = b["positions"].get(mint) or b["positions"].get(sym)  # audit P0.1 ripple: book is MINT-keyed
    if not p:
        return False
    key = mint if mint in b["positions"] else sym  # canonical book key
    frac = 0.5 if stage == 1 else 1.0
    allowed, gnotes = gate({"action": "SCALE_OUT" if stage == 1 else "FULL_EXIT",
                            "sell_fraction": frac}, b, key, market_ts=market_ts, quote_ts=None)
    if allowed in ("HOLD", "WAIT_RECONCILE"):
        if _gate_emit_ok(sym, reason, how):
            out.append(f"GATE {sym}: {reason} blocked — {'; '.join(gnotes)}")
        return False
    # USER-APPROVED (Oct 20): EMERGENCY exits at >=90% confidence BYPASS the
    # reservation-hold. Previously a stale in-flight intent held the inventory
    # and the fill deferred tick after tick (ZEE7 bled 0.89x → 0.11x while the
    # engine screamed EMERGENCY_EXIT 92% every tick). High-confidence emergencies
    # sell SAME TICK: force-release any foreign intent's stale rows, then sell.
    is_emergency = ("EMERGENCY" in reason.upper() or "DEAD" in reason.upper() or "HARD_STOP" in reason.upper())
    conf = 0.0
    try:
        # Oct 20 audit: confidence lives in the MONITOR dict (positions.json —
        # position_monitor writes pos["last_decision"]), NOT in the book row.
        # The book row has no last_decision key at all → reading it alone
        # silently returned 0.0 and the force path never fired. Read monitor
        # first (it is passed in as `pos`), fall back to the book row.
        ld = (pos.get(mint) or {}).get("last_decision") or p.get("last_decision") or {}
        conf = float(ld.get("confidence") or 0.0)
    except Exception:
        pass
    force = is_emergency and conf >= 90.0
    if force:
        # Drain foreign reservations (rows NOT keyed to this exact intent) so the
        # sell_delta computed below sees the FULL live position. Kept rows: ours.
        keep_key = f"{key}:{how}{stage}"
        rows = p.get("reservations") or []
        kept = [r for r in rows if r.get("key") == keep_key]
        if len(kept) != len(rows):
            p["reservations"] = kept
            p["reserved_exit_usd"] = round(sum(float(r.get("usd") or 0) for r in kept), 4)
            save_book(b)
            out.append(f"FORCE-EXIT {sym}: emergency ≥90% — drained {len(rows) - len(kept)} stale reservation(s)")
    # capture for attribution before position pops
    peak = float(p.get("peak_price") or 0); trough = float(p.get("trough_price") or 0)
    initial = float(p.get("initial_usd") or p["entry_usd"])
    # Oct 20 audit fix: the monitor writes last_decision_px into POSITIONS.json
    # (mp dict) but NEVER into the book row — so every attribution had
    # decision_px=exit_px → is_exit 0/212. Read the monitor row as fallback.
    _dp = p.get("last_decision_px")
    if not _dp:
        _dp = (pos.get(mint) or {}).get("last_decision_px")
    decision_px = float(_dp or px)
    entry_px = float(p["entry_px"])
    # reserve exactly the sellable slice the target curve needs
    want = sell_delta(b, key, stage)
    if want <= 0.0:
        if _gate_emit_ok(sym, reason, how):
            out.append(f"GATE {sym}: nothing to sell for stage{stage} (already on/under target)")
        return False
    if not reserve(b, key, want, intent_key=f"{key}:{how}{stage}"):
        if _gate_emit_ok(sym, reason, how):
            out.append(f"GATE {sym}: reservation failed (in-flight)")
        return False
    # Fix 4 (Oct 19): for emergency exits, never book a deeper dump than the
    # tape confirms. Cap mult at the on-chain multiple when evidence says the
    # coin didn't crash as hard as the paper's stale quote claims.
    mult_used = mult
    if "EMERGENCY" in reason.upper() or "DEAD" in reason.upper() or "HARD_STOP" in reason.upper():
        try:
            from bot.position_manager.scaleout import _onchain_mult
            capped, src = _onchain_mult(mint, float(mult), float(p.get("entry_px") or 0))
            if capped is not None:
                mult_used = capped
                out.append(f"TAPE-CHECK {sym}: capped mult {mult:.2f}x -> {capped:.2f}x ({src})")
        except Exception:
            pass
    res = execute_stage(b, key, stage, mult_used, reason=reason, how=how, liq=liq)
    if not res:
        release(b, key, want, intent_key=f"{key}:{how}{stage}")
        if _gate_emit_ok(sym, reason, how):
            out.append(f"GATE {sym}: stage engine no-op")
        return False
    if b.get("trades"):
        b["trades"][-1]["how"] = f"{how}_{reason.lower()}" if how == "decision" else f"{how}"
    # Oct 21: print the BOOKED mult (post _onchain_mult/_confirm_gain caps), not
    # the raw claim — the DM line must match the ledger (CUPSEY printed 62.50x
    # but booked 10.0x because the gain cap fired).
    booked_mult = float(b["trades"][-1].get("mult") or mult_used) if b.get("trades") else mult_used
    save_book(b)
    if res["closed"]:
        pos.pop(sym, None)  # legacy symbol-key cleanup (no-op if keyed by mint)
        pos.pop(mint, None)  # audit P1 (Oct 18): positions.json is keyed by MINT; pop(sym) never removed it
        try:
            from bot.research.attribution import attribute, record_event
            path = [(0, entry_px), (1, peak if peak > 0 else entry_px), (2, px)]
            attribute(sym, p.get("mint"),
                      {"entry_px": entry_px, "peak_price": peak, "trough_price": trough, "initial_usd": initial},
                      realized_pnl=res["pnl"], exit_px=px, decision_px=decision_px, price_path=path)
            record_event(p.get("mint"), "POSITION_CLOSE",
                         {"symbol": sym, "reason": reason, "pnl": res["pnl"]})
        except Exception:
            pass
        em = "EMERGENCY" if "EMERGENCY" in reason.upper() or "DEAD" in reason.upper() else "EXIT"
        out.append(f"{em} {sym} @ {booked_mult:.2f}x -> sold ${res['sold_usd']:.2f} (pnl {res['pnl']:+.2f}) [{reason} → stage{stage}] | gate: {'; '.join(gnotes) or 'ok'}")
    else:
        # audit P1.1: after a partial stage sale, sync the remaining basis into the monitor
        # ledger so size-dependent analysis (PnL, exit-capacity) uses the true remaining size.
        if pos.get(mint) is not None:
            pos[mint]["size"] = float(res.get("remaining") or 0.0)
            pos[mint]["entry_usd"] = float(res.get("remaining") or 0.0)
        # Oct 21 FIX (anomaly #2): PARTIAL closes were never attributed — the DB
        # only got rows for res["closed"] full exits. PC's +9.83 clip, LENA's
        # -0.40 clip and METOO's +0.09 frac all vanished from position_attribution,
        # under-reporting real PnL. Attribute the tranche with partial=True.
        try:
            from bot.research.attribution import attribute, record_event
            _path_p = [(0, entry_px), (1, peak if peak > 0 else entry_px), (2, px)]
            attribute(sym, p.get("mint"),
                      {"entry_px": entry_px, "peak_price": peak, "trough_price": trough, "initial_usd": initial},
                      realized_pnl=res["pnl"], exit_px=px, decision_px=decision_px, price_path=_path_p,
                      partial=True)
            record_event(p.get("mint"), "PARTIAL_CLOSE",
                         {"symbol": sym, "reason": reason, "pnl": res["pnl"], "stage": stage})
        except Exception:
            pass
        out.append(f"CLIP {sym} @ {booked_mult:.2f}x -> sold ${res['sold_usd']:.2f} (pnl {res['pnl']:+.2f}) → remaining ${res['remaining']:.2f} (target-curve) | gate ok")
    return True

def tier_exits(mc):
    # SINGLE SOURCE OF TRUTH: position_monitor.EXIT_TIERS via tier_for().
    # (clip_pct, tp1, tp2, stop) — stop is a positive fraction of entry.
    from position_monitor import tier_for
    mc_floor, tp1, tp2, stop = tier_for(mc)
    return (0.50, tp1, tp2, -stop)

def auto_buy():
    """Buy NEW calls (<5m since channel post) with available cash.

    Doc1-6 (Blix-parity, verified by both systems' own measurements): NO model grade /
    AI score ever gates an entry. Entries come only from curated calls + the liquidity
    + entry-hygiene guards. AEGIS/scanner scores are post-entry analysis, never filters.
    """
    b = load_book()
    if not b.get("autobuy", True):
        return []
    bought = []
    import json as _j
    # audit P2 (Oct 18): dedupe by MINT only, never by ticker symbol — two
    # distinct contracts sharing a symbol (buy-all mandate) must both be bought.
    seen_mints = {p.get("mint") for p in b["positions"].values()}
    # audit P0.2: also skip mints already consumed as entries (durable ledger)
    consumed = _consumed_mints()
    try:
        lines = open("/home/agentuser/memecoin-quant/calls.jsonl").read().strip().split("\n")[-80:]
    except FileNotFoundError:
        return []
    # FIX #7: calls.jsonl is NOT chronological (54 order violations in last 100).
    # Sort ALL candidate lines by post time descending, then walk newest-first with a
    # proper age cutoff — never `break` on a stray old line in the middle.
    # P8 (Oct 20): retraction markers carry ts but NO dt — the old `if d.get("dt")`
    # filter silently dropped the marker and kept the ORIGINAL row buyable in the
    # cron path. Pre-scan ALL lines for retraction markers FIRST and build a
    # retracted-mint set; any call for those mints is skipped regardless of row.
    retracted_mints = set()
    for line in lines:
        try:
            _r = _j.loads(line)
        except Exception:
            continue
        if (_r.get("retracted") or _r.get("dead")) and _r.get("mint"):
            retracted_mints.add(_r["mint"])
    def _ts(d):
        # Prefer the epoch `ts` field (telethon writes msg.date.timestamp() — unambiguous).
        # Fall back to parsing `dt` (crawler records may only have dt).
        ts = d.get("ts")
        if isinstance(ts, (int, float)) and ts > 1000000000:
            return float(ts)
        try:
            return time.mktime(time.strptime((d.get("dt") or "")[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except Exception:
            return 0
    parsed = []
    for line in lines:
        try:
            d = _j.loads(line)
            # P8: accept rows by ts OR dt; strip retracted/dead rows out entirely
            if d.get("retracted") or d.get("dead"):
                continue
            if d.get("dt") or d.get("ts"):
                parsed.append((_ts(d), d))
        except Exception:
            continue
    parsed.sort(key=lambda x: x[0], reverse=True)  # newest first
    now = time.time()
    # Oct 21 timeout fix: auto_buy had NO deadline — many fresh calls x market_for
    # (3.4s each on feed-cache miss) could blow the supervisor's 120s subprocess
    # window BEFORE the main loop's TICK_DEADLINE even started. That's how 63
    # positions piled up overnight: call_notifier bought via entry_call, paper_tick
    # kept timing out, exits never ran. Bound the WHOLE buy scan to the same
    # 100s budget; the main loop deadline is set in main() AFTER auto_buy, so
    # auto_buy gets its own ceiling here.
    import time as _t_buy
    _BUY_DEADLINE = _t_buy.time() + 90   # < 120s supervisor window (auto_buy + main)
    for t, d in parsed:
        if _t_buy.time() > _BUY_DEADLINE:
            break  # budget: stop scanning new calls; the main loop still runs below
        age = now - t
        # FIX #7b: stop at first stale AFTER sorted — but skip anything malformed
        if age > 300:  # only live calls
            continue
        sym = d.get("symbol") or "?"
        mint = d["mint"]
        if d.get("retracted") or mint in retracted_mints:  # P8: deferred retraction kills the call
            continue
        if any(p["mint"] == mint for p in b["positions"].values()) or mint in seen_mints or mint in consumed:
            continue
        # FIX 3: minimum liquidity gate ($10K per user spec Oct 17) — below: skip+log
        from desk_common import market_for
        mkt = market_for(mint) or {}
        liq = ((mkt.get("liquidity") or {}).get("usd")) or 0
        # P12 (Oct 20): first-seen MC extension gate (mirror entry_call) — never
        # enter a coin already pumped >=1.8x past its call-time MC.
        try:
            mc_now = float((mkt.get("marketCap") or 0) or 0)
            mc_call = float((d.get("mc") or 0) or 0)
            if mc_now > 0 and mc_call > 0:
                ratio = mc_now / mc_call
                if ratio >= 1.8:
                    b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                        "why": f"mc_extended_{ratio:.1f}x"})
                    continue
        except Exception:
            pass
        # FIX 3a (Oct 20): pre-graduation curve coins report liq=0 by design
        # (bonding curve has no pool until $69K fill) — the $10K gate rejected
        # 22/33 of the best GRAD-DURING cohort. Only enforce the gate for coins
        # that should ALREADY have a pool (MC >= fill) or have real liq data.
        if liq < 10000 and not _is_curve_pre_grad(mkt):
            b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                "why": f"thin_liquidity_${liq:,.0f}"})
            continue
        px = mkt.get("priceUsd")
        if not px:
            continue
        # Doc1-3 (Blix parity, entry hygiene): never auto-buy a mint that is actively
        # dumping or under a seller flood — same spirit as blixtrade entryBlock (chase/
        # fall/seller-flood guards). The call is still recorded; we just don't enter it.
        chg = mkt.get("priceChange") or {}
        txns5 = (mkt.get("txns") or {}).get("m5") or {}
        b5, s5 = txns5.get("buys") or 0, txns5.get("sells") or 0
        if float(chg.get("m5") or 0) <= -5.0:
            b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                "why": f"dumping_5m_{chg.get('m5'):+.1f}%"})
            continue
        if s5 >= 6 and s5 > 2 * b5:
            b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                "why": f"seller_flood_{b5}B/{s5}S_5m"})
            continue
        # FIX 2: confluence size booster — 2+ channels => max size regardless of channel
        # Oct 19: real confluence from ledger (see entry_call for rationale)
        conf = 1
        try:
            sys.path.insert(0, DESK)
            from confluence import confluence_count
            conf = max(1, confluence_count(mint, window_s=1800))
        except Exception:
            conf = 1
        if conf >= 2:
            size = 25.0
        else:
            size = 20.0
        size = min(size, b["cash"])
        if size < 10:
            b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                "why": f"insufficient_cash_${b['cash']:.2f}"})
            break
        # Resolve real symbol from market data when the call record lacks one
        if sym == "?" or sym == "None":
            sym = ((mkt.get("baseToken") or {}).get("symbol")) or sym
        # FILL-TO-FILL (Oct 21, Charon A1): prefer a REAL Jupiter buy quote when
        # available (fill px = usd/out_tokens, impact + fee included); the 1% slip
        # is only the fallback when the quote is missing/stale. Runs AFTER size is
        # final so the quote sizes the exact order. Feed price stays the decision
        # mark — this is the FILL price only.
        FILL_LATENCY_SLIP_PCT = 1.0
        _fill = None
        try:
            from jupiter_estimator import quote_fill
            _fill = quote_fill(mint, usd=size, slippage_bps=500)
        except Exception:
            _fill = None
        if _fill:
            px = _fill[0]  # real per-token fill price incl. impact
            _fill_note = f"fill={_fill[1]:.1f}%imp ${_fill[2]:.2f}fee"
        else:
            px = float(px) * (1 + FILL_LATENCY_SLIP_PCT / 100)
            _fill_note = "fill=1%slip"
        b["positions"][mint] = {"mint": mint, "symbol": sym, "entry_px": float(px), "size_usd": size,
                               "entry_usd": size, "initial_usd": size, "opened": time.time(),
                               "state": "full", "age_at_buy": age, "reserved_exit_usd": 0.0,
                               "stages_done": [], "peak_price": float(px), "trough_price": float(px),
                               # audit P0.1 (Oct 18): book is keyed by MINT, not ticker — two
                               # contracts sharing a symbol can both be held (buy-all mandate).
                               # audit P1 (Oct 18): persist IMMUTABLE entry baselines so the
                               # monitor's liq-collapse / volume-drying rules compare entry->now
                               "entry_liq": ((mkt.get("liquidity") or {}).get("usd")) or 0,
                               "entry_vol_h1": (mkt.get("volume") or {}).get("h1") or 0,
                               "entry_mc": float(mkt.get("marketCap") or 0),
                               "fill_note": _fill_note}
        _note_fill(mint, age)  # Oct 20: populate coins.fill_time_min (was 0% filled)
        b["cash"] = round(b["cash"] - size, 2)
        seen_mints.add(mint)
        # audit P0.2 (Oct 18): durable consumed-call ledger — a quick stop/clip within the
        # 5-min freshness window must NOT re-buy the same signal after the position closes.
        try:
            with open(CONSUMED_FILE, "a") as cf:
                cf.write(json.dumps({"mint": mint, "symbol": sym, "channel": d.get("channel"),
                                     "msg_id": d.get("msg_id"), "ts": time.time()}) + "\n")
        except Exception:
            pass
        bought.append(f"BUY ${sym} @ ${px} (${size:.0f}, call {age/60:.0f}m old, {conf}ch{' ⚡confluence' if conf >= 2 else ''}, {_fill_note}) {mint[:10]}")
        import position_monitor as _pm
        pos = _pm.load_positions()
        # audit P1.1 (Oct 18): copy entry baselines into the monitor ledger so the monitor
        # never infers entry conditions from current market data.
        pos[mint] = {"symbol": sym, "entry_px": float(px), "size": size, "ts": time.time(), "paper": True,
                     "entry_liq": ((mkt.get("liquidity") or {}).get("usd")) or 0,
                     "entry_vol_h1": (mkt.get("volume") or {}).get("h1") or 0,
                     "entry_mc": float(mkt.get("marketCap") or 0)}
        _pm.save_positions(pos)
        save_book(b)
    return bought


def entry_call(d, b=None, aegis_score=None):
    """Oct 19: DIRECT single-call paper entry (used by notifier after AEGIS card).

    Applies the same guards as auto_buy's loop (liq >= $10K gate, no 5m dump, no
    seller flood, fill-latency slip, dedupe vs open positions + consumed ledger) but
    for ONE call dict — so the DM notifier can trigger the buy immediately after the
    AEGIS deep card, without waiting for paper_tick's next cron.
    Returns (bought_line, skip_reason|None)."""

    # Oct 20 audit policy: AEGIS is a veto/boost ONLY (never a rank filter).
    # score < 30 = hard veto (the only band with real predictive power);
    # score >= 70 = mild size boost; the 30-70 zone changes nothing.
    if aegis_score is not None:
        try:
            aegis_score = float(aegis_score)
        except Exception:
            aegis_score = None
    import json as _j
    b = b if b is not None else load_book()
    if not b.get("autobuy", True):
        return None, "autobuy_disabled"
    mint = d.get("mint")
    sym = d.get("symbol") or "?"
    if not mint:
        return None, "no_mint"
    if d.get("retracted"):
        return None, "retracted"
    # FRESHNESS GATE (audit Oct 19 — the killer): entry_call (instant_trigger path)
    # had NO age check while auto_buy did (line 162). Stale calls (observed 125-241m
    # old) were being bought hours after the channel posted — after the pump died,
    # straight into UNSELLABLE full losses. Only calls <5 min old may enter.
    ts = d.get("ts")
    if isinstance(ts, (int, float)) and ts > 1000000000:
        age = time.time() - ts
        if age > 300:
            return None, f"stale_call_{age/60:.0f}m"
    # dedupe: open positions + consumed ledger — SEPARATE reasons (Oct 21, user:
    # "Separate them") so the card says WHICH guard fired: still holding the coin
    # vs already traded+exited it once. Both are no-rebuy, but they answer
    # different questions (current exposure vs historical consumption).
    if any(p.get("mint") == mint for p in b["positions"].values()):
        return None, "already_held"
    if mint in _consumed_mints():
        return None, "already_consumed"
    # AEGIS veto (audit policy Oct 20): <30 = hard veto, 70+ = mild size boost.
    # Applies AFTER dedupe so consumed/holding mints aren't double-logged.
    if aegis_score is not None and aegis_score < 30:
        return None, f"aegis_veto_score_{aegis_score:.0f}"
    aegis_boost = 1.0
    if aegis_score is not None and aegis_score >= 70:
        aegis_boost = 1.25  # mild position-size boost on strong contract/live signals
    mkt = market_for(mint) or {}
    liq = ((mkt.get("liquidity") or {}).get("usd")) or 0
    # P12 (Oct 20): first-seen MC extension gate — never chase a coin that has
    # already pumped >=1.8x past the MC printed at the channel call (M_now/M_0).
    # The tape says these are exhausted entries (the pump already happened).
    try:
        mc_now = float((mkt.get("marketCap") or 0) or 0)
        mc_call = float((d.get("mc") or 0) or 0)
        if mc_now > 0 and mc_call > 0:
            ratio = mc_now / mc_call
            if ratio >= 1.8:
                b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                    "why": f"mc_extended_{ratio:.1f}x"})
                save_book(b)
                return None, f"mc_extended_{ratio:.1f}x"
    except Exception:
        pass
    # FIX 3a (Oct 20): same pre-graduation exemption as auto_buy — sub-$69K curve
    # coins legitimately report liq=0; the gate must not kill the best cohort.
    if liq < 10000 and not _is_curve_pre_grad(mkt):
        b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                            "why": f"thin_liquidity_${liq:,.0f}"})
        save_book(b)
        return None, f"thin_liquidity_${liq:,.0f}"
    px = mkt.get("priceUsd")
    if not px:
        return None, "no_price"
    # entry hygiene (Doc1-3)
    chg = mkt.get("priceChange") or {}
    txns5 = (mkt.get("txns") or {}).get("m5") or {}
    b5, s5 = txns5.get("buys") or 0, txns5.get("sells") or 0
    if float(chg.get("m5") or 0) <= -5.0:
        b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                            "why": f"dumping_5m_{chg.get('m5'):+.1f}%"})
        save_book(b)
        return None, f"dumping_5m_{chg.get('m5'):+.1f}%"
    if s5 >= 6 and s5 > 2 * b5:
        b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                            "why": f"seller_flood_{b5}B/{s5}S_5m"})
        save_book(b)
        return None, f"seller_flood_{b5}B/{s5}S_5m"
    FILL_LATENCY_SLIP_PCT = 1.0  # defined here; used in the fill block after size is final
    if sym == "?" or sym == "None":
        sym = ((mkt.get("baseToken") or {}).get("symbol")) or sym
    # Oct 19 fix: REAL cross-channel confluence (ledger-based). The listener
    # dedupes calls.jsonl by mint, so `d["channels"]`/`d["confluence"]` were
    # always empty/1 — the 2nd channel posting the same mint was invisible.
    # confluence.confluence_count() reads the full ledger: N channels in 30m.
    conf = 1
    try:
        sys.path.insert(0, DESK)
        from confluence import confluence_count
        conf = max(1, confluence_count(mint, window_s=1800))
    except Exception:
        conf = 1
    size = 25.0 if conf >= 2 else 20.0
    size *= aegis_boost  # 70+ AEGIS: +25% position (Oct 20 policy)
    size = min(size, b["cash"])
    if size < 10:
        b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                            "why": f"insufficient_cash_${b['cash']:.2f}"})
        save_book(b)
        return None, f"insufficient_cash_${b['cash']:.2f}"
    # FILL-TO-FILL (Oct 21, Charon A1): quote AFTER size is final (aegis_boost/
    # confluence/cash all applied) so the real fill price sizes the exact order.
    FILL_LATENCY_SLIP_PCT = 1.0
    _fill = None
    try:
        from jupiter_estimator import quote_fill
        _fill = quote_fill(mint, usd=size, slippage_bps=500)
    except Exception:
        _fill = None
    if _fill:
        px = _fill[0]  # real per-token fill price incl. impact
        _fill_note = f"fill={_fill[1]:.1f}%imp ${_fill[2]:.2f}fee"
    else:
        px = float(px) * (1 + FILL_LATENCY_SLIP_PCT / 100)
        _fill_note = "fill=1%slip"
    # P11 (Oct 20): close the risk-gate bypass — the instant_trigger path called
    # entry_call directly and never consulted the exposure cap that risk_gate
    # enforces on the cron path. Same $200 face exposure limit here.
    try:
        from bot.decision_engine.risk_gate import MAX_OPEN_EXPOSURE_USD
        open_exposure = sum(float(p.get("entry_usd") or p.get("size_usd") or 0)
                            for p in b["positions"].values())
        if open_exposure + size > MAX_OPEN_EXPOSURE_USD:
            b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                "why": f"exposure_cap_${open_exposure + size:.0f}"})
            save_book(b)
            return None, f"exposure_cap_${open_exposure + size:.0f}"
    except Exception:
        pass
    age = (time.time() - float(d.get("ts") or time.time())) / 60
    b["positions"][mint] = {"mint": mint, "symbol": sym, "entry_px": float(px), "size_usd": size,
                           "entry_usd": size, "initial_usd": size, "opened": time.time(),
                           "state": "full", "age_at_buy": max(0, age), "reserved_exit_usd": 0.0,
                           "stages_done": [], "peak_price": float(px), "trough_price": float(px),
                           "entry_liq": liq,
                           "entry_vol_h1": (mkt.get("volume") or {}).get("h1") or 0,
                           "entry_mc": float(mkt.get("marketCap") or 0),
                           "fill_note": _fill_note}
    _note_fill(mint, max(0, age))  # Oct 20: populate coins.fill_time_min (was 0% filled)
    b["cash"] = round(b["cash"] - size, 2)
    try:
        with open(CONSUMED_FILE, "a") as cf:
            cf.write(_j.dumps({"mint": mint, "symbol": sym, "channel": d.get("channel"),
                               "msg_id": d.get("msg_id"), "ts": time.time()}) + "\n")
    except Exception:
        pass
    import position_monitor as _pm
    pos = _pm.load_positions()
    pos[mint] = {"symbol": sym, "entry_px": float(px), "size": size, "ts": time.time(), "paper": True,
                 "entry_liq": liq,
                 "entry_vol_h1": (mkt.get("volume") or {}).get("h1") or 0,
                 "entry_mc": float(mkt.get("marketCap") or 0)}
    _pm.save_positions(pos)
    save_book(b)
    line = f"🛒 PAPER BUY ${sym} @ ${px} (${size:.0f}, call {max(0,age):.0f}m old, {conf}ch{' ⚡confluence' if conf >= 2 else ''}, {_fill_note}) {mint[:10]}"
    return line, None


def main():
    # F4 (Oct 19): global tick budget. Each provider call can burn 20s x 3 retries;
    # under position load the old code blew the supervisor's 120s subprocess timeout
    # and exits never executed. Now: hard deadline for the whole tick — positions past
    # the budget are deferred (they run next tick) instead of blocking the book.
    TICK_DEADLINE = time.time() + 100   # < supervisor timeout (120s) — keeps the subprocess alive
    for line in auto_buy():
        print(line)
    b = load_book()
    pos = pm.load_positions()  # SINGLE authoritative monitor snapshot (audit P1 Oct 18)
    out = []
    _prices = {}   # mint -> priceUsd captured in the main loop (equity reuse, no refetch)
    for mint, p in list(b["positions"].items()):
        if time.time() > TICK_DEADLINE:
            out.append(f"⏱ tick budget hit — deferring {len(b['positions']) - list(b['positions']).index(mint)} position(s) to next tick")
            break
        sym = p.get("symbol") or mint[:8]  # display symbol (book key is MINT — P0.1)
        mkt_ts = time.time()               # audit P1.2 (Oct 18): market observation ts — captured
                                            # the moment the snapshot is fetched, not at decision end
        mkt = {}
        try:
            # PumpData feed FIRST (Oct 20): free keyless WS cache row, no network.
            # Falls back to DexScreener when the cache is missing/stale.
            mkt = market_for(mint, timeout=10, retries=1) or {}
        except Exception as e:
            # 429-blip resilience (Oct 19): ONE provider failure must not kill the
            # whole tick (that silently froze the desk for 25 min after a rate-limit
            # burst — no heartbeat, no exits processed). Defer this position and
            # let the next tick retry; the book stays live for other positions.
            out.append(f"? {sym}: market err ({type(e).__name__}) — deferred to next tick")
            continue
        px = mkt.get("priceUsd")
        if not px:
            if _gate_emit_ok(sym, "no_market", "main"):
                out.append(f"? {sym}: no market")
            continue
        px = float(px)
        _prices[mint] = px
        mc = mkt.get("marketCap") or 0
        mult = px / p["entry_px"]
        entry_usd = p["size_usd"]
        cur_usd = entry_usd * mult
        state = p.get("state", "full")

        # FIX 1 (Oct 21): FEED-SPEED STOP — runs EVERY tick at the feed-cache
        # price, BEFORE the (cadence-gated) deep analyze. A position at/under
        # its tier stop or hard floor sells THIS tick, no matter the analyze
        # cadence (NORMAL = 300s, BACKGROUND = 900s — a dump used to ride from
        # 0.75x to 0.09x between deep analyzes; 14 below-stop trades, -$110).
        # Never fires on green (thresholds are at 0.75x / 0.50x).
        try:
            from position_monitor import tier_for as _tf
            _tier = _tf(float(mc) or 0)
            _stop_at = 1.0 - float(_tier[3] or 0.25)
        except Exception:
            _stop_at = 0.75
        _floor_at = float(p.get("hard_stop_mult") or 0.50)
        if mult <= _floor_at:
            # Hard floor is the DEEPER stop — check it first so a 0.30x
            # gap-through books HARD_STOP (crashed through the floor),
            # not the shallower TIER_STOP label.
            if mint in pos: pos[mint]["hard_stop_triggered"] = True
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="HARD_STOP", how="stop", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
            # Stop intent fired but the fill is blocked (gate/reservation in-
            # flight) — DEFER, never let the decision engine downgrade a stop.
            save_book(b); pm.save_positions(pos)
            continue
        if mult <= _stop_at:
            if mint in pos: pos[mint]["tier_stop_triggered"] = True
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="TIER_STOP", how="stop", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
            save_book(b); pm.save_positions(pos)
            continue

        # ---- decision-contract execution (doc-1): monitor's analyze() drives exits ----
        # MFE/MAE high-water marks (Build 7: attribution needs peak/trough)
        if px > float(p.get("peak_price") or 0): p["peak_price"] = px
        if p.get("trough_price") is None or px < float(p["trough_price"]): p["trough_price"] = px
        # Build 5: tier budgeting — BACKGROUND moonbags skip deep analyze most ticks
        try:
            from bot.orchestration.monitor_tiers import classify, CADENCE_S
            # audit P1.5 (Oct 18): classify with the LATEST market result (mult) so a
            # moonbag that is losing money is never demoted to BACKGROUND on None mult.
            tier = classify(p, r={"mult": mult})
            p["tier"] = tier
        except Exception:
            tier = "NORMAL"
        # audit P1.5 (Oct 18): persistent round-robin counter in the book so a BACKGROUND
        # position at a fixed index isn't skipped every single run. Keep it in the position
        # row (survives restarts) instead of a per-invocation tick_no reset.
        tick_no = int(p.get("_tick_no") or 0)
        p["_tick_no"] = (tick_no + 1) % 10
        # Oct 20 audit: wire monitor_tiers.CADENCE_S (was dead constant). A position
        # gets full deep analyze only as often as its tier allows:
        #   CRITICAL 15s / HIGH 60s / NORMAL 300s / BACKGROUND 900s.
        # The old rule skipped BACKGROUND 9/10 ticks regardless of elapsed time;
        # now every tier honors its cadence via a persisted last_deep_ts, and the
        # round-robin counter is only a fallback for the first BACKGROUND tick.
        last_deep = float(p.get("_last_deep_ts") or 0)
        now_t = time.time()
        cad_s = CADENCE_S.get(tier, 300)
        skip_deep = (now_t - last_deep) < cad_s
        if tier == "BACKGROUND" and tick_no % 10 != 0 and skip_deep:
            skip_deep = True  # keep the coarse backward-skip for moonbags
        elif not skip_deep and last_deep == 0:
            p["_last_deep_ts"] = now_t
        decision_action = None
        if not skip_deep:
            p["_last_deep_ts"] = now_t  # deep analyze ran — stamp for cadence gating
            try:
                mp = pos.get(mint) or {"entry_px": p["entry_px"], "size": entry_usd,
                                           "ts": p.get("opened", time.time()), "paper": True,
                                           "symbol": sym, "entry_liq": p.get("entry_liq"),
                                           "entry_vol_h1": p.get("entry_vol_h1")}
                # Oct 21 (Charon topBlastRisk): give monitor the high-water mark so
                # the ATH_PROXIMITY clip works from the same peak the book tracks.
                if p.get("peak_price"):
                    mp["peak_price"] = p["peak_price"]
                # audit P1.1 (Oct 18): ALWAYS backfill immutable entry baselines from the book
                # into the monitor record so a stale/missing monitor row never infers entry
                # conditions from current market data.
                for k in ("entry_liq", "entry_vol_h1", "entry_mc"):
                    if mp.get(k) is None and p.get(k) is not None:
                        mp[k] = p[k]
                if mp.get("size") is None and p.get("entry_usd") is not None:
                    mp["size"] = p["entry_usd"]
                r = pm.analyze(mint, mp)
                pos[mint] = mp  # merge monitor metadata into the SAME dict saved at end
                if r.get("ok"):
                    decision_action = r.get("action")
            except Exception:
                r = None
        if decision_action in ("CLIP_50", "CLIP_100", "EXIT_ALL", "EMERGENCY_EXIT", "DEAD",
                               "SCALE_OUT", "FULL_EXIT"):
            # audit P0-1 (Oct 18): decision engine returns SCALE_OUT/FULL_EXIT; the
            # executor previously only knew CLIP_50/CLIP_100/EXIT_ALL/EMERGENCY_EXIT/DEAD,
            # so contract-driven partial exits were silently ignored.
            cd = mp.get("last_decision") or {}
            cfrac = float(cd.get("sell_fraction") or 0.0)
            if decision_action in ("FULL_EXIT", "EXIT_ALL", "EMERGENCY_EXIT", "DEAD") or cfrac >= 0.99:
                stage, frac = 3, 1.0
            elif decision_action in ("SCALE_OUT", "CLIP_50", "CLIP_100") or cfrac > 0.0:
                # audit P0.3 (Oct 18): honor the EXACT requested sell_fraction of the
                # remaining basis when the decision proposes a specific fraction; only
                # fall back to coarse stages for legacy CLIP labels without a fraction.
                if cfrac > 0.0:
                    stage, frac = "frac", cfrac
                else:
                    stage, frac = 1, 0.5 if decision_action == "CLIP_50" else 1.0
                    if frac >= 0.99:
                        stage = 3
            else:
                stage, frac = None, 0.0
            if stage == "frac":
                # exact-fraction sale (P0.3): sells cfrac of remaining basis
                frac_failed = False
                try:
                    from bot.position_manager.scaleout import execute_fraction
                    res = execute_fraction(b, mint, frac, mult, reason=decision_action,
                                           how="decision_frac", liq=((mkt.get("liquidity") or {}).get("usd") or 0))
                except Exception:
                    res = None
                    frac_failed = True
                if res:
                    # P1.1: sync remaining size into the monitor ledger
                    if not res.get("closed"):
                        pos[mint]["size"] = res.get("remaining", 0.0)
                    # Oct 21 FIX (anomaly #2): the frac path bypasses sell_via_stage
                    # entirely, so NEITHER partial nor full frac closes ever reached
                    # position_attribution (METOO's +0.09 frac had no DB row). All
                    # REALIZED frac closes get attributed here (partial flag per close).
                    try:
                        from bot.research.attribution import attribute, record_event
                        fpeak = float(p.get("peak_price") or px)
                        ftrough = float(p.get("trough_price") or px)
                        f_pp = [(0, float(p["entry_px"])), (1, fpeak if fpeak > 0 else p["entry_px"]), (2, px)]
                        attribute(sym, p.get("mint"),
                                  {"entry_px": float(p["entry_px"]), "peak_price": fpeak,
                                   "trough_price": ftrough, "initial_usd": float(p.get("initial_usd") or p.get("entry_usd") or 0)},
                                  realized_pnl=res["pnl"], exit_px=px, price_path=f_pp,
                                  partial=not res.get("closed"))
                        record_event(p.get("mint"), "POSITION_CLOSE" if res.get("closed") else "PARTIAL_CLOSE",
                                     {"symbol": sym, "reason": decision_action, "pnl": res["pnl"],
                                      "frac": frac})
                    except Exception:
                        pass
                    if res.get("closed"):
                        pos.pop(mint, None)
                    save_book(b); pm.save_positions(pos)
                    continue
                # P0 (Oct 20): a None from execute_fraction is a legit no-op (dust
                # tranche / gate hold / reservation in-flight) — NEVER fall through to
                # the legacy inline branch which BOOKS the sale as if it succeeded.
                if not frac_failed:
                    save_book(b); pm.save_positions(pos)
                    if _gate_emit_ok(sym, decision_action, "decision"):
                        out.append(f"GATE {sym}: fraction sell no-op (dust/hold/in-flight) — nothing booked")
                    continue
                stage = None  # only engine-unavailable reaches the legacy fallback
            if stage:
                res = sell_via_stage(b, pos, sym, mint, stage, mult, px,
                                     reason=decision_action, how="decision", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0))
                if res:
                    # P1.1: sync remaining size into the monitor ledger after any partial/full
                    save_book(b); pm.save_positions(pos)
                    continue
                # P0 (Oct 20): sell_via_stage returning False is a GATE HOLD / no-op
                # (reservation failed, nothing to sell, engine verdict) — the legacy
                # inline branch below must run ONLY when the stage engine is genuinely
                # unavailable (import error), never to force a booked sale.
                if _gate_emit_ok(sym, decision_action, "decision"):
                    out.append(f"GATE {sym}: stage engine no-op — nothing booked")
                save_book(b); pm.save_positions(pos)
                continue
            # LESS-CODE (Oct 21): NO legacy inline booking path. There used to be
            # a SECOND book-writer here (the +$30.8M phantom culprit) that only ran
            # when the stage engine reported "unavailable" — a duplicated write path
            # that drifted from the capped engine and booked uncapped mults. If the
            # stage engine is genuinely unavailable (import error), DEFER the sell
            # loudly and keep the position for the next tick — never book through a
            # hand-rolled path that can't share scaleout's MAX_BOOKED_GAIN_MULT
            # and _confirm_gain safeguards. One writer, verified invariants.
            elif stage is None and "unavailable" in (out[-1] if out else ""):
                if _gate_emit_ok(sym, decision_action, "decision"):
                    out.append(f"ENGINE {sym}: stage engine unavailable — sale DEFERRED (kept for next tick)")
                save_book(b); pm.save_positions(pos)
                continue
            # gate blocked or no-op -> fall through to tier engine for another opinion
            # (do NOT double-sell: reservation failed means in-flight; just skip this tick)
            if "reservation failed" in (out[-1] if out else ""):
                save_book(b); pm.save_positions(pos)
                continue
        # ---- fallback tier engine (Build 5: BACKGROUND moonbags skip deep analyze) ----
        clip1, tp1, tp2, stop = tier_exits(mc)
        action = "HOLD"
        # Oct 21 (Charon A4): TIME EXITS in the fallback engine too — MAX_HOLD
        # replaces the old flat 3h STALE rule: quick flips close at MAX_HOLD_S
        # (default 5min), conviction holds get MAX_HOLD_CONVICTION_S (2h). The
        # monitor engine fires these for deep-analyzed positions; this keeps
        # BACKGROUND moonbags (which skip deep analyze) on the same clock.
        try:
            from position_monitor import MAX_HOLD_S, MAX_HOLD_CONVICTION_S, SIDEWAYS_S, SIDEWAYS_PNL_PCT, max_hold_for
        except Exception:
            MAX_HOLD_S, MAX_HOLD_CONVICTION_S, SIDEWAYS_S, SIDEWAYS_PNL_PCT, max_hold_for = 1800, 7200, 600, 2.0, (lambda mc: 1800)
        age_s = time.time() - p.get("opened", time.time())
        _conv = bool(p.get("conviction")) or p.get("strategy") == "conviction_hold"
        hold_s = MAX_HOLD_CONVICTION_S if _conv else max_hold_for(float(mc) or 0)
        stale_reason = None
        # FIX 2 (Oct 21): MAX_HOLD fires ONLY on losers/flat (mult <= 1.0) —
        # winners ride to TP2/clips (same rule as the monitor engine).
        if age_s >= hold_s and mult <= 1.0:
            stale_reason = f"MAX_HOLD ({age_s/60:.0f}min >= {hold_s/60:.0f}min at {mult:.2f}x)"
        elif age_s >= hold_s and mult > 1.0:
            stale_reason = None  # green + old = ride (FIX 2)
        elif state == "full" and age_s >= SIDEWAYS_S and abs((mult - 1) * 100) < SIDEWAYS_PNL_PCT:
            stale_reason = f"SIDEWAYS ({age_s/60:.0f}min, |pnl| {abs((mult-1)*100):.1f}% < {SIDEWAYS_PNL_PCT}%)"
        if stale_reason:
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason=stale_reason, how="time", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        # FIX 1 (Oct 21): tier stop + hard floor apply to BOTH states (the old
        # state=="full" gate let half-state positions ride past 0.75 to 0.50;
        # the old half-state 'TP' branch even booked a 0.50 dump as reason TP).
        if mult <= 0.50:
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="HARD_STOP", how="stop", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        if mult <= 1 + stop:
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="TIER_STOP", how="stop", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        if state == "full" and mult >= tp1:
            if sell_via_stage(b, pos, sym, mint, 1, mult, px, reason="CLIP1", how="clip1", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        elif state == "half" and mult >= tp2:
            # FIX 1 (Oct 21): ONLY the honest take-profit — the old `or mult <= 0.50`
            # arm booked a crashed half position as reason "TP". Hard floor above
            # now catches that dump with the honest HARD_STOP label.
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="TP", how="tp", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        plan_s = f"sell half at +{(tp1-1)*100:.0f}%, sell all at +{(tp2-1)*100:.0f}%, cut losses at {abs(stop)*100:.0f}%"
        out.append(f"• ${sym}: worth ${cur_usd:.2f} ({'+' if mult>=1 else ''}{(mult-1)*100:.0f}% since buy) — {action}. Plan: {plan_s}")
    save_book(b); pm.save_positions(pos)
    # FIX #6: recompute equity from book (staged executor already updated cash/realized)
    # TIMEOUT FIX (Oct 20): the old loop re-fetched dex_pair for EVERY remaining
    # position with NO deadline — with 74 open positions it blew the supervisor's
    # 120s subprocess timeout on every tick (desk silent for hours, heartbeats
    # stale). Now reuse the prices already fetched in the main loop.
    openval = 0.0
    for mint, p in b["positions"].items():
        px = _prices.get(mint)
        if px and p.get("entry_px"):
            openval += p["size_usd"] * float(px) / p["entry_px"]
    cash = b.get("cash", 0.0)
    eq = cash + openval
    b["realized"] = round(b.get("realized", 0.0), 2)
    b["cash"] = round(cash, 2)
    b["equity"] = round(eq, 2)
    save_book(b)
    gain = eq - b["start"]
    gpct = gain / b["start"] * 100
    need = 500 - eq
    mood = "🎯 ahead!" if gain > 0 else ("⚠️ behind" if gain > -15 else "🚨 deep red")
    held = []
    for mint, p in b.get("positions", {}).items():
        psym = p.get("symbol") or mint[:8]
        psize = float(p.get("size_usd") or 0)
        held.append(f"${psym} (${psize:.2f})")
    held_note = f" Held: {', '.join(held)}." if held else ""
    # Oct 21 (mortdeus riskManager-style): risk level for the DM line — daily
    # realized PnL proximity to the $20 daily stop, open-position concentration,
    # and win rate. Gives the desk a visible "stop for the day" state.
    try:
        t0_day = time.time() - time.time() % 86400
        day_pnl = round(sum(float(t.get("pnl") or 0) for t in b.get("trades", [])
                            if float(t.get("ts") or 0) >= t0_day), 2)
        day_loss_pct = min(100, max(0, -day_pnl / 20 * 100))  # /$20 daily stop
        n_open = len(b.get("positions", {}))
        conc = min(100, n_open / 5 * 100)  # /max 5 positions
        tr = b.get("trades", [])
        wr = (sum(1 for t in tr if float(t.get("pnl") or 0) > 0) / max(1, len(tr)) * 100)
        risk = 0
        risk += day_loss_pct * 0.5
        risk += conc * 0.3
        if wr < 30: risk += 25
        elif wr < 40: risk += 12
        lvl = "HIGH" if risk >= 60 else ("MEDIUM" if risk >= 30 else "LOW")
        day_note = f"⚖️ risk {lvl} (day ${day_pnl:+.2f}/−$20, {n_open}/5 open, wr {wr:.0f}%)"
        if day_pnl <= -20:
            day_note += " 🛑 DAILY STOP — no new entries 24h"
        elif day_pnl <= -16:
            day_note += " ⚠️ near daily stop"
    except Exception:
        day_note = ""
    out.append(f"💼 Paper account: ${eq:.2f} (started ${b['start']:.0f}, {'+' if gain>=0 else ''}{gpct:.0f}% — {mood}). Waiting cash: ${cash:.2f}. Coins held: {len(b['positions'])}.{held_note} Still need ${need:.2f} to reach the $500 goal.")
    if day_note:
        out.append(day_note)
    blob = "\n".join(out)
    # 3-min heartbeat: DM the book summary even with no action (user-approved Oct 19;
    # user requested 1m → 3m cadence). Action lines (BUY/EXIT/skip) still send immediately.
    HB = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tick_heartbeat")
    now = time.time()
    due = True
    try:
        due = (now - float(open(HB).read().strip() or 0)) >= 180
    except Exception:
        due = True
    acted = any(("BOUGHT" in l or "BUY" in l or "SOLD" in l or "EXIT" in l or "skipped" in l or "HOLD" in l or "clip" in l.lower()) for l in out)
    # 3-min heartbeat only — open positions alone must NOT force a per-tick DM
    # (that flooded the chat every 60s; action lines still send instantly via acted).
    if due or acted:
        if due:
            open(HB, "w").write(str(now))
        try:
            sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
            from call_notifier import send_dm
            send_dm(blob)
        except Exception:
            pass  # cron deliver (telegram DM) is the fallback channel
    print(blob)

if __name__ == "__main__":
    main()

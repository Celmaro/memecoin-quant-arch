#!/usr/bin/env python3
"""Paper-trade tick: mark positions to market, apply MC-adaptive exit engine, execute paper clips/exits."""
import sys, json, time, os
sys.path.insert(0, "/home/agentuser/memecoin-quant")
from desk_common import dex_pair
import position_monitor as pm

BOOK = "/home/agentuser/memecoin-quant/paper_book.json"
CONSUMED_FILE = "/home/agentuser/memecoin-quant/consumed_calls.jsonl"

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
    import tempfile
    tmp = BOOK + ".tmp"
    with open(tmp, "w") as f:
        json.dump(b, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, BOOK)

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
        out.append(f"GATE {sym}: {reason} blocked — {'; '.join(gnotes)}")
        return False
    # capture for attribution before position pops
    peak = float(p.get("peak_price") or 0); trough = float(p.get("trough_price") or 0)
    initial = float(p.get("initial_usd") or p["entry_usd"])
    decision_px = float(p.get("last_decision_px") or px)
    entry_px = float(p["entry_px"])
    # reserve exactly the sellable slice the target curve needs
    want = sell_delta(b, key, stage)
    if want <= 0.0:
        out.append(f"GATE {sym}: nothing to sell for stage{stage} (already on/under target)")
        return False
    if not reserve(b, key, want, intent_key=f"{key}:{how}{stage}"):
        out.append(f"GATE {sym}: reservation failed (in-flight)")
        return False
    res = execute_stage(b, key, stage, mult, reason=reason, how=how, liq=liq)
    if not res:
        release(b, key, want)
        out.append(f"GATE {sym}: stage engine no-op")
        return False
    if b.get("trades"):
        b["trades"][-1]["how"] = f"{how}_{reason.lower()}" if how == "decision" else f"{how}"
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
        out.append(f"{em} {sym} @ {mult:.2f}x -> sold ${res['sold_usd']:.2f} (pnl {res['pnl']:+.2f}) [{reason} → stage{stage}] | gate: {'; '.join(gnotes) or 'ok'}")
    else:
        # audit P1.1: after a partial stage sale, sync the remaining basis into the monitor
        # ledger so size-dependent analysis (PnL, exit-capacity) uses the true remaining size.
        if pos.get(mint) is not None:
            pos[mint]["size"] = float(res.get("remaining") or 0.0)
            pos[mint]["entry_usd"] = float(res.get("remaining") or 0.0)
        out.append(f"CLIP {sym} @ {mult:.2f}x -> sold ${res['sold_usd']:.2f} (pnl {res['pnl']:+.2f}) → remaining ${res['remaining']:.2f} (target-curve) | gate ok")
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
            if d.get("dt"):
                parsed.append((_ts(d), d))
        except Exception:
            continue
    parsed.sort(key=lambda x: x[0], reverse=True)  # newest first
    now = time.time()
    for t, d in parsed:
        age = now - t
        # FIX #7b: stop at first stale AFTER sorted — but skip anything malformed
        if age > 300:  # only live calls
            continue
        sym = d.get("symbol") or "?"
        mint = d["mint"]
        if d.get("retracted"):
            continue
        if any(p["mint"] == mint for p in b["positions"].values()) or mint in seen_mints or mint in consumed:
            continue
        # FIX 3: minimum liquidity gate ($10K per user spec Oct 17) — below: skip+log
        from desk_common import dex_pair
        mkt = dex_pair(mint) or {}
        liq = ((mkt.get("liquidity") or {}).get("usd")) or 0
        if liq < 10000:
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
        # Doc2-8 (fill realism): paper entries fill at a small latency slip above the
        # observed price, modeling front-running by same-second bots. 1% slip.
        FILL_LATENCY_SLIP_PCT = 1.0
        px = float(px) * (1 + FILL_LATENCY_SLIP_PCT / 100)
        # Resolve real symbol from market data when the call record lacks one
        if sym == "?" or sym == "None":
            sym = ((mkt.get("baseToken") or {}).get("symbol")) or sym
        # FIX 2: confluence size booster — 2+ channels => max size regardless of channel
        conf = d.get("confluence") or len(d.get("channels") or [1])
        if conf >= 2:
            size = 25.0
        else:
            size = 20.0
        size = min(size, b["cash"])
        if size < 10:
            b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                                "why": f"insufficient_cash_${b['cash']:.2f}"})
            break
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
                               "entry_mc": float(mkt.get("marketCap") or 0)}
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
        bought.append(f"BUY ${sym} @ ${px} (${size:.0f}, call {age:.0f}m old) {mint[:10]}")
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


def entry_call(d, b=None):
    """Oct 19: DIRECT single-call paper entry (used by notifier after AEGIS card).

    Applies the same guards as auto_buy's loop (liq >= $10K gate, no 5m dump, no
    seller flood, fill-latency slip, dedupe vs open positions + consumed ledger) but
    for ONE call dict — so the DM notifier can trigger the buy immediately after the
    AEGIS deep card, without waiting for paper_tick's next cron.
    Returns (bought_line, skip_reason|None)."""
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
    # dedupe: open positions + consumed ledger
    if any(p.get("mint") == mint for p in b["positions"].values()) or mint in _consumed_mints():
        return None, "already_held_or_consumed"
    mkt = dex_pair(mint) or {}
    liq = ((mkt.get("liquidity") or {}).get("usd")) or 0
    if liq < 10000:
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
    FILL_LATENCY_SLIP_PCT = 1.0
    px = float(px) * (1 + FILL_LATENCY_SLIP_PCT / 100)
    if sym == "?" or sym == "None":
        sym = ((mkt.get("baseToken") or {}).get("symbol")) or sym
    conf = d.get("confluence") or len(d.get("channels") or [1])
    size = 25.0 if conf >= 2 else 20.0
    size = min(size, b["cash"])
    if size < 10:
        b.setdefault("skipped", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                                            "why": f"insufficient_cash_${b['cash']:.2f}"})
        save_book(b)
        return None, f"insufficient_cash_${b['cash']:.2f}"
    age = (time.time() - float(d.get("ts") or time.time())) / 60
    b["positions"][mint] = {"mint": mint, "symbol": sym, "entry_px": float(px), "size_usd": size,
                           "entry_usd": size, "initial_usd": size, "opened": time.time(),
                           "state": "full", "age_at_buy": max(0, age), "reserved_exit_usd": 0.0,
                           "stages_done": [], "peak_price": float(px), "trough_price": float(px),
                           "entry_liq": liq,
                           "entry_vol_h1": (mkt.get("volume") or {}).get("h1") or 0,
                           "entry_mc": float(mkt.get("marketCap") or 0)}
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
    line = f"🛒 PAPER BUY ${sym} @ ${px} (${size:.0f}, call {max(0,age):.0f}m old) {mint[:10]}"
    return line, None


def main():
    for line in auto_buy():
        print(line)
    b = load_book()
    pos = pm.load_positions()  # SINGLE authoritative monitor snapshot (audit P1 Oct 18)
    out = []
    for mint, p in list(b["positions"].items()):
        sym = p.get("symbol") or mint[:8]  # display symbol (book key is MINT — P0.1)
        mkt_ts = time.time()               # audit P1.2 (Oct 18): market observation ts — captured
                                            # the moment the snapshot is fetched, not at decision end
        mkt = dex_pair(mint) or {}
        px = mkt.get("priceUsd")
        if not px:
            out.append(f"? {sym}: no market"); continue
        px = float(px)
        mc = mkt.get("marketCap") or 0
        mult = px / p["entry_px"]
        entry_usd = p["size_usd"]
        cur_usd = entry_usd * mult
        state = p.get("state", "full")

        # ---- decision-contract execution (doc-1): monitor's analyze() drives exits ----
        # MFE/MAE high-water marks (Build 7: attribution needs peak/trough)
        if px > float(p.get("peak_price") or 0): p["peak_price"] = px
        if p.get("trough_price") is None or px < float(p["trough_price"]): p["trough_price"] = px
        # Build 5: tier budgeting — BACKGROUND moonbags skip deep analyze most ticks
        try:
            from bot.orchestration.monitor_tiers import classify
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
        skip_deep = (tier == "BACKGROUND" and tick_no % 10 != 0)
        p["_tick_no"] = (tick_no + 1) % 10
        decision_action = None
        if not skip_deep:
            try:
                mp = pos.get(mint) or {"entry_px": p["entry_px"], "size": entry_usd,
                                           "ts": p.get("opened", time.time()), "paper": True,
                                           "symbol": sym, "entry_liq": p.get("entry_liq"),
                                           "entry_vol_h1": p.get("entry_vol_h1")}
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
                try:
                    from bot.position_manager.scaleout import execute_fraction
                    res = execute_fraction(b, mint, frac, mult, reason=decision_action,
                                           how="decision_frac", liq=((mkt.get("liquidity") or {}).get("usd") or 0))
                except Exception:
                    res = None
                if res:
                    # P1.1: sync remaining size into the monitor ledger
                    if not res.get("closed"):
                        pos[mint]["size"] = res.get("remaining", 0.0)
                    save_book(b); pm.save_positions(pos)
                    continue
                stage = None  # fall through to tier engine if fraction path failed
            if stage:
                res = sell_via_stage(b, pos, sym, mint, stage, mult, px,
                                     reason=decision_action, how="decision", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0))
                if res:
                    # P1.1: sync remaining size into the monitor ledger after any partial/full
                    save_book(b); pm.save_positions(pos)
                    continue
            # fallback to legacy inline path if stage engine unavailable
            if stage is None or "unavailable" in (out[-1] if out else ""):
                frac = frac or (0.5 if decision_action == "CLIP_50" else 1.0)
                sold = entry_usd * frac * mult
                pnl = sold - entry_usd * frac
                b["realized"] = round(b.get("realized", 0.0) + pnl, 2)
                b["cash"] = round(b.get("cash", 0.0) + sold, 2)
                b.setdefault("trades", []).append({"ts": time.time(), "symbol": sym, "mint": mint,
                    "entry_px": p["entry_px"], "exit_px": px, "mult": mult,
                    "pnl": round(pnl, 2), "how": f"decision_{decision_action.lower()}",
                    "closed": frac >= 0.99})
                if frac >= 1.0:
                    b["positions"].pop(sym, None)
                    pos.pop(mint, None)
                else:
                    p["size_usd"] = entry_usd * (1 - frac); p["size"] = p["size_usd"]; p["state"] = "half"
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
        # FIX #5: stale-hold rule — quick flips only. Any position open >3h closes at market.
        age_h = (time.time() - p.get("opened", time.time())) / 3600
        if age_h > 3:
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="STALE", how="stale", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        if state == "full" and mult <= 0.50:
            # FIX (user #1): price-based hard stop in the fallback engine too —
            # liquidity-independent floor at 0.50x, catches tokens that dump
            # before the deep decision engine gets a clean read.
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="HARD_STOP", how="stop", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        if state == "full" and mult >= tp1:
            if sell_via_stage(b, pos, sym, mint, 1, mult, px, reason="CLIP1", how="clip1", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        elif state == "half" and (mult >= tp2 or mult <= 0.50):
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="TP", how="tp", out=out, market_ts=mkt_ts, liq=((mkt.get("liquidity") or {}).get("usd") or 0)):
                save_book(b); pm.save_positions(pos)
                continue
        plan_s = f"sell half at +{(tp1-1)*100:.0f}%, sell all at +{(tp2-1)*100:.0f}%, cut losses at {stop*100:.0f}%"
        out.append(f"• ${sym}: worth ${cur_usd:.2f} ({'+' if mult>=1 else ''}{(mult-1)*100:.0f}% since buy) — {action}. Plan: {plan_s}")
    save_book(b); pm.save_positions(pos)
    # FIX #6: recompute equity from book (staged executor already updated cash/realized)
    openval = 0.0
    for sym, p in b["positions"].items():
        mkt = dex_pair(p["mint"]) or {}
        px = mkt.get("priceUsd")
        if px: openval += p["size_usd"] * float(px) / p["entry_px"]
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
    out.append(f"💼 Paper account: ${eq:.2f} (started ${b['start']:.0f}, {'+' if gain>=0 else ''}{gpct:.0f}% — {mood}). Waiting cash: ${cash:.2f}. Coins held: {len(b['positions'])}. Still need ${need:.2f} to reach the $500 goal.")
    blob = "\n".join(out)
    # 10-min heartbeat: DM the book summary even with no action (user-approved Oct 19)
    HB = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tick_heartbeat")
    now = time.time()
    due = True
    try:
        due = (now - float(open(HB).read().strip() or 0)) >= 600
    except Exception:
        due = True
    acted = any(("BOUGHT" in l or "SOLD" in l or "EXIT" in l or "skipped" in l or "HOLD" in l or "clip" in l.lower()) for l in out)
    if due or acted or len(b["positions"]) > 0:
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

#!/usr/bin/env python3
"""Paper-trade tick: mark positions to market, apply MC-adaptive exit engine, execute paper clips/exits."""
import sys, json, time, os
sys.path.insert(0, "/home/agentuser/memecoin-quant")
from desk_common import dex_pair
import position_monitor as pm

BOOK = "/home/agentuser/memecoin-quant/paper_book.json"

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

def sell_via_stage(b, pos, sym, mint, stage, mult, px, reason, how="stage", out=None):
    """UNIFIED paper sell: risk gate → reserve → stage-curve execute → attribution.
    Returns True if handled. stage1 = target 50% of original, stage3 = full exit.
    Builds 1/3/4/7: reservation lock, self-healing target curve, risk gate, attribution."""
    out = out if out is not None else []
    try:
        from bot.decision_engine.risk_gate import gate
        from bot.position_manager.scaleout import reserve, execute_stage, release, sell_delta
    except Exception as e:
        out.append(f"⚠ stage engine unavailable {sym}: {str(e)[:50]} — legacy path")
        return False
    p = b["positions"].get(sym)
    if not p:
        return False
    frac = 0.5 if stage == 1 else 1.0
    allowed, gnotes = gate({"action": "SCALE_OUT" if stage == 1 else "FULL_EXIT",
                            "sell_fraction": frac}, b, sym, quote_ts=None)
    if allowed in ("HOLD", "WAIT_RECONCILE"):
        out.append(f"GATE {sym}: {reason} blocked — {'; '.join(gnotes)}")
        return False
    # capture for attribution before position pops
    peak = float(p.get("peak_price") or 0); trough = float(p.get("trough_price") or 0)
    initial = float(p.get("initial_usd") or p["entry_usd"])
    decision_px = float(p.get("last_decision_px") or px)
    entry_px = float(p["entry_px"])
    # reserve exactly the sellable slice the target curve needs
    want = sell_delta(b, sym, stage)
    if want <= 0.0:
        out.append(f"GATE {sym}: nothing to sell for stage{stage} (already on/under target)")
        return False
    if not reserve(b, sym, want, intent_key=f"{sym}:{how}{stage}"):
        out.append(f"GATE {sym}: reservation failed (in-flight)")
        return False
    res = execute_stage(b, sym, stage, mult, reason=reason, how=how)
    if not res:
        release(b, sym, want)
        out.append(f"GATE {sym}: stage engine no-op")
        return False
    if b.get("trades"):
        b["trades"][-1]["how"] = f"{how}_{reason.lower()}" if how == "decision" else f"{how}"
    save_book(b)
    if res["closed"]:
        pos.pop(sym, None)  # pm positions cleanup (symbol key, legacy)
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
        out.append(f"CLIP {sym} @ {mult:.2f}x -> sold ${res['sold_usd']:.2f} (pnl {res['pnl']:+.2f}) → remaining ${res['remaining']:.2f} (target-curve) | gate ok")
    return True

def tier_exits(mc):
    # SINGLE SOURCE OF TRUTH: position_monitor.EXIT_TIERS via tier_for().
    # (clip_pct, tp1, tp2, stop) — stop is a positive fraction of entry.
    from position_monitor import tier_for
    mc_floor, tp1, tp2, stop = tier_for(mc)
    return (0.50, tp1, tp2, -stop)

def auto_buy():
    """Buy NEW calls (<5m since channel post) with available cash."""
    b = load_book()
    if not b.get("autobuy", True):
        return []
    bought = []
    import json as _j
    # audit P2 (Oct 18): dedupe by MINT only, never by ticker symbol — two
    # distinct contracts sharing a symbol (buy-all mandate) must both be bought.
    seen_mints = {p.get("mint") for p in b["positions"].values()}
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
        if any(p["mint"] == mint for p in b["positions"].values()) or mint in seen_mints:
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
        b["positions"][sym] = {"mint": mint, "entry_px": float(px), "size_usd": size,
                               "entry_usd": size, "initial_usd": size, "opened": time.time(),
                               "state": "full", "age_at_buy": age, "reserved_exit_usd": 0.0,
                               "stages_done": [], "peak_price": float(px), "trough_price": float(px),
                               # audit P1 (Oct 18): persist IMMUTABLE entry baselines so the
                               # monitor's liq-collapse / volume-drying rules compare entry->now
                               "entry_liq": ((mkt.get("liquidity") or {}).get("usd")) or 0,
                               "entry_vol_h1": (mkt.get("volume") or {}).get("h1") or 0,
                               "entry_mc": float(mkt.get("marketCap") or 0)}
        b["cash"] = round(b["cash"] - size, 2)
        seen_mints.add(mint)
        bought.append(f"BUY ${sym} @ ${px} (${size:.0f}, call {age:.0f}m old) {mint[:10]}")
        import position_monitor as _pm
        pos = _pm.load_positions()
        pos[mint] = {"symbol": sym, "entry_px": float(px), "size": size, "ts": time.time(), "paper": True}
        _pm.save_positions(pos)
        save_book(b)
    return bought

def main():
    for line in auto_buy():
        print(line)
    b = load_book()
    pos = pm.load_positions()  # SINGLE authoritative monitor snapshot (audit P1 Oct 18)
    out = []
    tick_no = 0
    for sym, p in list(b["positions"].items()):
        mint = p["mint"]
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
            tier = classify(p)
            p["tier"] = tier
        except Exception:
            tier = "NORMAL"
        skip_deep = (tier == "BACKGROUND" and tick_no % 10 != 0)
        tick_no += 1
        decision_action = None
        if not skip_deep:
            try:
                mp = pos.get(mint) or {"entry_px": p["entry_px"], "size": entry_usd,
                                           "ts": p.get("opened", time.time()), "paper": True,
                                           "symbol": sym, "entry_liq": p.get("entry_liq"),
                                           "entry_vol_h1": p.get("entry_vol_h1")}
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
                stage, frac = 1, 0.5 if cfrac < 0.85 else 1.0
                if frac >= 0.99:
                    stage = 3
            else:
                stage, frac = None, 0.0
            if stage:
                res = sell_via_stage(b, pos, sym, mint, stage, mult, px,
                                     reason=decision_action, how="decision", out=out)
                if res:
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
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="STALE", how="stale", out=out):
                save_book(b); pm.save_positions(pos)
                continue
        if state == "full" and mult <= 1 + stop:
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="STOP", how="stop", out=out):
                save_book(b); pm.save_positions(pos)
                continue
        if state == "full" and mult >= tp1:
            if sell_via_stage(b, pos, sym, mint, 1, mult, px, reason="CLIP1", how="clip1", out=out):
                save_book(b); pm.save_positions(pos)
                continue
        elif state == "half" and (mult >= tp2 or mult <= 1 + stop * 0.6):
            if sell_via_stage(b, pos, sym, mint, 3, mult, px, reason="TP", how="tp", out=out):
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
    print("\n".join(out))

if __name__ == "__main__":
    main()

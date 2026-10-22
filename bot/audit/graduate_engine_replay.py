#!/usr/bin/env python3
"""graduate_engine_replay.py — Oct 10 graduates through the REAL memequant
paper engine (scaleout executor + live EXIT_TIERS + paper_tick tier logic).

Entry mirrors paper_tick.entry_call/auto_buy:
  - first slot end >= grad+15s (user rule 1 timing)
  - liq gate $10K (post-pump graduates are NOT curve-pre-grad -> gate applies)
  - 1% fill slip, $20 size
Exit mirrors paper_tick fallback tier engine executed by the REAL
scaleout.execute_stage (3% fee/side, stage1=sell-half, stage3=sell-all,
MIN_TRANCHE $2.5, UNSELLABLE when tape liq < $1000 twice >=60s apart):
  - full: mult <= 0.75 -> TIER_STOP (stage 3); mult <= 0.50 -> HARD_STOP;
          mult >= tp1 -> CLIP1 (stage 1)
  - half: mult >= tp2 or mult <= 0.50 -> TP (stage 3)
  - open at tape end (+30min): mark at last price (the engine holds past
    horizon — no +180s exit exists in paper_tick)
All book I/O monkeypatched to a temp dict; scaleout.time.time is remapped to
slot time so the 60s low-liq independence gate replays faithfully; the
RECLASSIFIED_UP dex check is stubbed (no network) so dust books UNSELLABLE.
The live paper_book.json is NEVER touched.
"""
import json, os, sys, time, csv, statistics, collections
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

DESK = os.path.expanduser("~/memecoin-quant")
GRAD_CSV = "/home/agentuser/.hermes/opencraft-attachments/8e486aaf-4d3c-468b-b117-3fd8ae46b7d8/content"
CACHE = os.path.join(DESK, "_grad_cache")
HOURS = ["2026/10/10/14", "2026/10/10/15", "2026/10/10/16"]
SOL_USD = 150.0
SIZE_USD = 20.0
BUY_S = 15
HORIZON_S = 1800
ENTRY_LIQ_MIN = 10_000.0

from bot.position_manager import scaleout as so
from position_monitor import EXIT_TIERS, tier_for


def calendar_time(s):
    import calendar
    return calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M:%S"))


def load_grads():
    rows = list(csv.DictReader(open(GRAD_CSV)))
    out = []
    for r in rows:
        try:
            t = calendar_time(r["grad_time_utc"])
        except Exception:
            continue
        out.append({"mint": r["mint"], "pool": r["pool"], "grad_ts": t,
                    "symbol": r.get("symbol") or r["mint"][:6],
                    "touched_minus80": int(r["touched_minus80_10m"] or 0),
                    "touched_2x": int(r["touched_2x_10m"] or 0),
                    "cap_sol_at_grad": float(r["cap_sol_at_grad"] or 0)})
    return out


def load_pool_events():
    pools = {g["pool"] for g in GRADS}
    per_pool = collections.defaultdict(dict)
    for h in HOURS:
        filt = os.path.join(CACHE, h.replace("/", "_") + ".filtered.jsonl")
        if not os.path.exists(filt):
            continue
        for line in open(filt):
            try:
                e = json.loads(line)
            except Exception:
                continue
            key = e.get("poolId")
            if key not in pools:
                continue
            if e.get("action") not in ("buy", "sell"):
                continue
            ts = e.get("timestamp")
            price = e.get("price")
            b = e.get("tokensInPool")
            q = e.get("quoteInPool")
            blk = e.get("block")
            if not ts or not price or not b or not q or blk is None:
                continue
            price = float(price)
            if price <= 0:
                continue
            cur = per_pool[key].get(blk)
            if cur is None or ts >= cur[0]:
                per_pool[key][blk] = (ts, price, float(b), float(q))
    return {k: sorted(v.values()) for k, v in per_pool.items()}


def run_engine(evs, grad_ts, cap_sol_grad, use_gate=True, plus180=False, ladder=True):
    """One graduate through the REAL executor. Returns (trade, skip).
    trade = {exit_reason, pnl, ret, entry_px} or None.
    plus180: force-sell at first slot end >= grad+180s (user rule 1)."""
    g_ms = grad_ts * 1000
    entry_sig = g_ms + BUY_S * 1000
    if not evs or evs[-1][0] < entry_sig:
        return None, "NEVER_ENTERED"
    idx = next((i for i, e in enumerate(evs) if e[0] >= entry_sig), None)
    if idx is None:
        return None, "NEVER_ENTERED"
    ts_e, px_e, B_e, Q_e = evs[idx]

    entry_liq_usd = 2 * Q_e * SOL_USD
    if use_gate and entry_liq_usd < ENTRY_LIQ_MIN:
        return None, f"LIQ_GATE_${entry_liq_usd:.0f}"
    entry_px = px_e * 1.01  # Doc2-8 1% fill slip
    mc = cap_sol_grad * SOL_USD
    tier = tier_for(mc)
    sym = "G" + str(grad_ts)[-5:]
    book = {"cash": SIZE_USD, "realized": 0.0, "positions": {}, "start": time.time(),
            "trades": []}
    book["positions"][sym] = {"mint": "grad", "symbol": sym,
                              "entry_px": float(entry_px), "size_usd": SIZE_USD,
                              "entry_usd": SIZE_USD, "initial_usd": SIZE_USD,
                              "opened": time.time(), "state": "full",
                              "age_at_buy": 0, "reserved_exit_usd": 0.0,
                              "stages_done": [], "peak_price": float(entry_px),
                              "trough_price": float(entry_px), "entry_liq": entry_liq_usd,
                              "entry_vol_h1": 0, "entry_mc": mc,
                              "reservations": [], "_low_liq_streak": 0,
                              "_low_liq_bump_ts": 0.0}
    book["cash"] = 0.0
    _orig_load, _orig_save = so.load_book, so.save_book
    so.load_book = lambda: book
    so.save_book = lambda b: None
    # stub network in the reclass/liq-unknown paths so the replay is offline
    _orig_dex = None
    _orig_rug = None
    try:
        import desk_common
        _orig_dex = desk_common.dex_pair
        desk_common.dex_pair = lambda *a, **k: {}
        _orig_rug = desk_common.rugcheck
        desk_common.rugcheck = lambda *a, **k: {}
    except Exception:
        pass
    # remap scaleout's clock to slot time for the 60s liq-independence gate
    _orig_clock = so.time.time
    _cur_s = [0.0]
    so.time.time = lambda: _cur_s[0]
    try:
        stop_mult = 1 - tier[3]
        tp1, tp2 = tier[1], tier[2]
        sell_sig = g_ms + 180 * 1000
        horizon = g_ms + HORIZON_S * 1000
        pending = None
        last_px_bf = None
        last_q_bf = None
        last_reason = None
        idx += 1
        while idx < len(evs):
            ts, px, B, Q = evs[idx]
            _cur_s[0] = ts / 1000.0
            if ts >= horizon:
                break
            if pending is not None:
                # fill pending at THIS slot end (rule 1: observed N -> fills N+1)
                mult = px / entry_px
                liq = 2 * Q * SOL_USD
                stg = 3 if pending in ("STOP", "HARD", "TP2", "SELL180") else 1
                traderec = so.execute_stage(book, sym, stg, mult, reason=pending, how="stage",
                                            liq=liq, fee_pct=3.0)
                last_reason = pending + (traderec.get("reason", "") or "") if traderec else pending
                p = book["positions"].get(sym)
                if p is None or float(p["entry_usd"]) < 0.01:
                    break  # closed (executor pops on close)
                pending = None
                continue
            last_px_bf = px
            last_q_bf = Q
            mult = px / entry_px
            state = book["positions"].get(sym, {}).get("state", "full")
            # observe at slot end (rule 4) — mirror paper_tick tier engine
            if ladder:
                if state == "full" and mult <= stop_mult:
                    pending = "STOP"
                elif state == "full" and mult <= 0.50:
                    pending = "HARD"
                elif state == "full" and mult >= tp1:
                    pending = "CLIP1"
                elif state == "half" and (mult >= tp2 or mult <= 0.50):
                    pending = "TP2"
            if plus180 and pending is None and ts >= sell_sig:
                pending = "SELL180"
            idx += 1
        # position still open at horizon -> mark at last price (engine holds)
        p = book["positions"].get(sym)
        realized = float(book.get("realized") or 0.0)
        if p is not None and float(p["entry_usd"]) >= 0.01 and last_px_bf is not None:
            rem = float(p["entry_usd"])
            mult_last = last_px_bf / entry_px
            mark_proceeds = rem * mult_last * (1 - 3.0 / 100)
            pnl = realized + (mark_proceeds - rem)
            reason = "OPEN_30M_MARK"
        else:
            pnl = realized
            reason = last_reason or "CLOSED"
        if p is not None and float(p["entry_usd"]) >= 0.01 and last_px_bf is None:
            return None, "TAPE_ENDED_OPEN"
    finally:
        so.load_book, so.save_book = _orig_load, _orig_save
        if _orig_dex is not None:
            try:
                import desk_common
                desk_common.dex_pair = _orig_dex
                desk_common.rugcheck = _orig_rug
            except Exception:
                pass
    return {"exit_reason": reason, "pnl": pnl, "ret": pnl / SIZE_USD,
            "entry_px": entry_px}, None


def main():
    global GRADS
    GRADS = load_grads()
    events = load_pool_events()
    print(f"graduates {len(GRADS)}, pool tapes {sum(1 for g in GRADS if g['pool'] in events)}")

    configs = [
        ("ENGINE (real executor, $10K gate, 3% fee/side, tier ladder)",
         dict(use_gate=True, plus180=False, ladder=True)),
        ("ENGINE minus gate (no $10K liq filter)",
         dict(use_gate=False, plus180=False, ladder=True)),
        ("ENGINE +180s force-sell, gate ON, WITH ladder",
         dict(use_gate=True, plus180=True, ladder=True)),
        ("ENGINE +180s ONLY (no ladder/stops), gate ON — pure time exit",
         dict(use_gate=True, plus180=True, ladder=False)),
    ]
    for label, cfg in configs:
        trades, skips = [], collections.Counter()
        for g in GRADS:
            tr, sk = run_engine(events.get(g["pool"], []), g["grad_ts"],
                                g["cap_sol_at_grad"], **cfg)
            if tr:
                trades.append((g["symbol"], tr["ret"], tr["pnl"], tr["exit_reason"]))
            else:
                skips[sk or "SKIP"] += 1
        n = len(trades)
        print(f"\n== {label} ==")
        print(f"traded {n}/{len(GRADS)}  skips: {dict(skips)}")
        if n:
            rets = [t[1] for t in trades]
            wins = sum(1 for r in rets if r > 0)
            tot = sum(t[2] for t in trades)
            print(f"win rate {100*wins/n:.1f}%  median {100*statistics.median(rets):+.2f}%  mean {100*statistics.mean(rets):+.2f}%")
            print(f"total {tot:+.2f} USD ({100*tot/(n*SIZE_USD):+.1f}% of deployed ${n*SIZE_USD:.0f})")
            for w in sorted(trades, key=lambda t: t[1])[:3]:
                print(f"  worst {w[0]}: {100*w[1]:+.1f}% ({w[2]:+.2f} USD, {w[3]})")
            print("  exits:", dict(collections.Counter(t[3] for t in trades)))


if __name__ == "__main__":
    main()
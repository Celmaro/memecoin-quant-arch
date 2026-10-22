#!/usr/bin/env python3
"""prototype_liveness_gate.py — FIRST-3-MIN LIVENESS GATE prototype (Oct 22).

User-approved experiment: skip coins that have not shown a +20% peak within
N minutes of the call. Losers never move (median peak +12%); winners show
+30-70% almost immediately. The gate buys at the +20% level instead of at the
call price, and only if the coin showed life.

Design:
  gate(peak_mult_observed) -> True if the coin reached >= 1.20x its reference
  price within the window. In production this reads the pumpdata feed's
  pxs_tail for the mint (samples timestamped ms). For the BACKTEST we do not
  have per-minute paths for the exact trade windows (market_events = close
  events only; shrine tapes cover other hours), so we use the ALIGNED
  attribution peak_px/entry_px as a proxy for "peaked >= +20% during the
  hold". Bias (stated honestly): a coin peaking AFTER minute 3 would falsely
  pass; the proxy is optimistic about the gate's selectivity.

  Simulated fill: the gate passes at ~1.20x, so the entry is modeled at
  entry_px * 1.20 * (1 + 1% slip). Exit keeps the SAME exit mult the desk
  actually booked. P&L per pass = (exit_mult / 1.2 - 1) * size - fees.
  Coins that fail the gate: not bought -> 0 P&L (dead-coins avoided; but
  winners that never showed +20% are missed too -> opportunity cost shown).

NO live writes: everything runs on copies of the book/DB reads.
"""
import json, sqlite3, sys, os

MIN_PEAK_MULT = 1.20      # +20% liveness threshold
WINDOW_S = 180            # first 3 minutes after call
SLIP = 0.01
FEE = 0.03
SIZE = 20.0

def load_trades():
    books = []
    for path in ("paper_book.json", "paper_book_ARCHIVE_20261021_233532.json"):
        try:
            b = json.load(open(path))
        except Exception:
            continue
        for t in b.get("trades", []):
            m = t.get("mult") or 0; p = t.get("pnl") or 0
            if isinstance(m, (int, float)) and 0 < m < 50 and isinstance(p, (int, float)) and -30 < p < 400:
                books.append(t)
    return books

def load_calls():
    calls = {}
    for line in open("calls.jsonl"):
        try:
            d = json.loads(line)
            mint = d.get("mint") or d.get("ca")
            if mint:
                calls.setdefault(mint, {}).update({
                    "channel": d.get("channel") or calls.get(mint, {}).get("channel"),
                    "call_mc": d.get("mc") or d.get("mc_usd"),
                    "call_ts": d.get("ts")})
        except Exception:
            pass
    return calls

def load_attribution():
    db = sqlite3.connect("aegis.db")
    rows = db.execute(
        "SELECT mint, closed_at, entry_px, peak_px, realized_pnl FROM position_attribution").fetchall()
    att = {}
    for r in rows:
        att.setdefault(r[0] or "", []).append(
            {"closed_at": r[1], "entry_px": r[2], "peak_px": r[3], "rp": r[4]})
    return att

def aligned_peak(trade, att):
    """Best aligned attribution row within +-15min, sane realized pnl; return
    (peak_mult, entry_px, exit_mult, closed_at). peak_mult = peak/entry."""
    a = att.get(trade.get("mint") or "")
    if not a:
        return None
    cand = [x for x in a if x["closed_at"] and abs(x["closed_at"] - (trade.get("ts") or 0)) < 900
            and (x["rp"] is None or abs(x["rp"]) < 100)]
    if not cand:
        return None
    best = min(cand, key=lambda x: abs(x["closed_at"] - (trade.get("ts") or 0)))
    ep = best["entry_px"]; pp = best["peak_px"]
    if not ep or not pp or float(ep) <= 0 or float(pp) <= 0:
        return None
    peak_mult = float(pp) / float(ep)
    exit_mult = float(trade.get("mult") or 0)
    if peak_mult >= 50 or exit_mult >= 50:   # corrupt-era glitch guard
        return None
    return peak_mult, ep, exit_mult, best["closed_at"]

def main():
    trades = load_trades()
    calls = load_calls()
    att = load_attribution()
    print(f"loaded {len(trades)} clean trades | {len(calls)} calls | attribution {sum(len(v) for v in att.values())} rows")

    rows = []
    for t in trades:
        r = aligned_peak(t, att)
        if not r:
            continue
        peak_mult, ep, xm, closed_at = r
        rows.append({
            "pnl": t.get("pnl") or 0, "mult": xm, "peak_mult": peak_mult,
            "reason": t.get("reason", ""), "ts": t.get("ts"),
            "channel": (calls.get(t.get("mint")) or {}).get("channel"),
            "call_mc": (calls.get(t.get("mint")) or {}).get("call_mc"),
        })
    print(f"aligned with peak data: {len(rows)} / {len(trades)}")

    passed = [r for r in rows if r["peak_mult"] >= MIN_PEAK_MULT]
    failed = [r for r in rows if r["peak_mult"] < MIN_PEAK_MULT]

    # ---- actual book P&L vs gate-sim P&L ----
    actual_total = sum(r["pnl"] for r in rows)
    sim_total = 0.0
    stats = {"bought": 0, "sim_wins": 0, "sim_losses": 0,
             "missed_actual_wins": 0, "missed_win_pnl": 0.0,
             "avoided_losses": 0, "avoided_loss_pnl": 0.0}
    per_channel = {}
    for r in rows:
        ch = r["channel"] or "?"
        per_channel.setdefault(ch, {"actual": 0.0, "sim": 0.0, "n": 0, "pass": 0})
        per_channel[ch]["actual"] += r["pnl"]
        per_channel[ch]["n"] += 1
        if r["peak_mult"] >= MIN_PEAK_MULT:
            sim_entry = 1.20 * (1 + SLIP)
            sim_pnl = SIZE * (r["mult"] / sim_entry - 1) - SIZE * FEE * 2
            sim_total += sim_pnl
            stats["bought"] += 1
            per_channel[ch]["sim"] += sim_pnl
            per_channel[ch]["pass"] += 1
            if sim_pnl > 0: stats["sim_wins"] += 1
            else: stats["sim_losses"] += 1
        else:
            if r["pnl"] > 0:
                stats["missed_actual_wins"] += 1
                stats["missed_win_pnl"] += r["pnl"]
            else:
                stats["avoided_losses"] += 1
                stats["avoided_loss_pnl"] += r["pnl"]

    n = len(rows)
    print(f"\n=== ACTUAL DESK (79 trades, aligned {n}) ===")
    print(f"total PnL: ${actual_total:+.2f} | avg ${actual_total/n:+.2f}/trade | "
          f"wins {sum(1 for r in rows if r['pnl']>0)}/{n}")

    print(f"\n=== GATE SIMULATION (buy only if peak >= {MIN_PEAK_MULT:.2f} within window, enter at 1.20x+slip) ===")
    print(f"would buy: {stats['bought']}/{n} ({100*stats['bought']/n:.0f}%)")
    print(f"sim total PnL: ${sim_total:+.2f} | avg ${sim_total/stats['bought']:+.2f}/bought" if stats['bought'] else "no buys")
    print(f"sim WR: {stats['sim_wins']}/{stats['bought']} ({100*stats['sim_wins']/max(stats['bought'],1):.0f}%)")
    print(f"saved by skipping dead coins: ${stats['avoided_loss_pnl']:+.2f} over {stats['avoided_losses']} skipped-losers")
    print(f"opportunity cost (missed winners that never showed +20%): ${stats['missed_win_pnl']:+.2f} over {stats['missed_actual_wins']}")

    print(f"\n=== CHANNEL: actual vs gate-sim ===")
    for ch, s in sorted(per_channel.items(), key=lambda x: -x[1]["sim"]):
        if s["n"] >= 2:
            print(f"  {ch}: n={s['n']} actual={s['actual']:+.2f} sim={s['sim']:+.2f} pass={s['pass']}")

    # ---- gate selectivity by peak bucket ----
    print(f"\n=== PEAK DISTRIBUTION (pass = peak >= 1.20) ===")
    buckets = [(1.0, "never green"), (1.05, "1.00-1.05x"), (1.20, "1.05-1.20x"), (1.5, "1.20-1.50x"), (float('inf'), "1.50x+")]
    prev = 0.0
    for hi, label in buckets:
        grp = [r for r in rows if prev <= r["peak_mult"] < hi]
        if grp:
            w = sum(1 for r in grp if r["pnl"] > 0)
            pnl = sum(r["pnl"] for r in grp)
            print(f"  peak [{label:>10}]: n={len(grp):2d} wr={100*w/len(grp):3.0f}% actual_pnl={pnl:+7.2f} | {'PASS' if hi >= 1.20 or prev >= 1.20 else ''}")
        prev = hi

if __name__ == "__main__":
    main()
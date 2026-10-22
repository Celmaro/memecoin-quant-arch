#!/usr/bin/env python3
"""aegis_learn.py — AEGIS adaptive weighting (v1, Oct 19).

AEGIS currently uses STATIC weights. This trainer closes the loop:

1. Re-derive flags from stored scan_log.checks_json (deterministic mapping —
   mirrors the real check logic for the subset of checks persisted in history).
2. Join outcomes: position_attribution.realized_pnl (paper) + followups
   SURVIVED/DRAINED (fixed-rule shadow) — win = pnl>0 or SURVIVED.
3. Compute per-flag lift: P(win | flag) vs base P(win), min-sample guard.
4. Write bounded overrides to aegis_weight_overrides.json
   {flag: delta} with delta clipped to [-6, +6] so no single check can
   dominate and the live overlay bounds (+33/-42) still hold.

Run:  python3 aegis_learn.py [--min-n 8] [--apply]
Safe: read-only except the overrides file; never touches aegis.db writes.
"""

import json
import os
import sqlite3
import sys
from collections import defaultdict

DESK = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DESK, "aegis.db")
OVERRIDES = os.path.join(DESK, "aegis_weight_overrides.json")

MIN_N = 8          # minimum flag sample before its lift is trusted
CLIP = 6           # max single-flag weight delta
BASE_PENALTY = 0   # base already applied in score_coin; only deltas stored here


def derive_flags(checks):
    """Deterministic flags from stored checks_json — MUST match live score_coin
    flag vocabulary EXACTLY (verified against aegis.py static flags), else the
    learned overrides never match. Live names confirmed Oct 19:
    RUGGED_FLAG, insider_graph_detected, transfer_fee_present, lp_unlocked,
    lp_partially_locked, top10_concentration, mint_authority_present,
    freeze_authority_present, goplus_mintable, non_transferable, transfer_hook,
    trusted_token, bundle_or_sniper_risk, copycat, mint_risk, freeze_risk,
    ws_dumping, ws_sell_pressure, ws_buy_pressure, ws_hot_buys, ws_quiet_tape,
    blix_dev_flat, fresh_wallet_power_none, gmgn_wash_flag, known_rugger_in_first_buyers,
    deployer_*; dynamic names (thin_pool_liq_mc_X%, micro_cvd_*) cannot be
    reproduced from checks_json — those are learned going forward.
    """
    flags = []
    auth = checks.get("authorities") or {}
    if auth.get("mint_authority"):
        flags.append("mint_authority_present")
    if auth.get("freeze_authority"):
        flags.append("freeze_authority_present")

    risks = checks.get("risks") or {}
    if risks.get("rugged"):
        flags.append("RUGGED_FLAG")
    if risks.get("graph_insiders"):
        flags.append("insider_graph_detected")
    tf = risks.get("transfer_fee") or {}
    if isinstance(tf, dict) and float(tf.get("pct") or 0) > 0:
        flags.append(f"transfer_fee_present_{float(tf['pct']):.1f}%")
    lp = risks.get("lp_locked_pct")
    if isinstance(lp, (int, float)):
        if lp < 50:
            flags.append("lp_partially_locked")
        # lp_unlocked comes from risks list entries in live code
        for r in (risks.get("risks") or []):
            nm = str((r or {}).get("name") or "").lower()
            lvl = str((r or {}).get("level") or "").lower()
            if "liquidity" in nm and "lock" in nm and "unlocked" in lvl:
                flags.append("lp_unlocked")
    t10 = risks.get("top10_pct")
    if isinstance(t10, (int, float)) and t10 > 70:
        flags.append("top10_concentration")
    for r in (risks.get("risks") or []):
        nm = str((r or {}).get("name") or "").lower()
        if "bundle" in nm or "snip" in nm or "insider" in nm:
            flags.append("bundle_or_sniper_risk")
        if "copy" in nm or "duplicate" in nm:
            flags.append("copycat")
        if "mint" in nm:
            flags.append("mint_risk")
        if "freeze" in nm:
            flags.append("freeze_risk")

    g = checks.get("goplus") or {}
    if g.get("mintable"):
        flags.append("goplus_mintable")
    if g.get("transfer_hook"):
        flags.append("transfer_hook")
    if g.get("non_transferable"):
        flags.append("non_transferable")
    if g.get("trusted_token"):
        flags.append("trusted_token")

    # market side — thin pool ratio mirrors live check #9 (liq/mc < 20%)
    mkt = checks.get("market") or {}
    liq = mkt.get("liq") or 0
    mc = mkt.get("mc") or 0
    if isinstance(liq, (int, float)) and isinstance(mc, (int, float)) and mc > 0 \
            and liq / mc < 0.20:
        flags.append(f"thin_pool_liq_mc_{liq/mc*100:.1f}%")
    return flags


def load_outcomes():
    """Map mint -> outcome label from paper attribution + shadow followups."""
    db = sqlite3.connect(DB)
    cur = db.cursor()
    out = {}
    # paper outcomes: win = realized_pnl > 0
    for mint, pnl in cur.execute(
            "SELECT mint, realized_pnl FROM position_attribution"):
        if mint:
            out.setdefault(mint, []).append(1 if (pnl or 0) > 0 else 0)
    # shadow outcomes: SURVIVED = win, DRAINED = loss
    for mint, oc in cur.execute(
            "SELECT mint, outcome FROM followups WHERE outcome IS NOT NULL"):
        if mint and oc in ("SURVIVED", "DRAINED"):
            out.setdefault(mint, []).append(1 if oc == "SURVIVED" else 0)
    db.close()
    # multiple labels per mint: majority vote
    return {m: (1 if sum(v) >= (len(v) + 1) // 2 else 0)
            for m, v in out.items()}


def load_scans():
    """mint -> list of (ts, checks, exact_flags_or_None) for every scan_log row.
    Prefers stored flags_json (exact live vocabulary) over re-derived flags."""
    db = sqlite3.connect(DB)
    cur = db.cursor()
    scans = defaultdict(list)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(scan_log)").fetchall()]
    has_fj = "flags_json" in cols
    for row in cur.execute("SELECT mint, ts, checks_json, flags_json FROM scan_log WHERE checks_json IS NOT NULL"):
        mint, ts, checks, fj = row
        try:
            exact = json.loads(fj) if fj else None
        except Exception:
            exact = None
        try:
            scans[mint].append((ts, json.loads(checks), exact))
        except Exception:
            continue
    db.close()
    return scans


def main():
    min_n = 8
    apply_overrides = "--apply" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--min-n" and i + 1 < len(sys.argv):
            min_n = int(sys.argv[i + 1])

    outcomes = load_outcomes()
    scans = load_scans()
    # use the MOST RECENT scan per mint (closest to entry); prefer stored flags
    per_mint = {m: sorted(v, key=lambda x: x[0])[-1] for m, v in scans.items()}

    joined = [(m, outcomes[m],
               (per_mint[m][2] if per_mint[m][2] is not None else derive_flags(per_mint[m][1])))
              for m in per_mint if m in outcomes]
    base = sum(1 for _, w, _ in joined if w) / max(1, len(joined))
    print(f"joined samples: {len(joined)}  base win rate: {base:.0%}")

    stats = defaultdict(lambda: [0, 0])  # flag -> [n, wins]
    for _, win, flags in joined:
        for f in set(flags):
            stats[f][0] += 1
            if win:
                stats[f][1] += 1

    deltas = {}
    print(f"\n=== per-flag empirical lift (min_n={min_n}) ===")
    for f, (n, w) in sorted(stats.items(), key=lambda kv: kv[1][1] / max(1, kv[1][0])):
        rate = w / max(1, n)
        if n >= min_n:
            lift = rate - base
            # shrink toward 0 for small samples: n=8 -> 8/20 weight
            shrink = n / (n + 12)
            delta = lift * 100 * shrink * 0.5  # half-scale; keep conservative
            delta = max(-CLIP, min(CLIP, round(delta, 1)))
            if abs(delta) >= 1.0:  # only material deltas
                deltas[f] = delta
            print(f"  {f:38} n={n:3} win={rate*100:4.0f}% lift={lift*100:+5.1f}% delta={delta:+5.1f}")

    if apply_overrides:
        with open(OVERRIDES, "w") as f:
            json.dump({"generated_ts": __import__("time").time(),
                       "min_n": min_n, "base_win": base, "deltas": deltas},
                      f, indent=1)
        print(f"\nwrote {OVERRIDES} with {len(deltas)} deltas")
    else:
        print(f"\n(no --apply: {len(deltas)} deltas computed, not written)")
        print("run with --apply to write aegis_weight_overrides.json")


if __name__ == "__main__":
    main()

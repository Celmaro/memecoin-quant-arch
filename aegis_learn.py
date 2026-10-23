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
    """Map mint -> (entry_anchor_ts, label) from paper attribution ONLY.

    4th audit 7.1 (Oct 23): the old trainer majority-voted INCOMPATIBLE labels
    (paper position_attribution + followups SURVIVED/DRAINED shadow rules with a
    different horizon) into one win/loss, and treated ANY positive partial PnL as
    a full win. Now:
      - PRIMARY label = position_attribution.realized_pnl (one position-level
        realized result; partial fills are aggregated by attribution at write time).
      - paper_outcomes: {mint: (closed_at, label)} for the training signal.
      - shadow_outcomes: followups SURVIVED/DRAINED kept SEPARATE and reported as
        a shadow-only delta set — never mixed into the applied weights.
      - win = realized_pnl > 0; losses and UNSELLABLE count as loss (realized<=0).
    """
    db = sqlite3.connect(DB)
    cur = db.cursor()
    paper, shadow = {}, {}
    for mint, pnl, closed_at in cur.execute(
            "SELECT mint, realized_pnl, closed_at FROM position_attribution"):
        if not mint:
            continue
        try:
            label = 1 if (pnl or 0) > 0 else 0
        except Exception:
            continue
        # one entry event per mint in this ledger — last close wins (idempotent)
        paper[mint] = (float(closed_at or 0), label)
    for mint, oc in cur.execute(
            "SELECT mint, outcome FROM followups WHERE outcome IS NOT NULL"):
        if mint and oc in ("SURVIVED", "DRAINED"):
            shadow[mint] = 1 if oc == "SURVIVED" else 0
    db.close()
    return paper, shadow


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

    paper_outcomes, shadow_outcomes = load_outcomes()
    scans = load_scans()
    # 4th audit 7.1: POINT-IN-TIME anchoring. The old code picked the MOST
    # RECENT scan per mint — that scan can be AFTER entry or after meaningful
    # market moves (monitoring rescan), leaking post-entry information into the
    # feature vector. The desk scans a call when it arrives (pre-entry), so the
    # EARLIEST scan per mint is the pre-decision snapshot. If a mint has only
    # post-entry scans, we cannot build a point-in-time vector: skip it.
    per_mint = {}
    for m, v in scans.items():
        v_sorted = sorted(v, key=lambda x: x[0])
        per_mint[m] = v_sorted[0]  # earliest = pre-entry feature snapshot

    joined = [(m, paper_outcomes[m][1],
               (per_mint[m][2] if per_mint[m][2] is not None else derive_flags(per_mint[m][1])),
               paper_outcomes[m][0])
              for m in per_mint if m in paper_outcomes]
    base = sum(1 for _, w, _, _ in joined if w) / max(1, len(joined))
    print(f"joined paper samples: {len(joined)}  base win rate: {base:.0%}")

    # CHRONOLOGICAL HOLDOUT (4th audit 7.1): train on the first 70% by close
    # time, validate sign-agreement on the last 30%. No shuffle — time order is
    # the only honest split for adaptive weights.
    joined.sort(key=lambda r: r[3])
    cut = max(1, int(len(joined) * 0.7))
    train, valid = joined[:cut], joined[cut:]

    def lift_stats(sample):
        st = defaultdict(lambda: [0, 0])
        b = sum(1 for _, w, _, _ in sample if w) / max(1, len(sample))
        for _, win, flags, _ in sample:
            for f in set(flags):
                st[f][0] += 1
                if win:
                    st[f][1] += 1
        return st, b

    stats, _ = lift_stats(train)
    vstats, vbase = lift_stats(valid)
    deltas = {}
    shadow = {}
    print(f"\n=== per-flag empirical lift (paper labels, min_n={min_n}, "
          f"train={len(train)} / valid={len(valid)}) ===")
    for f, (n, w) in sorted(stats.items(), key=lambda kv: kv[1][1] / max(1, kv[1][0])):
        rate = w / max(1, n)
        if n >= min_n:
            lift = rate - base
            shrink = n / (n + 12)
            delta = lift * 100 * shrink * 0.5
            delta = max(-CLIP, min(CLIP, round(delta, 1)))
            if abs(delta) >= 1.0:
                deltas[f] = delta
                # holdout agreement: does the sign hold on unseen time?
                vn, vw = vstats.get(f, [0, 0])
                vrate = vw / max(1, vn)
                agree = "?"
                if vn >= 3:
                    vlift = vrate - vbase
                    agree = "MATCH" if (delta > 0) == (vlift > 0) else "FLIP"
                shadow[f] = {"delta": delta, "train_n": n, "train_win": round(rate, 3),
                             "valid_n": vn, "valid_win": round(vrate, 3), "agree": agree}
                print(f"  {f:38} n={n:3} win={rate*100:4.0f}% lift={lift*100:+5.1f}% "
                      f"delta={delta:+5.1f}  valid_n={vn:3} valid_win={vrate*100:3.0f}% {agree}")

    # shadow labels reported SEPARATELY, never mixed into deltas
    if shadow_outcomes:
        sbase = sum(1 for w in shadow_outcomes.values() if w) / max(1, len(shadow_outcomes))
        print(f"\nshadow followup labels (NOT applied — separate horizon): n={len(shadow_outcomes)} "
              f"base win={sbase:.0%}")

    if apply_overrides:
        with open(OVERRIDES, "w") as f:
            json.dump({"generated_ts": __import__("time").time(),
                       "min_n": min_n, "base_win": base, "deltas": deltas},
                      f, indent=1)
        print(f"\nwrote {OVERRIDES} with {len(deltas)} deltas")
    else:
        # 4th audit 6.5/7.1: SHADOW MODE default — the live overrides stay
        # frozen until a human reviews the holdout agreement and runs --apply
        # explicitly. Maintenance no longer auto-applies.
        shadow_path = os.path.join(DESK, "aegis_weight_overrides.shadow.json")
        with open(shadow_path, "w") as f:
            json.dump({"generated_ts": __import__("time").time(),
                       "mode": "SHADOW — NOT applied",
                       "base_win": base, "train_n": len(train), "valid_n": len(valid),
                       "flags": shadow, "deltas": deltas}, f, indent=1)
        print(f"\n(SHADOW MODE: {len(deltas)} deltas computed, NOT applied. "
              f"Reviewed output: {shadow_path})")
        print("run with --apply to write aegis_weight_overrides.json (only after "
              "reviewing holdout agreement)")


if __name__ == "__main__":
    main()

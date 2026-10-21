#!/usr/bin/env python3
"""
Memecoin Quant Desk — Post-Entry Position Monitor v2 (quick-flip + conviction mode)

Strategy: buy ALL Telegram calls; default quick flips (minutes-2h); hold LONGER when
conviction is high (fresh narrative, tech works, FOMO inflow, slow-graduation fill,
smart wallets behind it). Autopilot exits: narrative dead / volume drying / good-wallet
selling / liq collapse / hard stop / profit clips.

Usage:
  position_monitor.py add <mint> <entry_px> <size> [--conviction] [--strategy X]
  position_monitor.py status [mint]
  position_monitor.py watch [mint] [--interval 15] [--minutes 10]
  position_monitor.py close <mint> <exit_px> <reason>
  position_monitor.py list
"""
import json, sys, os, time, argparse
from desk_common import (dex_pair, db, log_event, init_schema, rugcheck,
                         market_for)
from signals import token_intel, holder_growth, holder_churn, fresh_wallet_power
import aegis

DESK_DIR = os.path.expanduser("~/memecoin-quant")
POS_FILE = os.path.join(DESK_DIR, "positions.json")
# Oct 20 audit (P4): ONE book fee constant. The desk books 3.0%/side
# (scaleout.PAPER_FEE_PCT); the monitor's DISPLAY must use the same number.
try:
    from bot.position_manager.scaleout import PAPER_FEE_PCT as _PAPER_FEE
    FEE_BPS = _PAPER_FEE
except Exception:
    FEE_BPS = 3.0

# MC-adaptive exit plan: (mc_floor, clip1_mult, clip2_mult, stop_loss%)
# Oct 20 OFF tape retune (user: "most calls do at least 50% — engine broken?"):
# the 12h PumpAPI tape (170 calls) PROVED the old 1.10-clip/1.30-TP/0.15-stop
# ladder was stop-then-run suicide — 65% of calls dip below 0.85 BEFORE hitting
# 1.30 (median peak 1.73x; 58% reach 1.5x), so the tight stop booked -15% on the
# shakeout and the clip gave away half at +10% into the run. Tape sweep (same
# fills, only exits changed): old ladder -3.9%/call -> TP2.0/SL0.75 +5.2%,
# TP2.5/SL0.75 +5.4%. Direction holds in BOTH half-windows. No 1.10 clip.
EXIT_TIERS = [
    (0,        2.00, 2.50, 0.25),
    (40_000,   2.00, 2.50, 0.25),
    (150_000,  2.20, 2.80, 0.25),
    (1_500_000,2.50, 3.00, 0.25),
]
# Conviction mode relaxes targets (hold longer, ride further)
CONVICTION_MULT = 1.45
CONVICTION_STOP_WIDEN = 1.15

# TIME EXITS (Oct 21, CHARON A4/4.1 — validated externally and on our own tape):
# MAX_HOLD_S: hard time exit — sell at market once a position is this old.
# FIX 2 (Oct 21): winners are NEVER time-exited early (the 5-min MAX_HOLD
# strangled 11 winners at 1.04-1.73x, leaving +$157 on the table — vi2zUPH
# +2.50 cut at 1.16x after 6min, BAkdiNu +0.09 at 1.04x after 8min). MAX_HOLD
# now defaults to 30 min (quick-flip horizon) and only fires when NOT in profit
# (mult <= 1.0); green positions ride to TP2/clips instead.
MAX_HOLD_S = 1800   # 30 min — quick flip; RULE: only fires when mult <= 1.0
MAX_HOLD_CONVICTION_S = 7200   # 2h = user's max horizon for conviction holds
MAX_HOLD_TIER_S = {150_000: 3600, 1_500_000: 5400}  # higher MC = slower flips
SIDEWAYS_S = 600
SIDEWAYS_PNL_PCT = 2.0

def max_hold_for(mc):
    """Tier-aware MAX_HOLD: higher-MC tokens move slower -> longer cap."""
    t = MAX_HOLD_S
    for floor, sec in MAX_HOLD_TIER_S.items():
        if mc >= floor:
            t = sec
        else:
            break
    return t

ROUND_NUMS = [25_000, 50_000, 100_000, 250_000, 500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000]


def load_positions():
    if not os.path.exists(POS_FILE): return {}
    with open(POS_FILE) as f: return json.load(f)

def save_positions(pos):
    # Atomic write: write to temp, fsync, rename. Prevents torn reads (the crash that killed ws_monitor).
    import tempfile
    tmp = POS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(pos, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, POS_FILE)

def tier_for(mc):
    """MC-adaptive exit plan — returns the tier whose floor is <= mc (tightest applicable)."""
    t = EXIT_TIERS[0]
    for cand in EXIT_TIERS:
        if mc >= cand[0]:
            t = cand
        else:
            break
    return t

def round_stall(mc):
    for r in ROUND_NUMS:
        if mc < r * 0.98 and mc > r * 0.85: return r
    return None

def analyze(mint, pos):
    """Exit-signal engine v2. Returns dict with signals + recommended action."""
    mkt_ts = time.time()  # audit P1.2 (Oct 18): market observation timestamp — captured at the
                          # moment the snapshot is fetched, before any slow intelligence calls.
    pair = market_for(mint)  # PumpData feed first (Oct 20), dex fallback
    if not pair:
        return {"ok": False, "signal": "DATA_UNAVAILABLE", "action": "MANUAL_CHECK"}
    px = float(pair.get("priceUsd") or 0)
    mc = float(pair.get("marketCap") or 0)
    _liq_raw = (pair.get("liquidity") or {}).get("usd")
    # Oct 21 fix: None = UNKNOWN (feed hasn't measured liq yet), 0 = confirmed zero.
    # `or 0` collapsed missing data into a phantom DEAD/LIQUIDITY_WITHDRAWAL exit
    # on coins with live buy tape (CLOUT buys $2,742 sold @0.99x on "liq 0.0%").
    liq = float(_liq_raw) if _liq_raw not in (None, "") else None
    vol = pair.get("volume") or {}
    vol_m5, vol_h1, vol_h24 = vol.get("m5") or 0, vol.get("h1") or 0, vol.get("h24") or 0
    chg = pair.get("priceChange") or {}
    chg_m5 = chg.get("m5") or 0
    txns = pair.get("txns") or {}
    t5 = txns.get("m5") or {}
    b5, s5 = t5.get("buys") or 0, t5.get("sells") or 0

    entry = float(pos["entry_px"] or 0); size = float(pos.get("size") or 0)
    if entry <= 0 or size <= 0:
        return {"ok": False, "signal": "BAD_POSITION_DATA", "action": "MANUAL_CHECK"}
    mult = px / entry
    gross = size * mult
    net = gross * (1 - FEE_BPS / 100)
    pnl_usd = net - size
    pnl_pct = (net / size - 1) * 100

    conviction = bool(pos.get("conviction")) or (pos.get("strategy") == "conviction_hold")
    tier = tier_for(mc)
    c1 = tier[1] * (CONVICTION_MULT if conviction else 1.0)
    c2 = tier[2] * (CONVICTION_MULT if conviction else 1.0)
    stop = tier[3] * (CONVICTION_STOP_WIDEN if conviction else 1.0)

    signals = []
    action = "HOLD"

    # FIX 1 (Oct 21): FEED-SPEED STOP — evaluated FIRST, before token_intel /
    # holder_growth / exit_intel enrichment (those take 10-30s; by the time the
    # decision returns, a dump has gapped from 0.75x to 0.09x — the 14-trade
    # below-stop bleed class, -$110 in one book). The stop fires on the FIRST
    # tick that sees it, at the feed-cache price, and enrichment is skipped
    # (a position we're exiting needs no further research).
    hard_floor = float(pos.get("hard_stop_mult") or 0.50)
    if mult <= (1 - stop) or mult <= hard_floor:
        if mult <= (1 - stop):
            signals.append(f"TIER_STOP: {mult:.2f}x <= {1-stop:.2f}x (tier stop {stop:.0%}, MC ${mc:,.0f})")
            pos["tier_stop_triggered"] = True
        if mult <= hard_floor:
            signals.append(f"HARD_STOP: {mult:.2f}x <= {hard_floor:.2f}x price floor (liquidity-independent)")
            pos["hard_stop_triggered"] = True
        action = "EXIT_ALL"
        return {"ok": True, "token": mint[:10], "symbol": (pair.get("baseToken") or {}).get("symbol", "?"),
                "px": px, "mc": mc, "liq": liq, "mult": mult, "gross": gross, "net": net,
                "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "vol_m5": vol_m5, "vol_h1": vol_h1,
                "chg_m5": chg_m5, "b5": b5, "s5": s5, "age_min": (time.time() - float(pos.get("ts") or time.time())) / 60,
                "conviction": conviction, "strategy": "default",
                "holders": None, "churn": pos.get("holder_churn"),
                "wallet_power": pos.get("wallet_power"),
                "lifecycle": None, "lifecycle_why": [],
                "thesis": None, "exit_intel": None, "micro": None,
                "signals": signals, "action": action}

    # --- Intel from signal engine (slow-grad / wash / fragility) ---
    try:
        intel = token_intel(mint)
        fill_class = intel.get("fill_class", "unknown")
        wash_flags = (intel.get("wash") or {}).get("flags", [])
        frag = intel.get("fragility") or {}
        if fill_class == "bots":
            signals.append("SLOW-GRAD: fast/bot fill -> flip fast, no moonbags")
            c2 = min(c2, tier[2] * 1.1)
        elif fill_class in ("people", "very_slow") and not conviction:
            signals.append(f"SLOW-GRAD: {fill_class} fill -> real buyers, conviction candidate")
            conviction = True
            c1 = tier[1] * CONVICTION_MULT; c2 = tier[2] * CONVICTION_MULT
        for w in wash_flags[:3]:
            signals.append(f"WASH/LPI: {w}")
            stop = min(stop, tier[3] * 0.85)
        if frag.get("fragility", 0) > 70:
            signals.append(f"FRAGILITY {frag['fragility']}/100: {','.join(frag.get('reasons', [])[:2])}")
            c1 = min(c1, tier[1] * 1.15)
    except Exception as e:
        signals.append(f"intel_err: {str(e)[:60]}")

    # --- Profit clips ---
    if mult >= c2:
        signals.append(f"CLIP2: {mult:.2f}x >= {c2:.2f}x (exit all)")
        action = "CLIP_100"
    elif mult >= c1:
        signals.append(f"CLIP1: {mult:.2f}x >= {c1:.2f}x (sell 50%)")
        action = "CLIP_50"

    # --- Tier stop / hard stop: MOVED to the feed-speed block at the top of
    # analyze() (FIX 1, Oct 21) — fires on the FIRST tick at the feed-cache
    # price, before enrichment latency gaps the fill. Sticky floors set there
    # (tier_stop_triggered / hard_stop_triggered) are non-overridable below.

    # --- Liquidity collapse (FIX #3: missing-liq fallback) ---
    stored_entry_liq = pos.get("entry_liq")
    entry_liq = float(stored_entry_liq or liq or 0)
    # Oct 21 fix: liq may be None (UNKNOWN) — collapse checks only fire on
    # CONFIRMED liquidity values, never on missing data.
    # FIX 2 (Oct 21): EMERGENCY requires mult < 1.0 — a green coin (1.08-1.36x)
    # with halved liq is a CLIP_50 at most, never a panic exit (the 7 phantom
    # emergency-at-flat class that cut winners at 1.08-1.36x).
    if liq is not None and stored_entry_liq and float(stored_entry_liq) > 0 and liq < float(stored_entry_liq) * 0.5:
        if mult < 1.0:
            signals.append(f"LIQ_COLLAPSE: ${liq:,.0f} < 50% of entry ${float(stored_entry_liq):,.0f}")
            action = "EMERGENCY_EXIT"
        else:
            signals.append(f"LIQ_HALVED_BUT_GREEN: ${liq:,.0f} < 50% of entry but mult {mult:.2f}x — clip, don't panic")
            if action == "HOLD": action = "CLIP_50"
    elif liq is not None and not stored_entry_liq and liq > 0 and mult <= 0.75:
        # FIX #3: NO entry liquidity snapshot was stored at buy time — a >25%
        # price drawdown with any reported liquidity is collapse risk (price proxy)
        signals.append(f"LIQ_MISSING_DRAWDOWN: no entry liq, mult {mult:.2f} <= 0.75 with ${liq:,.0f} liq")
        action = "EMERGENCY_EXIT"
        pos["emergency_triggered"] = True  # non-overridable: contract may not downgrade

    # --- LIQ=0 -> DEAD (ghost/dumped position: no one can exit at $0) ---
    # Oct 21 fix: only CONFIRMED zero liq is DEAD. liq=None = UNKNOWN data —
    # a missing feed row was collapsing to 0 and emergency-exiting healthy coins.
    # FIX 2 (Oct 21): DEAD requires mult < 1.0 — a GREEN coin (mult >= 1.0) with
    # confirmed zero liq is CLIP_50/mark-to-market, never a total-loss write-off
    # (7-book class: EMERGENCY booked at 1.02-1.36x on coins that were UP).
    if liq is not None and liq <= 0:
        if mult < 1.0:
            signals.append("LIQ=0 — dead/dumped position, no liquidity to exit into")
            action = "DEAD"
        else:
            signals.append(f"LIQ=0_BUT_GREEN: mult {mult:.2f}x with zero liq — clip profit, don't write off")
            if action in ("HOLD", "CLIP_50"): action = "CLIP_50"

    # --- Volume drying (autopilot exit) ---
    entry_v = float(pos.get("entry_vol_h1") or vol_h1 or 0)
    age_min = (time.time() - float(pos.get("ts") or time.time())) / 60
    if entry_v > 0 and vol_h1 < entry_v * 0.4 and age_min > 20:
        signals.append(f"VOLUME_DRYING: h1 ${vol_h1:,.0f} < 40% of entry ${entry_v:,.0f} — narrative fading")
        if action == "HOLD": action = "CLIP_50"
        if mult <= 1.05: action = "EXIT_ALL"

    # --- Volume climax + stalling (exhaustion, persisted peaks) ---
    prior_peak = float(pos.get("prior_peak_m5_vol") or 0)
    pos["prior_peak_m5_vol"] = max(prior_peak, vol_m5)
    if prior_peak > 0 and vol_m5 >= prior_peak * 0.9 and chg_m5 < 2 and vol_m5 > 0:
        signals.append(f"VOLUME_CLIMAX_STALL: m5 vol ${vol_m5:,.0f} near peak (${prior_peak:,.0f}) price flat ({chg_m5:+.1f}%)")
        if action == "HOLD": action = "CLIP_50"
        if chg_m5 <= -2: action = "EXIT_ALL"

    # --- FOMO inflow (conviction boost: hold, scale out slower) ---
    tot5 = b5 + s5
    if tot5 >= 8 and b5 / tot5 > 0.75 and chg_m5 > 5 and vol_m5 > 0:
        signals.append(f"FOMO_INFLOW: {b5}B/{s5}S in 5m, +{chg_m5:.1f}%, vol ${vol_m5:,.0f} — riding it")
        if action == "CLIP_50": action = "HOLD"
        conviction = True

    # --- Round-number stall ---
    r = round_stall(mc)
    if r:
        signals.append(f"ROUND_STALL: MC ${mc:,.0f} in 85-95% of ${r:,.0f}")
        if chg_m5 < 10 and action == "HOLD": action = "CLIP_50"

    # --- Buy/sell divergence ---
    if tot5 >= 8 and b5 / tot5 > 0.7 and chg_m5 < 1:
        signals.append(f"DIVERGENCE: {b5}B/{s5}S but price flat — absorption, whales exiting")
        if action == "HOLD": action = "CLIP_50"

    # --- TIME EXITS (Oct 21, Charon A4/4.1) ---
    # MAX_HOLD: hard time exit for the quick-flip horizon; conviction holds get
    # the 2h user horizon. SIDEWAYS is the smarter EARLY exit (flat + old enough
    # frees capital) so it is checked FIRST; MAX_HOLD is the hard cap for
    # positions that are still moving. Sticky floors (hard stop / emergency)
    # already fired above and must NOT be downgraded.
    hold_s = MAX_HOLD_CONVICTION_S if conviction else max_hold_for(mc)
    age_s = age_min * 60
    if action in ("HOLD", "CLIP_50"):
        if age_s >= SIDEWAYS_S and abs(pnl_pct) < SIDEWAYS_PNL_PCT:
            signals.append(f"SIDEWAYS: {age_s/60:.0f}min open, |pnl| {abs(pnl_pct):.1f}% < {SIDEWAYS_PNL_PCT}% — frees capital")
            action = "EXIT_ALL"
            pos["time_exit_triggered"] = True
        elif age_s >= hold_s and mult <= 1.0:
            # FIX 2 (Oct 21): MAX_HOLD fires ONLY on losers/flat (mult <= 1.0).
            # Winners ride to TP2/clips — the 5-min MAX_HOLD was strangling
            # 11 winners at 1.04-1.73x (+$157 left on table) and emergency-ing
            # green coins. Green + old + stalled is handled by SIDEWAYS (flat)
            # and ATH_PROXIMITY / VOLUME_DRYING (stalled momentum).
            signals.append(f"MAX_HOLD: {age_s/60:.0f}min >= {hold_s/60:.0f}min at {mult:.2f}x — time exit (quick-flip horizon)")
            action = "EXIT_ALL"
            pos["time_exit_triggered"] = True
        elif age_s >= hold_s and mult > 1.0:
            signals.append(f"MAX_HOLD_SKIPPED: {age_s/60:.0f}min old but GREEN ({mult:.2f}x) — riding to TP2 (FIX 2)")

    # --- ATH-PROXIMITY CLIP (Oct 21, Charon topBlastRisk) ---
    # Charon: current/rangeHigh >= 0.85 = top-blast risk zone (within 15% of the
    # run's high, momentum fading -> round-trip risk). Complements round_stall
    # (85-95% of a ROUND number); this catches cliffs BELOW the high-water mark.
    try:
        _peak = float(pos.get("peak_price") or entry_px or 0)
        if _peak > 0 and px / _peak >= 0.85 and (chg_m5 or 0) < 2 and vol_m5 > 0:
            signals.append(f"ATH_PROXIMITY: px at {px/_peak*100:.0f}% of peak ${_peak} — momentum fading, round-trip risk")
            if action == "HOLD": action = "CLIP_50"
            if (chg_m5 or 0) <= -2: action = "EXIT_ALL"
    except Exception:
        pass

    # --- Behavior-index override (who's behind it) ---
    try:
        strat = aegis.strategy_for(mint)
    except Exception:
        strat = "default"
    if strat == "flip_fast":
        signals.append("BEHAVIOR: rug-deployer history -> tight flips")
        c1 = min(c1, tier[1] * 1.1)
        if mult >= c1 and action == "HOLD": action = "CLIP_50"
    elif strat == "front_run_exit":
        signals.append("BEHAVIOR: KOL-called token -> front-run KOL exit")
        if mult >= 1.2 and action in ("HOLD", "CLIP_50"): action = "CLIP_50"
    elif strat == "conviction_hold" and not conviction:
        signals.append("BEHAVIOR: smart wallets behind -> hold longer")
        conviction = True

    # --- GOOD_WALLET_SELLING REMOVED (Oct 21 simplification): source was CoinStats
    # --- top-holder balance — 406 credit-locked since Oct 16, always [].
    # --- BLIX holder movement (7.1) covers the same Yoda autopilot rule live.
    # --- DEEP-ENRICHMENT GATES (Oct 18 — throttled to once per position per 15 min) ---
    try:
        last_deep = pos.get("deep_enrich_ts") or 0
        if time.time() - last_deep > 900:  # 15-min throttle
            pos["deep_enrich_ts"] = time.time()
    except Exception:
        pass

    # --- BLIX holder/dev/sniper movement (7.1, Oct 18 — guarded local radar) ---
    try:
        from desk_common import blix_holders, blix_alive, blix_coin_trades
        if blix_alive():
            hd = blix_holders(mint, top=10)
            if hd:
                dev_w = hd.get("dev")
                if isinstance(dev_w, dict):
                    dev_pct_now = dev_w.get("pct") or 0
                    prev_dev = pos.get("blix_dev_pct")
                    if prev_dev is not None and dev_pct_now < prev_dev * 0.5:
                        signals.append(f"BLIX_DEV_DUMPING: dev {dev_pct_now:.2f}% < 50% of {prev_dev:.2f}%")
                        if action == "HOLD": action = "CLIP_50"
                        if mult <= 1.0: action = "EXIT_ALL"
                    pos["blix_dev_pct"] = dev_pct_now
                t10 = hd.get("top10") or 0
                # blix top10 is a PERCENTAGE (27.16 = 27.16%)
                t10_pct = t10 if t10 > 1 else t10 * 100
                if t10_pct > 75:
                    signals.append(f"BLIX_TOP10_CONCENTRATED: {t10_pct:.0f}% in top 10")
                    if mult <= 1.0: action = "CLIP_50"
    except Exception:
        pass

    # --- BLIX narrative + market-regime context (7.4/7.6, Oct 18 — guarded) ---
    try:
        from desk_common import blix_narratives, blix_market, blix_alive
        if blix_alive():
            sym = (pair.get("baseToken") or {}).get("symbol") or ""
            themes = blix_narratives()
            for th in themes[:6]:
                name = (th.get("name") or "").lower()
                heat = th.get("heat") or 0
                if sym and (sym.lower() in name or any(sym.lower() in (c.get("symbol") or "").lower() for c in (th.get("top") or [])[:6])):
                    signals.append(f"BLIX_NARRATIVE: theme '{th.get('name')}' heat={heat} lift={th.get('launchLift')}")
                    pos["blix_narrative"] = th.get("name")
                    # Recalib Oct 18: narrative heat now influences action — a HOT narrative
                    # suppresses premature clips (ride the theme), a COLD narrative + green
                    # prompts taking profit (theme fading).
                    if heat >= 70 and action == "CLIP_50" and mult < 1.1:
                        action = "HOLD"  # don't clip early while theme is hot and gains are small
                    elif heat < 40 and action == "HOLD" and mult >= 1.0:
                        action = "CLIP_50"  # cold narrative + green = take some now
                    break
            mkt = blix_market()
            sol_now = ((mkt or {}).get("sol") or {}).get("usd")
            prev_sol = pos.get("blix_sol_usd")
            if sol_now and prev_sol and abs(sol_now - prev_sol) / prev_sol > 0.02:
                d = (sol_now - prev_sol) / prev_sol * 100
                signals.append(f"BLIX_SOL_REGIME: SOL {d:+.1f}% (${sol_now:.0f})")
            if sol_now:
                pos["blix_sol_usd"] = sol_now
    except Exception:
        pass

    # --- BLIX empirical track record (7.6, Oct 18) — measured 1h hit rates for context ---
    try:
        from desk_common import blix_signal_track, blix_alive
        if blix_alive() and signals:
            tr = blix_signal_track()  # overall
            if tr and tr.get("events", 0) >= 5:
                up20 = tr.get("up20") or 0
                med = tr.get("median1h")
                signals.append(f"BLIX_TRACK: {up20}/{tr.get('events')} up20+ · median1h={med} (empirical)")
    except Exception:
        pass

    # --- USD buy/sell imbalance from BLIX tape (audit Oct 19 fix #4: Bitquery is
    # credit-locked 402; the local tape gives the same signal for free). ---
    try:
        from desk_common import blix_coin_trades, blix_alive
        if blix_alive():
            trs = blix_coin_trades(mint, limit=60) or []
            buy_usd = sum(float(t.get("usd") or 0) for t in trs if t.get("side") == "buy")
            sell_usd = sum(float(t.get("usd") or 0) for t in trs if t.get("side") == "sell")
            tot = buy_usd + sell_usd
            if tot > 500:
                ratio = buy_usd / tot
                if ratio < 0.35:
                    signals.append(f"TAPE SELL IMBALANCE: buys ${buy_usd:,.0f} vs sells ${sell_usd:,.0f}")
                    if action == "HOLD": action = "CLIP_50"
                elif ratio > 0.7:
                    signals.append(f"TAPE BUY IMBALANCE: buys ${buy_usd:,.0f} vs sells ${sell_usd:,.0f}")
    except Exception:
        pass

    # --- Holder growth (Helius, refreshed every 5 min per position) ---
    hg = pos.get("holder_growth")
    try:
        now = time.time()
        last = float(pos.get("last_holders_ts") or 0)
        if now - last >= 300:  # 5-minute throttle — pages 100 txs, keep it cheap
            fresh = holder_growth(mint)
            if fresh:
                prev = pos.get("holder_growth")
                pos["holder_growth"] = fresh
                pos["last_holders_ts"] = now
                if prev:
                    delta = int(fresh.get("unique_buyers") or 0) - int(prev.get("unique_buyers") or 0)
                    pos["holder_delta"] = delta
                    signals.append(f"HOLDER: {fresh.get('buyers_1h')} buyers 1h, {fresh.get('unique_buyers')} unique sample, Δ{delta:+d}")
                    # No NEW buyers while holding = inflow dried -> clip
                    if fresh.get("buyers_1h", 0) <= 1 and age_min > 30 and int(fresh.get("unique_buyers") or 0) > 5:
                        signals.append("HOLDER INFLOW DRIED — no fresh buyers 1h")
                        if action == "HOLD": action = "CLIP_50"
                else:
                    signals.append(f"HOLDER: {fresh.get('buyers_1h')} buyers 1h / {fresh.get('unique_buyers')} unique sample")
                hg = fresh
            # --- Holder churn/retention (DAS, same 5-min throttle, cheap: 1 paged read) ---
            # --- and New-wallet purchasing power (GeckoTerminal trades, keyless) ---
            # P1-4 (Oct 20): churn also had NO throttle — every tick paid a 1k-tx page.
            # Reuse the same last_holders_ts window so both refreshes share one cadence.
            try:
                if now - last >= 300:
                    churn = holder_churn(mint)
                    pos["holder_churn"] = churn
                    pos["last_holders_ts"] = now
                else:
                    churn = pos.get("holder_churn") or {}
                if churn:
                    if churn.get("retention") is not None:
                        ret_pct = churn["retention"] * 100
                        signals.append(f"RETENTION: {ret_pct:.0f}% of holders stayed (churned {churn.get('churned')} of {churn.get('holders_prev')})")
                        # community shrinking: <60% of prior holders still in top-1000 -> churn escalation
                        if ret_pct < 60 and churn.get("holders_prev", 0) >= 100:
                            signals.append("HOLDER CHURN — holders are leaving")
                            if action == "HOLD": action = "CLIP_50"
                    else:
                        signals.append(f"RETAIN: baseline snapshot — {churn.get('holders_now')} holders")
            except Exception:
                pass
            try:
                power = fresh_wallet_power(mint)
                pos["wallet_power"] = power
                if power:
                    nb = power.get("new_buyer_count") or 0
                    if nb >= 5:
                        signals.append(f"NEW MONEY: {nb} fresh wallets put in ${power.get('sum_buy_usd'):,.0f} (biggest ${power.get('biggest_buy_usd'):,.0f})")
                    elif nb == 0 and age_min > 30:
                        signals.append("NO fresh wallets buying — only repeat holders")
                    # Build 6: seed wallet_history from fresh buyers (cohort signal source)
                    try:
                        from bot.persistence import wallet_history_add
                        for wb in (power.get("wallet_buys") or [])[:15]:
                            # audit P1-6: mark as NOTIONAL USD explicitly — never feed
                            # this into token-balance comparisons (wallet_intel filters snapshots)
                            wallet_history_add(wb["wallet"], mint, wb.get("usd", 0.0), "buy_usd_notional")
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    # ===== doc-2 intelligence layer (exit intel / microstructure / wallets /
    # lifecycle / thesis / events) — evidence for the decision contract =====
    exit_info = micro = wallet_sig = None
    try:
        from bot.intelligence.exit_intel import exit_intel
        exit_info = exit_intel(mint, pos, entry_usd=size, entry_liq=entry_liq or liq, mark_px=px)
        if exit_info:
            pos["last_exit_intel"] = exit_info
            ec = exit_info.get("exit_capacity_ratio")
            if ec is not None and ec > 0.3 and mult >= 1.1:
                signals.append(f"EXIT_CAPACITY: selling our ${size:,.0f} moves {ec*100:.0f}% of pool liq — profit not fully realizable")
                if action == "HOLD": action = "CLIP_50"
    except Exception:
        pass
    try:
        from bot.intelligence.microstructure import microstructure
        micro = microstructure(mint)
        if micro:
            pos["last_micro"] = micro
            cvd = micro.get("cvd")
            if cvd is not None and cvd < -0.4 and (micro.get("n_sells") or 0) > 10:
                signals.append(f"MICRO CVD {cvd:+.2f} — aggressive sells dominating ({micro.get('n_sells')} sells)")
                if action == "HOLD": action = "CLIP_50"
            bc = micro.get("buy_concentration")
            if bc is not None and bc >= 0.6:
                signals.append(f"MICRO BUY CONCENTRATION {bc:.0%} — buying from very few wallets")
    except Exception:
        pass
    try:
        from bot.intelligence.wallet_intel import wallet_distribution_signal
        wallet_sig = wallet_distribution_signal(mint)
        if wallet_sig and wallet_sig.get("signal") == "DISTRIBUTION":
            signals.append(f"WALLET DIST: {wallet_sig.get('distributing')} of {wallet_sig.get('known_tracked')} tracked top holders distributing")
            if action == "HOLD": action = "CLIP_50"
            if mult <= 1.0: action = "EXIT_ALL"
    except Exception:
        pass
    try:
        from bot.intelligence.cohort import cohort_signal_for
        cs = cohort_signal_for(mint)
        if cs and cs.get("signal") in ("ACCUMULATION", "DISTRIBUTION"):
            signals.append(f"COHORT: {cs.get('entities_buying')} entities acc / {cs.get('entities_selling')} dist → {cs['signal']}")
            if cs.get("signal") == "DISTRIBUTION" and action == "HOLD":
                action = "CLIP_50"
    except Exception:
        pass
    try:
        from bot.intelligence.lifecycle import classify
        lifecycle, life_why = classify(age_min=age_min, holder_retention=(churn or {}).get("retention") if (churn := pos.get("holder_churn")) else None,
                                       fresh_buyers=(pos.get("wallet_power") or {}).get("new_buyer_count"),
                                       unique_buyers=(hg or {}).get("unique_buyers"), liq=liq,
                                       entry_liq=entry_liq or liq, vol_h1=vol_h1, entry_vol=entry_v, mult=mult)
        pos["lifecycle"] = lifecycle; pos["lifecycle_why"] = life_why
    except Exception:
        lifecycle, life_why = None, []
    try:
        from bot.intelligence.thesis import evaluate as thesis_evaluate
        retention = (pos.get("holder_churn") or {}).get("retention")
        thesis = thesis_evaluate(mint, pos, signals, extra={
            "retention": retention,
            "fresh_buyers": (pos.get("wallet_power") or {}).get("new_buyer_count"),
            "vol_ratio": (vol_h1 / entry_v) if entry_v else None,
            "liq_pct_of_entry": ((liq / entry_liq * 100) if (entry_liq and liq is not None) else None),
            "mult": mult,
        })
        pos["thesis"] = thesis
        if thesis.get("confidence") is not None and thesis["confidence"] < 35:
            signals.append(f"THESIS WEAK ({thesis['confidence']:.0f}/100): {', '.join(thesis.get('weak', [])[:2])}")
            if action == "HOLD" and mult < 1.0: action = "CLIP_50"
    except Exception:
        thesis = None
    try:
        from bot.intelligence.event_alerts import detect_all
        from bot.persistence import log_decision, log_evidence
        from bot.decision_engine import decide
        from bot.decision_engine.evidence_ttl import weight_signals
        events = detect_all(mint, wallet_signal=wallet_sig, exit_info=exit_info, micro=micro, mult=mult)
        for e in events:
            if e["sev"] in ("high", "critical"):
                signals.append(f"EVENT {e['type']}: {e['detail']}")
                # FIX 2 (Oct 21): critical events require mult < 1.0 to panic —
                # a green coin at 1.08-1.36x must never be emergency-exited (the
                # 7-book phantom-emergency class). Green + critical = CLIP_50.
                if e["sev"] == "critical" and mult < 1.0: action = "EMERGENCY_EXIT"
                elif e["sev"] == "critical" and mult >= 1.0:
                    signals.append(f"EVENT_CRITICAL_BUT_GREEN: {e['type']} at {mult:.2f}x — clip, don't panic")
                    if action == "HOLD": action = "CLIP_50"
        # Build 2: persist each signal as decaying evidence + compute TTL weights
        now_ev = time.time()
        for s in signals:
            try:
                log_evidence(mint, s, observed_ts=now_ev)
            except Exception:
                pass
        try:
            from bot.decision_engine.evidence_ttl import historical_weight_signals
            ttl_weights = historical_weight_signals(mint, signals, now=now_ev)  # audit P1-5: real observed timestamps + dedupe
        except Exception:
            ttl_weights = weight_signals([(s, now_ev) for s in signals])
        # Pass real paper-book cash so ADD decisions are possible (audit F5)
        try:
            pb = json.load(open("/home/agentuser/memecoin-quant/paper_book.json"))
            book_cash = float(pb.get("cash") or 0)
            book_pos = {(p.get("mint") or ""): s for s, p in (pb.get("positions") or {}).items()}
        except Exception:
            book_cash = 0.0
            book_pos = {}
        # doc-1 decision contract: log every tick's decision with full evidence
        contract = decide(mint, pos, signals, mc, liq, mult, age_min,
                          extra={"cash": book_cash, "ttl": (ttl_weights[0], ttl_weights[1])})
        # audit P1-2: every contract passes the deterministic risk gate before apply
        try:
            from bot.decision_engine.risk_gate import gate
            sym_key = book_pos.get(mint)
            if sym_key:
                # audit P1 (Oct 18): pass a real market observation timestamp so the
                # gate's staleness rule is ACTIVE, not silently disabled by None.
                # audit P1.2 (Oct 18): use the TRUE snapshot fetch time (mkt_ts) rather than
                # the end-of-analysis now_ev — slow forensic calls must not make stale data
                # appear fresh to the gate.
                allowed, gnotes = gate(contract, pb, sym_key, market_ts=mkt_ts, quote_ts=None)
                if allowed != contract.get("action"):
                    contract = dict(contract)
                    contract["action"] = allowed
                    contract["reason_codes"] = list(contract.get("reason_codes") or []) + ["RISK_GATE:" + ";".join(gnotes)]
        except Exception as e:
            # audit P1 (Oct 18): a broken gate must be OBSERVABLE, never a silent pass
            signals.append(f"RISK_GATE_UNAVAILABLE: {str(e)[:60]}")
        log_decision(contract, mint)
        pos["last_decision"] = {k: contract[k] for k in ("action", "sell_fraction", "confidence", "thesis_state")}
        pos["last_decision_px"] = px
        if contract["action"] not in ("HOLD", action):
            # HARD_STOP / EMERGENCY (liq proxy) are non-overridable: the risk
            # engine may UPGRADE but never DOWNGRADE a floor/collapse exit.
            # audit P0.3 (Oct 18): ANY full-exit intent set by intelligence branches
            # (wallet-distribution, BLIX dev-dump, sell-block) must also stay sticky —
            # the decision engine can otherwise downgrade it to SCALE_OUT via thesis.
            if (pos.get("hard_stop_triggered") or pos.get("emergency_triggered")
                    or action in ("EXIT_ALL", "EMERGENCY_EXIT", "FULL_EXIT", "DEAD")):
                if contract["action"] in ("EMERGENCY_EXIT", "DEAD", "FULL_EXIT", "EXIT_ALL"):
                    action = contract["action"]
                # else keep the stronger action — floor/collapse/full-exit wins
            else:
                action = contract["action"]  # risk engine veto/upgrade
    except Exception:
        pass

    return {"ok": True, "token": mint[:10], "symbol": (pair.get("baseToken") or {}).get("symbol", "?"),
            "px": px, "mc": mc, "liq": liq, "mult": mult, "gross": gross, "net": net,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "vol_m5": vol_m5, "vol_h1": vol_h1,
            "chg_m5": chg_m5, "b5": b5, "s5": s5, "age_min": age_min,
            "conviction": conviction, "strategy": strat,
            "holders": hg,
            "churn": pos.get("holder_churn"),
            "wallet_power": pos.get("wallet_power"),
            "lifecycle": pos.get("lifecycle"), "lifecycle_why": pos.get("lifecycle_why"),
            "thesis": pos.get("thesis"), "exit_intel": pos.get("last_exit_intel"),
            "micro": pos.get("last_micro"),
            "signals": signals, "action": action}


def fmt(r):
    L = [f"  {r['symbol']} {r['token']}... px=${r['px']:.8f} mult={r['mult']:.2f}x PnL={r['pnl_pct']:+.1f}% (${r['pnl_usd']:+.2f}) {'🔒CONVICTION' if r['conviction'] else ''}"]
    liq_s = "n/a" if r['liq'] is None else f"{r['liq']:,.0f}"
    L.append(f"  MC=${r['mc']:,.0f} liq=${liq_s} | 5m vol=${r['vol_m5']:,.0f} chg {r['chg_m5']:+.1f}% | B/S {r['b5']}/{r['s5']} | age={r['age_min']:.0f}m")
    h = r.get("holders")
    if h:
        L.append(f"  🧠 buyers 1h={h.get('buyers_1h')} 6h={h.get('buyers_6h')} unique={h.get('unique_buyers')} sellers={h.get('unique_sellers')}")
    # holder churn/retention + fresh-wallet power lines (only when present)
    ch = r.get("churn")
    if ch and ch.get("retention") is not None:
        L.append(f"  💳 retention={ch['retention']*100:.0f}% (from {ch.get('holders_prev')}) churned={ch.get('churned')} added={ch.get('holders_added')}")
    wp = r.get("wallet_power")
    if wp:
        L.append(f"  🆕 fresh-wallet buying in window: {wp.get('new_buyer_count')} wallets ${wp.get('sum_buy_usd'):,.0f} (biggest ${wp.get('biggest_buy_usd'):,.0f})")
    if r.get("lifecycle"):
        L.append(f"  🔄 lifecycle: {r['lifecycle']} ({'; '.join((r.get('lifecycle_why') or [])[:2])})")
    t = r.get("thesis")
    if t and t.get("confidence") is not None:
        L.append(f"  📐 thesis {t['confidence']:.0f}/100 strong=[{', '.join((t.get('strong') or [])[:2])}] weak=[{', '.join((t.get('weak') or [])[:2])}]")
    xi = r.get("exit_intel")
    if xi and xi.get("exit_capacity_ratio") is not None:
        L.append(f"  💸 exit capacity: selling full pos = {xi['exit_capacity_ratio']*100:.1f}% of pool liq")
    for s in r["signals"]: L.append(f"  ⚠ {s}")
    L.append(f"  >>> ACTION: {r['action']}")
    return "\n".join(L)


def cmd_add(args):
    pos = load_positions()
    pair = market_for(args.mint)  # PumpData feed first (Oct 20), dex fallback
    if not pair:
        print(f"ERROR: no pair data for {args.mint}"); return 1
    px = float(pair.get("priceUsd") or 0)
    mc = float(pair.get("marketCap") or 0)
    _liq_raw = (pair.get("liquidity") or {}).get("usd")
    # Oct 21 fix: None = UNKNOWN (feed hasn't measured liq yet), 0 = confirmed zero.
    # `or 0` collapsed missing data into a phantom DEAD/LIQUIDITY_WITHDRAWAL exit
    # on coins with live buy tape (CLOUT buys $2,742 sold @0.99x on "liq 0.0%").
    liq = float(_liq_raw) if _liq_raw not in (None, "") else None
    vh1 = (pair.get("volume") or {}).get("h1") or 0
    intel = token_intel(args.mint)
    try:
        strat = args.strategy or aegis.strategy_for(args.mint) or "default"
    except Exception:
        strat = "default"
    pos[args.mint] = {"entry_px": args.entry, "size": args.size, "ts": time.time(),
                      "entry_mc": mc, "entry_liq": liq, "entry_vol_h1": vh1,
                      "symbol": (pair.get("baseToken") or {}).get("symbol"),
                      "conviction": 1 if args.conviction else 0, "strategy": strat,
                      "fill_class": intel.get("fill_class"), "fill_min": intel.get("fill_min")}
    save_positions(pos)
    init_schema()
    con = db()
    con.execute("INSERT OR REPLACE INTO positions(mint, entry_px, size, entry_mc, entry_liq, opened_ts, strategy, conviction) VALUES(?,?,?,?,?,?,?,?)",
                (args.mint, args.entry, args.size, mc, liq, time.time(), strat, 1 if args.conviction else 0))
    con.commit(); con.close()
    log_event({"event": "POSITION_OPEN", "mint": args.mint, "entry": args.entry, "size": args.size,
               "mc": mc, "liq": liq, "strategy": strat, "conviction": args.conviction,
               "fill_class": intel.get("fill_class")})
    print(f"ADDED {args.mint[:12]}... px=${px:.8f} MC=${mc:,.0f} liq=${liq:,.0f} strat={strat} fill={intel.get('fill_class')}")
    return 0


def cmd_status(args):
    pos = load_positions()
    mints = [args.mint] if args.mint and args.mint in pos else list(pos.keys())
    if not mints: print("No positions. add <mint> <entry_px> <size>"); return 1
    for m in mints:
        r = analyze(m, pos[m])
        if not r["ok"]: print(f"  {m[:12]}...: no live data"); continue
        print(fmt(r))
        log_event({"event": "MONITOR", "mint": m, **{k: r.get(k) for k in ("px","mc","liq","mult","pnl_pct","action","conviction")}})
    save_positions(pos)
    return 0


def cmd_watch(args):
    pos = load_positions()
    mints = [args.mint] if args.mint and args.mint in pos else list(pos.keys())
    if not mints: print("No positions."); return 1
    start = time.time()
    print(f"Watching {len(mints)} position(s) every {args.interval}s for {args.minutes}m — Ctrl+C to stop\n")
    try:
        while time.time() - start < args.minutes * 60:
            for m in mints:
                r = analyze(m, pos[m])
                if not r["ok"]: continue
                print(time.strftime("%H:%M:%S"), fmt(r), "\n")
                log_event({"event": "TICK", "mint": m, **{k: r.get(k) for k in ("px","mc","liq","mult","pnl_pct","action","conviction")}})
            save_positions(pos)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("stopped.")
    return 0


def cmd_close(args):
    pos = load_positions()
    if args.mint not in pos:
        print(f"Not tracked: {args.mint}"); return 1
    p = pos.pop(args.mint)
    save_positions(pos)
    # keep DB positions table in sync (it was previously orphaned on close)
    try:
        con = db()
        con.execute("DELETE FROM positions WHERE mint=?", (args.mint,))
        con.commit(); con.close()
    except Exception:
        pass
    mult = float(args.exit_px) / float(p["entry_px"])
    gross = float(p["size"]) * mult * (1 - FEE_BPS / 100)
    pnl = gross - float(p["size"])
    hold_min = (time.time() - float(p["ts"])) / 60
    log_event({"event": "POSITION_CLOSE", "mint": args.mint, "exit_px": args.exit_px,
               "mult": mult, "realized_usd": pnl, "reason": args.reason, "hold_min": hold_min})
    # --- deployer memory outcome (audit Oct 19 fix #1): rug/liq-collapse closes
    # increment deployers.rug_count so deployer_offset + funder→rug checks compound.
    reason_l = (args.reason or "").lower()
    is_rug = ("rug" in reason_l or "collapse" in reason_l or "dead" in reason_l
              or "liq=0" in reason_l or mult <= 0.15)
    if is_rug:
        try:
            con = db()
            dep = con.execute("SELECT deployer FROM coins WHERE mint=?", (args.mint,)).fetchone()
            if dep and dep["deployer"]:
                con.execute("""UPDATE deployers SET rug_count = rug_count + 1,
                    score_offset = MAX(score_offset - 5, -25) WHERE deployer=?""", (dep["deployer"],))
                con.commit()
                print(f"  deployer {dep['deployer'][:8]}... rug_count incremented (memory layer compounding)")
        except Exception as e:
            print(f"  deployer-memory update skipped: {e}")
    print(f"CLOSED {args.mint[:12]}... mult={mult:.2f}x realized=${pnl:+.2f} hold={hold_min:.0f}m reason={args.reason}")
    return 0


def cmd_list(args):
    pos = load_positions()
    if not pos: print("No positions."); return 0
    for m, p in pos.items():
        hrs = (time.time() - p["ts"]) / 3600
        print(f"  {m[:16]}... ${p.get('symbol')} entry=${p['entry_px']:.8f} size=${p['size']:.0f} age={hrs:.1f}h strat={p.get('strategy')} conv={'Y' if p.get('conviction') else 'N'}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Position monitor v2")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("add"); a.add_argument("mint"); a.add_argument("entry", type=float); a.add_argument("size", type=float)
    a.add_argument("--conviction", action="store_true"); a.add_argument("--strategy")
    s = sub.add_parser("status"); s.add_argument("mint", nargs="?")
    w = sub.add_parser("watch"); w.add_argument("mint", nargs="?"); w.add_argument("--interval", type=int, default=15); w.add_argument("--minutes", type=int, default=10)
    c = sub.add_parser("close"); c.add_argument("mint"); c.add_argument("exit_px", type=float); c.add_argument("reason")
    l = sub.add_parser("list")
    args = ap.parse_args()
    if not args.cmd: ap.print_help(); return 1
    return {"add": cmd_add, "status": cmd_status, "watch": cmd_watch, "close": cmd_close, "list": cmd_list}[args.cmd](args)

if __name__ == "__main__":
    sys.exit(main())
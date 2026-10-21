#!/usr/bin/env python3
"""AEGIS-lite: scam filter with a second brain (SQLite memory across launches).

Six checks (mint/freeze/top-10/bundle/LP/metadata) + deployer & first-buyer memory.
Score 0-100; deployer history adjusts ±25; clean contract from dirty wallet = distrust.
Also hosts the behavior index (actors -> post-buy strategy).
"""
import json, time, re
from desk_common import (db, init_schema, dex_pair, rugcheck, rpc, log_event, helius_txs,
                         deep_cache_get, deep_cache_put,
                         helius_asc_txs, mother_wallet, ave_risk,
                         goplus_security, budget)

# Learning-loop cache (Oct 19): {ts, data} for aegis_weight_overrides.json
_LRN = {"ts": 0, "data": {}}

# ---------- Six on-chain checks ----------

def check_authorities(mint):
    """1) mint authority, 2) freeze authority via RPC jsonParsed."""
    try:
        acc = rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        if not acc or not acc.get("value"):
            return {"error": "no_mint_account"}
        info = acc["value"]["data"]["parsed"]["info"]
        return {
            "mint_authority": info.get("mintAuthority"),
            "freeze_authority": info.get("freezeAuthority"),
            "supply": int(info.get("supply", 0)),
            "decimals": info.get("decimals"),
            "token_program": acc["value"].get("owner"),
        }
    except Exception as e:
        return {"error": str(e)[:100]}

def check_top10_and_lp(mint):
    """3) top-10 concentration, 4) bundle/insider risks, 5) LP lock — from Rugcheck.
    Full report carries creator/insiders/rugged/lockers; summary adds lpLockedPct."""
    report = rugcheck(mint, full=True)
    summary = rugcheck(mint, full=False)
    out = {}
    if report and isinstance(report, dict):
        top = report.get("topHolders") or []
        top10 = sum(float(h.get("pct") or 0) for h in top[:10]) if top else None
        lockers = report.get("lockers") or []
        locker_locked = lockers[0].get("lockedPct", 0) if lockers else None
        out = {
            "score": report.get("score"),
            "top10_pct": top10,
            "top_holder_count": len(top),
            "top_holder_pct": (top[0].get("pct") if top else None),
            "creator": report.get("creator"),
            "creator_balance": report.get("creatorBalance"),
            "holders": report.get("totalHolders"),
            "rugged": bool(report.get("rugged")),
            "mint_authority": report.get("mintAuthority"),
            "freeze_authority": report.get("freezeAuthority"),
            "transfer_fee": report.get("transferFee"),
            "graph_insiders": bool(report.get("graphInsidersDetected")),
            "insider_networks": report.get("insiderNetworks") or [],
            "creator_tokens": report.get("creatorTokens") or [],
            "known_accounts": report.get("knownAccounts") or [],
            "launchpad": report.get("launchpad"), "deploy_platform": report.get("deployPlatform"),
            "locker_locked_pct": locker_locked,
            "locker_scan_status": report.get("lockerScanStatus"),
            "markets_count": len(report.get("markets") or []),
            "risks": report.get("risks", []),
            "events": report.get("events", []),
        }
    if summary and isinstance(summary, dict):
        out.setdefault("lp_locked_pct", summary.get("lpLockedPct"))
        out.setdefault("score", summary.get("score"))
        out.setdefault("risks", summary.get("risks", []))
        out.setdefault("token_program", summary.get("tokenProgram"))
    if not out:
        return {"error": "no_rugcheck"}
    return out

def check_metadata_and_liquidity(mint):
    """6) metadata (symbol/name/dupe-likelihood) + liquidity from DexScreener."""
    pair = dex_pair(mint)
    if not pair:
        return {"error": "no_pair"}
    base = pair.get("baseToken") or {}
    liq = (pair.get("liquidity") or {}).get("usd") or 0
    vol = pair.get("volume") or {}
    return {
        "symbol": base.get("symbol"), "name": base.get("name"),
        "dex": pair.get("dexId"), "mc": pair.get("marketCap"),
        "liq": liq, "vol_h24": vol.get("h24"), "vol_m5": vol.get("m5"),
        "created_at": pair.get("pairCreatedAt"),
        "buys24": (pair.get("txns", {}).get("h24") or {}).get("buys"),
        "sells24": (pair.get("txns", {}).get("h24") or {}).get("sells"),
        "price_usd": pair.get("priceUsd"),
        "price_chg": pair.get("priceChange"),
        "address_suffix_ok": (mint[-4:] == "pump"),
    }

# ---------- Deployer / first-buyer memory ----------

def resolve_deployer(mint):
    """Oldest tx on the mint => fee payer (deployer). Best-effort, capped pages."""
    try:
        cutoff_sig = None
        for _ in range(8):
            p = [mint, {"limit": 1000}]
            if cutoff_sig: p[1]["before"] = cutoff_sig
            res = rpc("getSignaturesForAddress", p) or []
            if not res: break
            oldest = res[-1]
            tx = rpc("getTransaction", [oldest["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            if tx:
                meta = tx.get("meta") or {}
                fp = None
                try:
                    fp = tx.get("transaction", {}).get("message", {}).get("accountKeys", [{}])[0]
                except Exception:
                    pass
                if not fp: fp = (meta.get("loadedAddresses", {}).get("writable", [None]) or [None])[0]
                return fp, oldest.get("blockTime"), oldest["signature"]
            cutoff_sig = oldest["signature"]
            if len(res) < 1000: break
    except Exception:
        pass
    return None, None, None

def remember_deployer(deployer, mint, ts):
    if not deployer: return
    con = db()
    row = con.execute("SELECT * FROM deployers WHERE deployer=?", (deployer,)).fetchone()
    if row:
        con.execute("UPDATE deployers SET coin_count=coin_count+1, first_seen=? WHERE deployer=?",
                    (min(row["first_seen"], ts or time.time()), deployer))
    else:
        con.execute("INSERT INTO deployers(deployer, first_seen, coin_count) VALUES(?,?,1)",
                    (deployer, ts or time.time()))
    con.commit(); con.close()

def record_first_buyers(mint, deployer, n=5):
    """Oldest n signatures on the mint => signers (first-block buyers).

    getSignaturesForAddress returns NEWEST first; walk to the oldest page to
    avoid labeling recent traders as early buyers (audit F: chronological order).
    Speed (Oct 19): page limit 100 -> 1000 (RPC max) so a 40-page walk becomes
    ~4 calls; only a few mints exceed 4k sigs in the first minutes anyway.
    """
    try:
        sigs = []
        before = None
        for _ in range(10):  # max 10 pages back (1000/page = 10k sigs)
            params = [mint, {"limit": 1000}]
            if before:
                params[1]["before"] = before
            page = rpc("getSignaturesForAddress", params) or []
            if not page:
                break
            sigs.extend(page)
            if len(page) < 1000:
                break
            before = page[-1]["signature"]
        # earliest signatures only (we walked newest->oldest; want oldest n)
        sigs.sort(key=lambda s: s.get("blockTime") or 0)
        sigs = sigs[:max(3, n * 4)]
        for i, s in enumerate(sigs[:n]):
            tx = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
            if not tx: continue
            signer = tx.get("transaction", {}).get("message", {}).get("accountKeys", [None])[0]
            if signer and signer != deployer:
                con = db()
                con.execute("INSERT OR REPLACE INTO first_buyers(coin_mint, wallet, rank, ts, size) VALUES(?,?,?,?,?)",
                            (mint, signer, i, s.get("blockTime"), 0))
                con.commit(); con.close()
    except Exception:
        pass

def deployer_offset(deployer):
    """AEGIS: deployer with rugs starts at minus; clean history = small bonus."""
    if not deployer: return 0.0
    con = db()
    row = con.execute("SELECT * FROM deployers WHERE deployer=?", (deployer,)).fetchone()
    if not row: return 0.0
    off = -5.0 * row["rug_count"]
    if row["rug_count"] == 0 and row["coin_count"] >= 2:
        off = +5.0
    return max(-25.0, min(off, 10.0))

# ---------- Behavior index (post-buy strategy) ----------

def tag_actor(actor, actor_class, strategy=None, confidence=0.6, notes=""):
    con = db()
    if actor_class == "rug_deployer":
        strategy = strategy or "flip_fast"
        con.execute("UPDATE deployers SET rug_count=rug_count+1 WHERE deployer=?", (actor,))
    if actor_class == "kol":
        strategy = strategy or "front_run_exit"
    if actor_class == "smart_wallet":
        strategy = strategy or "conviction_hold"
    if actor_class == "whale":
        strategy = strategy or "clip_into_strength"
    con.execute("INSERT OR REPLACE INTO actors(actor, class, confidence, strategy, notes, updated_ts) VALUES(?,?,?,?,?,?)",
                (actor, actor_class, confidence, strategy, notes, time.time()))
    con.commit(); con.close()

def strategy_for(mint):
    """Behavior index -> post-buy strategy for a coin (deployer class first)."""
    con = db()
    coin = con.execute("SELECT deployer FROM coins WHERE mint=?", (mint,)).fetchone()
    if coin and coin["deployer"]:
        row = con.execute("SELECT strategy FROM actors WHERE actor=?", (coin["deployer"],)).fetchone()
        if row: return row["strategy"]
    # First-buyer actors
    rows = con.execute("""SELECT DISTINCT a.strategy FROM first_buyers fb
                          JOIN actors a ON a.actor=fb.wallet WHERE fb.coin_mint=?""", (mint,)).fetchall()
    if rows:
        return rows[0]["strategy"]
    return "default"

# ---------- Tier 1.2: FomoAPI smart-money overlay (auto behavior index) ----------


# ---------- Score ----------

def score_coin(mint, deep=False):
    """Full AEGIS pipeline. Returns report dict; writes DB rows."""
    init_schema()
    if deep:
        cached = deep_cache_get(mint)
        if cached:
            return cached
    a = check_authorities(mint)
    r = check_top10_and_lp(mint)
    m = check_metadata_and_liquidity(mint)

    score = 65.0  # recalibrated base (Oct 18): base 50 + 15 headroom so typical
    # memecoins land 30-70 (research: only <30 is a true veto; 55+ strong). The old
    # base 50 let the stack of independent checks zero most tokens, killing the band.
    verdict = "LIVE"
    flags = []

    # --- CALL-MC DECAY (Oct 20, user: "Top 10 98%, sell pressure, biggest wallet 78%,
    # MC already -89%. But score 72???") ---
    # The scorer used to be BLIND to the call context: coins.mc_at_call (the MC the
    # channel printed when it called the coin) is stored by remember_call_meta, but
    # score_coin never compared current MC against it. SLAB was called at $71,853,
    # scanned hours-later at $11,112 (-84.5%) and STILL scored 72 LIVE. A coin that
    # has already dumped >50% from its call MC is a poisoned call, not a fresh launch.
    try:
        _cc = db().execute("SELECT mc_at_call FROM coins WHERE mint=?", (mint,)).fetchone()
        _decay_ratio = None
        if _cc and _cc[0]:
            _mc_now = (m.get("mc") or 0)
            if _mc_now and float(_cc[0]) > 0:
                _decay_ratio = _mc_now / float(_cc[0])
        if _decay_ratio is not None:
            if _decay_ratio < 0.10:
                score -= 50; flags.append(f"call_mc_decay_{1-_decay_ratio:.0%}")
            elif _decay_ratio < 0.25:
                score -= 35; flags.append(f"call_mc_decay_{1-_decay_ratio:.0%}")
            elif _decay_ratio < 0.50:
                score -= 20; flags.append(f"call_mc_decay_{1-_decay_ratio:.0%}")
            elif _decay_ratio < 0.80:
                score -= 8; flags.append(f"call_mc_decay_{1-_decay_ratio:.0%}")
            elif _decay_ratio >= 1.8:
                # coin already pumped 1.8x past call — P12 entry gate blocks the buy;
                # score reflects it too (chasing risk)
                score -= 10; flags.append(f"call_mc_pumped_{_decay_ratio:.1f}x")
    except Exception:
        pass  # decay bonus never blocks scoring

    # Contract snapshot checks
    if a.get("mint_authority") or r.get("mint_authority"):
        score -= 25; flags.append("mint_authority_present")
    if a.get("freeze_authority") or r.get("freeze_authority"):
        score -= 15; flags.append("freeze_authority_present")
    if r.get("rugged"):
        score -= 40; flags.append("RUGGED_FLAG")
        verdict = "RUGGED"
    if r.get("graph_insiders"):
        # Oct 20 audit (12h live marks, n=149): insider_graph_detected → +17.2pp
        # lift (34.6% win vs 17.4% base); rug_insider_nets_x1 +21pp, x2 +32.6pp.
        # The old -10 static penalty was INVERTED — it penalized the model's best
        # predictors. Static weight now NEUTRAL (0); the learning loop owns this
        # flag's sign from empirical outcomes (learned delta was already +2.2).
        flags.append("insider_graph_detected")
    # Oct 20 audit: rugcheck transferFee is a DICT {'pct': 0, ...} — `if r.get("transfer_fee")`
    # was truthy for ANY dict, so transfer_fee_present fired on every mint even at pct=0
    # (verified 25/25 latest mints pct==0 yet 300/300 scans flagged it, phantom -10 that
    # also blocked the clean_snapshot +15). Normalize to pct and flag only when > 0.
    _tf = r.get("transfer_fee")
    _tf_pct = 0.0
    if isinstance(_tf, dict):
        _tf_pct = float(_tf.get("pct") or 0)
    elif isinstance(_tf, (int, float)):
        _tf_pct = float(_tf or 0)
    if _tf_pct > 0:
        score -= 10; flags.append(f"transfer_fee_present_{_tf_pct:.1f}%")
    if isinstance(r.get("risks"), list):
        # Dedupe penalties by category (audit F6): several "bundle/sniper" rows
        # were each subtracting -15, over-punishing a single underlying issue.
        applied = set()
        for risk in r["risks"]:
            name = (risk.get("name") or "").lower()
            lvl = (risk.get("level") or "").lower()
            if ("bundle" in name or "snip" in name or "insider" in name) and "bundle_or_sniper" not in applied:
                score -= 15; flags.append("bundle_or_sniper_risk"); applied.add("bundle_or_sniper")
            if "liquidity" in name and "lock" in name and "unlocked" in lvl and "lp_unlocked" not in applied:
                score -= 15; flags.append("lp_unlocked"); applied.add("lp_unlocked")
            if "mint" in name and "mint_risk" not in applied:
                score -= 10; flags.append("mint_risk"); applied.add("mint_risk")
            if "freeze" in name and "freeze_risk" not in applied:
                score -= 10; flags.append("freeze_risk"); applied.add("freeze_risk")
            if ("copy" in name or "duplicate" in name) and "copycat" not in applied:
                score -= 10; flags.append("copycat"); applied.add("copycat")
    # P1-3 (Oct 20): raw Rugcheck summary exposes top10 concentration as
    # top10HolderPct (not top10_pct — that key exists only in our derived map).
    # Read the SUMMARY field if the full-report derivation is absent.
    # Oct 20 audit FIX: used to reference `summary` — a LOCAL inside
    # check_top10_and_lp, undefined in score_coin scope → NameError whenever
    # topHolders was empty (top10_pct=None). check_top10_and_lp already merges
    # summary fields into r; read TOP10 from r only.
    t10pct = r.get("top10_pct")
    if not isinstance(t10pct, (int, float)):
        t10pct = r.get("top10HolderPct") or r.get("top10Share") or 0
    try:
        t10pct = float(t10pct or 0)
    except Exception:
        t10pct = 0.0
    if t10pct > 70:
        score -= 10; flags.append("top10_concentration")
    lp = r.get("lp_locked_pct")
    if isinstance(lp, (int, float)) and lp < 50:
        score -= 10; flags.append("lp_partially_locked")

    # --- 7th check: Ave.ai Contract Risk Report (verified free endpoint) ---
    ave = ave_risk(mint) or {}
    if ave:
        if int(ave.get("is_honeypot") or 0) == 1:
            score -= 30; flags.append("AVE_HONEYPOT")
        if int(ave.get("has_mint_method") or 0) == 1:
            score -= 15; flags.append("ave_has_mint_method")
        if str(ave.get("can_take_back_ownership") or "0") == "1":
            score -= 10; flags.append("ave_owner_can_take_back")
        if str(ave.get("anti_whale_modifiable") or "0") == "1":
            score -= 5; flags.append("ave_anti_whale_modifiable")
        if isinstance(ave.get("risk_score"), (int, float)) and ave["risk_score"] >= 80:
            score -= 10; flags.append(f"ave_risk_score_{ave['risk_score']}")

    # --- 8th check: LIVE MOMENTUM from BLIX trade tape (bounded +/-10; replaces pruned ws_momentum) ---
    try:
        from desk_common import blix_coin_trades, blix_alive
        if blix_alive():
            trades = blix_coin_trades(mint, limit=120)
            if trades and len(trades) >= 3:
                buys = sum(float(t.get("usd") or 0) for t in trades if t.get("side") == "buy")
                sells = sum(float(t.get("usd") or 0) for t in trades if t.get("side") == "sell")
                tot = buys + sells
                ratio = buys / tot if tot else 0.5
                if tot >= 500:
                    if ratio >= 0.7:
                        score += 8; flags.append("ws_hot_buys")
                    elif ratio >= 0.55:
                        score += 4; flags.append("ws_buy_pressure")
                    elif ratio >= 0.45:
                        pass  # neutral
                    elif ratio >= 0.3:
                        score -= 6; flags.append("ws_sell_pressure")
                    else:
                        score -= 6; flags.append("ws_dumping")
            # too few trades = dead tape at scan time
            if not trades or len(trades) < 3:
                flags.append("ws_quiet_tape")
    except Exception:
        pass  # WS unavailable — never block the scan on it

    # --- 9th check: DERIVED LIQUIDITY RATIOS (liq/MC, vol/liq) ---
    try:
        m_mc = (m.get("mc") or 0)
        m_liq = (m.get("liq") or 0)
        m_vol = (m.get("vol_h24") or 0)
        if m_mc and m_liq:
            liq_mc = m_liq / m_mc
            if liq_mc < 0.03:
                score -= 15; flags.append(f"thin_pool_liq_mc_{liq_mc*100:.1f}%")
            elif liq_mc < 0.06:
                score -= 8; flags.append(f"low_liq_mc_{liq_mc*100:.1f}%")
            elif liq_mc >= 0.12:
                score += 3; flags.append(f"deep_pool_liq_mc_{liq_mc*100:.1f}%")
        if m_liq and m_vol:
            vol_liq = m_vol / m_liq
            if vol_liq > 40:
                score -= 10; flags.append(f"vol_liq_extreme_{vol_liq:.0f}x_wash_risk")
            elif vol_liq > 15:
                score -= 4; flags.append(f"vol_liq_high_{vol_liq:.0f}x")
    except Exception:
        pass

    # --- 10th check: HOLDER GROWTH (delta vs previous scan of same mint) ---
    try:
        prev = db().execute(
            "select json_extract(checks_json,'$.risks.holders') h, ts from scan_log "
            "where mint=? and json_extract(checks_json,'$.risks.holders') is not null "
            "order by ts desc limit 1", (mint,)).fetchone()
        cur_h = r.get("holders")
        if prev and cur_h and prev["h"]:
            hours = (time.time() - prev["ts"]) / 3600
            if hours > 0.1:
                growth = (cur_h - prev["h"]) / prev["h"] / hours * 100  # % per hour
                growth = max(min(growth, 200), -100)  # clamp: baseline rescans distort
                if growth > 25:
                    score += 5; flags.append(f"holder_growth_{growth:.0f}%_per_h")
                elif growth < -10:
                    score -= 6; flags.append(f"holder_shrinking_{growth:.0f}%_per_h")
    except Exception:
        pass

    # --- 11th check REMOVED (Oct 21): FomoAPI 402-dead; smart-money overlay
    # unable to produce signal. Replaced by fomo-live cohort when wired. ---

    # --- 12th check: SLIPPAGE AT DESK SIZE ($20) via Jupiter ---
    try:
        from jupiter_estimator import quote_impact
        imp = quote_impact(mint, usd=20)
        if imp is not None:
            if imp > 0.08:
                score -= 12; flags.append(f"slippage_{imp*100:.1f}%_at_20usd")
            elif imp > 0.04:
                score -= 5; flags.append(f"slippage_{imp*100:.1f}%_at_20usd")
    except Exception:
        pass  # jupiter down / no route — never block

    # --- 13th check: GoPlus Solana security (transfer_hook / non_transferable / trusted_token) ---
    gp = {}
    try:
        gp = goplus_security(mint)
        if gp:
            if gp.get("transfer_hook"):
                score -= 20; flags.append("transfer_hook")
            if gp.get("non_transferable"):
                score -= 15; flags.append("non_transferable")
            if gp.get("trusted_token"):
                score += 10; flags.append("trusted_token")
            if gp.get("mintable") and a.get("mint_authority"):
                score -= 5; flags.append("goplus_mintable")
    except Exception:
        pass  # goplus down — never block

    # --- 14th check: BLIX holder-quality cross-check (Oct 18 — local radar, guarded) ---
    # dev/bundle tags + top-10 concentration from the live holder ledger.
    try:
        from desk_common import blix_holders, blix_alive
        if blix_alive():
            hd = blix_holders(mint, top=15)
            if hd:
                t10 = hd.get("top10") or hd.get("top10Share") or 0
                # blix top10 is a PERCENTAGE (27.16 = 27.16%), not a fraction
                t10_pct = t10 if t10 > 1 else t10 * 100
                if t10_pct > 70:
                    score -= 10; flags.append(f"blix_top10_{t10_pct:.0f}%")
                dev_w = hd.get("dev")
                if isinstance(dev_w, dict):
                    dev_pct = dev_w.get("pct") or 0
                    if dev_pct > 5:
                        score -= 8; flags.append(f"blix_dev_holds_{dev_pct:.1f}%")
                    elif dev_pct <= 0.5:
                        score += 4; flags.append("blix_dev_flat")
                snipers = hd.get("snipers")
                if isinstance(snipers, (int, float)) and snipers > 3:
                    score -= 6; flags.append(f"blix_snipers_x{snipers}")
                elif isinstance(snipers, list) and len(snipers) > 3:
                    score -= 6; flags.append(f"blix_snipers_x{len(snipers)}")
    except Exception:
        pass  # blix down — never block

    # --- 15th check: AVE top-100 holder quality (Oct 18 — keyed, per-holder PnL/funding) ---
    try:
        from desk_common import ave_top100_holders
        th = ave_top100_holders(mint)
        if th and isinstance(th, list) and len(th) >= 5:
            n = len(th)
            # top-1 concentration
            top1 = th[0].get("balance_ratio") or 0
            if top1 > 0.5:
                score -= 6; flags.append(f"ave_top1_{top1*100:.0f}%")  # recalib: -12->-6 (whale-held is common in memecoins)
            # count holders with realized profit taken on this mint (smart exits visible)
            sold_profit = sum(1 for h in th if (h.get("realized_profit") or 0) > 0)
            if sold_profit >= max(3, n // 3):
                score -= 6; flags.append(f"ave_smart_exits_x{sold_profit}")
            # avg holder age / wallet age signal
            young = sum(1 for h in th if h.get("age") and (h.get("age") or "")[:7] >= "2026-10-01")
            if young > n // 2:
                flags.append("ave_wallets_young")
    except Exception:
        pass  # ave down — never block

    # --- 16th check: Rugcheck insider networks (free; net_id persists across tokens) ---
    try:
        from desk_common import rugcheck_insiders, rugcheck_lockers
        ins = rugcheck_insiders(mint)
        if ins and isinstance(ins, list):
            nets = len(ins)
            if nets > 0:
                # Oct 20 audit: NET ZERO weights (was -5/net). Live marks showed
                # rug_insider_nets_x1 +21pp / x2 +32.6pp lift — penalizing them
                # was inverted. Learning loop owns the sign going forward.
                flags.append(f"rug_insider_nets_x{nets}")
                # transfer-type networks = involved wallets; trade-type = bought-in insiders
                trade_nets = sum(1 for n in ins if n.get("network_type") == "trade")
                if trade_nets > 0:
                    flags.append(f"rug_trade_insiders_x{trade_nets}")
    except Exception:
        pass  # rugcheck down — never block

    # --- 17th check: LIVE MICROSTRUCTURE (CVD, buy concentration) from GeckoTerminal tape ---
    # Recalibration Oct 18: the old AEGIS was contract-heavy (mint/freeze/lp/rug).
    # It ignored the LIVE quality metrics the desk already computes. This overlay
    # adds bounded market-microstructure scoring so real buyers/flow move the score.
    try:
        from bot.intelligence.microstructure import microstructure
        micro = microstructure(mint)
        if micro:
            cvd = micro.get("cvd")
            n_buys = micro.get("n_buys") or 0
            n_sells = micro.get("n_sells") or 0
            bc = micro.get("buy_concentration")
            if cvd is not None:
                if cvd > 0.4:
                    score += 6; flags.append(f"micro_cvd_pos_{cvd:+.2f}")
                elif cvd < -0.4:
                    score -= 8; flags.append(f"micro_cvd_neg_{cvd:+.2f}")
            if bc is not None and bc >= 0.6:
                score -= 6; flags.append(f"micro_buy_conc_{bc:.0%}")
            # tx ratio is a SECONDARY read of the same tape — only apply when CVD
            # gave no signal, to avoid double-counting one snapshot (audit Oct 18)
            if cvd is None or -0.4 <= (cvd or 0) <= 0.4:
                if n_buys and n_sells and (n_buys + n_sells) >= 5:
                    ratio = n_buys / (n_buys + n_sells)
                    if ratio >= 0.7:
                        score += 4; flags.append(f"micro_tx_ratio_{ratio:.2f}")
                    elif ratio <= 0.3:
                        score -= 4; flags.append(f"micro_tx_ratio_{ratio:.2f}")
    except Exception:
        pass

    # --- 18th check: HOLDER CHURN / RETENTION (who's still holding vs leaving) ---
    try:
        churn = holder_churn(mint)
        if churn:
            ret = churn.get("retention")
            churned = churn.get("churned") or 0
            added = churn.get("holders_added") or 0
            if ret is not None:
                if ret < 0.60:
                    score -= 8; flags.append(f"churn_retention_low_{ret:.0%}")
                elif ret > 0.85:
                    score += 5; flags.append(f"churn_retention_high_{ret:.0%}")
            # renewal/loss is a SECONDARY read (same snapshot) — only apply when
            # retention gave no signal, to avoid double-counting (audit Oct 18)
            if ret is None or 0.60 <= (ret or 0) <= 0.85:
                if churned and churned > 0 and added and added > churned * 2:
                    score += 4; flags.append(f"churn_renewal_{added}_new_vs_{churned}_out")
                elif churned and churned > 0 and added is not None and added < churned * 0.5:
                    score -= 4; flags.append(f"churn_loss_{churned}_out_{added}_in")
    except Exception:
        pass

    # --- 19th check: FRESH-WALLET PURCHASING POWER (new money coming in) ---
    try:
        power = fresh_wallet_power(mint)
        if power:
            nb = power.get("new_buyer_count") or 0
            sum_usd = power.get("sum_buy_usd") or 0
            if nb >= 5 and sum_usd >= 50:
                score += 6; flags.append(f"fresh_wallet_power_{nb}_wallets_${sum_usd:.0f}")
            elif nb >= 2 and sum_usd >= 20:
                score += 3; flags.append(f"fresh_wallet_power_{nb}_wallets_${sum_usd:.0f}")
            elif nb == 0:
                score -= 4; flags.append("fresh_wallet_power_none")
    except Exception:
        pass

    # --- 20th check: FILL CLASS + FRAGILITY from the signal engine (flip speed) ---
    try:
        intel = token_intel(mint)
        if intel:
            fill = intel.get("fill_class")
            frag = (intel.get("fragility") or {}).get("fragility") or 0
            if fill == "bots":
                score -= 6; flags.append("fill_bots_fast_flip")
            elif fill in ("people", "very_slow"):
                score += 4; flags.append(f"fill_{fill}_conviction")
            if frag > 70:
                score -= 5; flags.append(f"fragility_{frag}")
    except Exception:
        pass

    # --- 21st check: COHORT / SMART-MONEY CONSENSUS (solanatracker leaderboard entity) ---
    try:
        from bot.intelligence.cohort import cohort_signal_for
        cs = cohort_signal_for(mint)
        if cs:
            sig = cs.get("signal")
            if sig == "ACCUMULATION":
                score += 7; flags.append("cohort_accumulation")
            elif sig == "DISTRIBUTION":
                score -= 9; flags.append("cohort_distribution")
    except Exception:
        pass

    # --- 22nd check: GMGN RANK DISCOVERY CONTEXT (free, live — replaces 402-locked Bitquery) ---
    try:
        from desk_common import gmgn_rank
        rows = gmgn_rank("1h", "volume", 50) or []
        for row in rows:
            if isinstance(row, dict) and (row.get("address") == mint or row.get("mint") == mint):
                sym = row.get("symbol") or ""
                # GMGN rank fields: sniper_count, bundler_rate, top_10_holder_rate,
                # rug_ratio, is_wash_trading, buy_tax, sell_tax, creator_close
                snipers = int(row.get("sniper_count") or 0)
                bundler = float(row.get("bundler_rate") or 0)
                wash = bool(row.get("is_wash_trading"))
                rug = float(row.get("rug_ratio") or 0)
                t10 = float(row.get("top_10_holder_rate") or 0)
                if snipers > 15:
                    score -= 5; flags.append(f"gmgn_snipers_{snipers}")
                if bundler > 0.3:
                    score -= 5; flags.append(f"gmgn_bundler_{bundler*100:.0f}%")
                if wash:
                    score -= 6; flags.append("gmgn_wash_flag")
                if rug > 0.3:
                    score -= 4; flags.append(f"gmgn_rug_ratio_{rug*100:.0f}%")
                if t10 > 70:
                    score -= 4; flags.append(f"gmgn_t10_{t10*100:.0f}%")
                # presence in top-50 by 1h volume = live demand context (+small)
                score += 3; flags.append("gmgn_top50_1h_vol")
                break
    except Exception:
        pass  # gmgn down/blocked — never block

    # Second brain: deployer + first buyers
    deployer = None
    dts = None
    if deep:
        deployer = r.get("creator")  # stable source: Rugcheck report creator
        if not deployer:
            deployer, dts, _ = resolve_deployer(mint)
        if deployer:
            remember_deployer(deployer, mint, dts)
            # --- BLIX dev-board enrichment (7.5, Oct 18 — guarded local radar) ---
            try:
                from desk_common import blix_devs, blix_pump_board, blix_alive
                if blix_alive():
                    devs = blix_devs(limit=100)
                    for drow in devs:
                        if (drow.get("wallet") or "") == deployer:
                            flags.append(f"blix_dev_board_rank{drow.get('rank')}")
                            if drow.get("launches"):
                                flags.append(f"blix_dev_launches_{drow.get('launches')}")
                            break
                    board = blix_pump_board(range_="24h", limit=50)
                    for brow in board:
                        if (brow.get("wallet") or "") == deployer:
                            flags.append("blix_sniper_board_appears")
                            break
            except Exception:
                pass  # blix down — never block
            # --- GoPlus address_security on the deployer (Oct 18 — free, wire vetting) ---
            try:
                from desk_common import goplus_address_security
                gpa = goplus_address_security(deployer)
                if gpa:
                    if gpa.get("risk") == "RISK":
                        score -= 12; flags.append("deployer_goplus_risk")
                    if gpa.get("honeypot_related"):
                        score -= 12; flags.append("deployer_honeypot_related")
                    if gpa.get("phishing"):
                        score -= 10; flags.append("deployer_phishing")
                    if gpa.get("sanctioned"):
                        score -= 15; flags.append("deployer_sanctioned")
                    if gpa.get("malicious_contracts"):
                        score -= min(12, 3 * int(gpa["malicious_contracts"])); flags.append(f"deployer_malicious_x{gpa['malicious_contracts']}")
            except Exception:
                pass  # goplus down — never block
            # --- WalletIntel deployer quality (Oct 18 — free, 10/day budgeted) ---
            # Historical win-rate / strategy / score of the deployer wallet → feeds deployer
            # memory (a "serial pumpfun flipper" deployer is a different risk than a "diamond
            # hands" veteran). Budgeted via credit_budget (walletintel 10/day).
            # Oct 19 audit: budget gate was COMMENTED but never called — every deep scan
            # burned one of the 10 daily requests AND ~14s (endpoint does heavy compute).
            # Now: budget() gate + 24h per-deployer cache so repeated scans are free.
            try:
                from desk_common import budget, walletintel_summary, ttl_cache_get, ttl_cache_put
                if budget("walletintel"):
                    wi_key = f"wi_dep_{deployer}"
                    wi = ttl_cache_get(wi_key, 86400)
                    if wi is None:
                        wi = walletintel_summary(deployer)
                        if wi:
                            ttl_cache_put(wi_key, wi)
                if wi and isinstance(wi, dict) and wi.get("score") is not None:
                    wi_score = float(wi.get("score") or 0)
                    wi_strategy = (wi.get("strategy") or "").lower()
                    wi_wr = float(wi.get("win_rate") or 0)
                    flags.append(f"deployer_wi_score_{wi_score:.0f}_wr_{wi_wr:.0f}")
                    if wi_score >= 70:
                        score += 5; flags.append("deployer_wi_strong")
                    elif wi_score < 25:
                        score -= 6; flags.append("deployer_wi_weak")
                    if wi_strategy in ("sniper", "scalper", "degen", "mixed"):
                        # serial quick-flipper: tight flips, faster exits
                        score -= 3; flags.append(f"deployer_wi_{wi_strategy}")
                    elif "diamond" in wi_strategy:
                        score += 4; flags.append("deployer_wi_diamond_hands")
            except Exception:
                pass  # walletintel / budget exhausted — never block
            # Remember deployer's OTHER coins (creatorTokens) — pattern across launches
            other = r.get("creator_tokens") or []
            if other:
                con = db()
                row = con.execute("SELECT wallets_json FROM deployers WHERE deployer=?", (deployer,)).fetchone()
                try: known = json.loads(row["wallets_json"]) if row else []
                except Exception: known = []
                known = list(dict.fromkeys(known + [t if isinstance(t, str) else t.get("mint") for t in other]))[:50]
                con.execute("UPDATE deployers SET wallets_json=? WHERE deployer=?", (json.dumps(known), deployer))
                con.commit(); con.close()
                flags.append(f"deployer_other_coins({len(other)})")
            record_first_buyers(mint, deployer)
            off = deployer_offset(deployer)
            score += off
            if off < 0: flags.append(f"dirty_deployer_history({off:+.0f})")
            con = db()
            con.execute("UPDATE coins SET deployer=? WHERE mint=?", (deployer, mint))
            con.commit(); con.close()
            fbs = db().execute("SELECT wallet FROM first_buyers WHERE coin_mint=? LIMIT 6", (mint,)).fetchall()
            known = [fb["wallet"] for fb in fbs]
            if known:
                actors = db().execute("SELECT actor, class FROM actors WHERE actor IN (%s)" % ",".join("?"*len(known)), known).fetchall()
                for act in actors:
                    if act["class"] in ("rug_deployer",):
                        score -= 10; flags.append("known_rugger_in_first_buyers")

    # --- 18th check: ENTITY-LABEL enrichment (self-sourced: funder graph + bot behavior) ---
    # annotate_wallet_risk(): -10 exchange first-buyer, -15 known-rug entity, -5 MEV/bot.
    # Self tier only needs Helius + our own deployers/first_buyers memory (no external keys).
    try:
        from aegis_entity_labels import annotate_wallet_risk
        fbs2 = db().execute("SELECT wallet FROM first_buyers WHERE coin_mint=? LIMIT 6", (mint,)).fetchall()
        wl = [fb["wallet"] for fb in fbs2]
        if wl:
            stats = {w: {"role": "first_buyer"} for w in wl}
            adj, notes, _ = annotate_wallet_risk(stats)
            if adj:
                score += adj  # adj is negative or 0
                flags.append(f"entity_labels({adj:+d})")
                for nt in notes:
                    flags.append(nt)  # e.g. "Wallet… is Binance (exchange) — supply distribution risk"
            # Oct 20 PIPI/SHLRT lesson (user: "PIPI was given 70 then immediately dumped"):
            # first-block SNIPER-BOT wallets in the launch's first buyers are the single
            # best dump predictor (12h audit: first-block snipers -> 0% win, n=7-22), yet
            # each one costs only -5 via entity_labels and the LEARNING overrides are keyed
            # by exact wallet string so NEW sniper wallets carry ZERO learned weight.
            # Add a CATEGORICAL penalty: N distinct sniper wallets in first buyers.
            try:
                from aegis_entity_labels import behavioral_labels
                n_snipers = 0
                for w in wl:
                    lbl, cat = behavioral_labels(w)
                    if cat == "mev":
                        n_snipers += 1
                # Oct 20 24h audit: even ONE first-block sniper kills — x1 snipers
                # (E7CD4J 87% dead, E6cZWf 87%, 5k2Ckk 85%) were slipping through
                # because the old penalty started at x2. Ladder: 1->10, 2->15,
                # 3->25, 4+->30 (bounded).
                if n_snipers >= 1:
                    pen = 10 if n_snipers == 1 else min(15 + 10 * (n_snipers - 2), 30)
                    score -= pen
                    flags.append(f"first_block_sniper_wallets_x{n_snipers} (-{pen})")
            except Exception:
                pass
    except Exception:
        pass  # enrichment down — never block
    # AEGIS is now interpretable: 4 category sub-scores + 2 new live checks + dedupe.
    CAT = {
        "security":  ("mint_", "freeze_", "rugged", "bundle", "insider", "transfer_fee", "lp_",
                      "top10", "AVE_", "goplus", "deployer_", "known_rugger", "copycat", "slippage",
                      "ave_smart_exits", "ave_top1", "entity_labels"),
        "tape":      ("ws_", "vol_liq", "thin_pool", "low_liq", "blix_top10", "blix_dev",
                      "blix_snipers", "micro_", "fill_", "fragility", "wash", "slow-", "bitquery_",
                      "blix_sol"),
        "distribution": ("holder_", "retention", "churn", "fresh_wallet", "ave_wallets",
                         "whale_holds"),
        "ecosystem": ("smart_money", "cohort_", "fomo_", "rug_insider_nets", "rug_trade_insiders",
                      "blix_dev_board", "blix_sniper_board", "deep_pool", "clean_snapshot",
                      "blix_narrative"),
    }

    # 23rd check: SOL.WALES WHALE INTERSECTION (free, live — the missing whale layer).
    # If any top-10 holder (Rugcheck or Ave) is a tracked sol.wales whale, that whale's
    # activity is directly material to this mint.
    try:
        from desk_common import sol_wales_traders
        rc_wallets = set()
        for h in (r.get("topHolders") or [])[:10]:
            w = h.get("wallet") if isinstance(h, dict) else h
            if w: rc_wallets.add(w)
        if isinstance(th, list):
            for h in th[:10]:
                if isinstance(h, dict) and h.get("holder"):
                    rc_wallets.add(h["holder"])
                elif isinstance(h, dict) and h.get("address"):
                    rc_wallets.add(h["address"])
        if rc_wallets:
            whale_set = {w.get("address") for w in (sol_wales_traders(limit=200) or []) if isinstance(w, dict)}
            inter = rc_wallets & whale_set
            if inter:
                n = len(inter)
                score -= 8 * min(n, 3); flags.append(f"whale_holds_top10_x{n}")
    except Exception:
        pass  # sol.wales down — never block

    # 24th check: BLIX NARRATIVE HEAT for this symbol (free, live — confluence bonus)
    try:
        from desk_common import blix_narratives, blix_alive
        sym = (m.get("symbol") or "").upper()
        if not sym:
            # metadata lookup failed (common for fresh mints) — fall back to DB symbol
            try:
                row = db().execute("SELECT symbol FROM coins WHERE mint=?", (mint,)).fetchone()
                if row and row["symbol"]:
                    sym = str(row["symbol"]).upper()
            except Exception:
                pass
        if sym and blix_alive():
            for t in (blix_narratives() or [])[:8]:
                name = (t.get("name") or "").lower()
                top = t.get("top") or []
                if sym.lower() in name or any(sym.lower() in (c.get("symbol") or "").lower() for c in top[:6]):
                    heat = float(t.get("heat") or 0)
                    if heat >= 70:
                        score += 6; flags.append(f"blix_narrative_heat_{heat:.0f}")
                    elif heat >= 55:
                        score += 3; flags.append(f"blix_narrative_heat_{heat:.0f}")
                    else:
                        flags.append("blix_narrative_cold")
                    break
    except Exception:
        pass

    # Recalibration #1: dedupe top-10 concentration penalty (Rugcheck + Blix + Ave each
    # may have charged it) — keep ONE concentration penalty, refund the duplicates.
    conc_flags = [f for f in flags if f.startswith(("top10", "blix_top10", "ave_top1"))]
    if len(conc_flags) > 1:
        nominal = {"top10_concentration": 10, "blix_top10": 10, "ave_top1": 12}
        refund = 0.0
        for f in conc_flags[1:]:
            for key, val in nominal.items():
                if f.startswith(key):
                    refund += val
                    break
        score += refund
        # collapse to one clear flag (keep the largest source)
        keep = max(conc_flags, key=lambda f: next((v for k, v in nominal.items() if f.startswith(k)), 0))
        flags = [f for f in flags if f not in conc_flags] + [keep]

    # Recalibration #2: category sub-scores (signal counts per layer) for interpretability.
    cat_bad = {k: sum(1 for f in flags if any(f.startswith(p) for p in CAT[k])
                      and not f.startswith(("blix_narrative_heat", "smart_money_", "deep_pool", "clean_snapshot")))
               for k in CAT}
    cat_good = {k: sum(1 for f in flags if any(f.startswith(p) for p in CAT[k])
                       and f.startswith(("blix_narrative_heat", "smart_money_", "deep_pool", "clean_snapshot")))
                for k in CAT}

    score = max(0.0, min(100.0, score))
                        # Clean token bonus: no NEGATIVE flags + LP locked => genuinely clean snapshot.
                        # Positive/neutral flags (smart_money, deep_pool_liq, deployer_other_coins,
                        # clean_snapshot itself) must NOT block the bonus (audit F6).
    _NEG_PREFIXES = ("transfer_fee", "bundle", "lp_", "mint_", "freeze_",
                     "copycat", "top10", "AVE_", "dirty", "rugger", "wash",
                     "slow", "fragility", "vol_liq", "slippage", "RUGGED", "drained",
                     "lp_unlocked", "mint_risk", "freeze_risk", "micro_cvd_neg",
                     "micro_buy_conc", "churn_retention_low", "churn_loss",
                     "fresh_wallet_power_none", "fill_bots", "cohort_distribution",
                     "bitquery_sell_imbalance", "ws_sell_pressure", "thin_pool_liq_mc",
                     "low_liq_mc", "gmgn_snipers", "gmgn_bundler", "gmgn_wash",
                     "gmgn_rug_ratio", "gmgn_t10", "entity_labels", "ave_smart_exits",
                     "ave_top1", "blix_top10",
                     "blix_dev_", "blix_snipers", "known_rugger", "deep_pool_risk",
                     "first_block_sniper", "call_mc_decay", "holder_dump", "whale_holds_dump", "fomo_")
    _neg = [f for f in flags if any(f.startswith(p) for p in _NEG_PREFIXES)]
    if not _neg and verdict != "RUGGED" and isinstance(r.get("lp_locked_pct"), (int, float)) and r["lp_locked_pct"] >= 90:
        score += 15
        flags.append("clean_snapshot(+15)")
    score = max(0.0, min(100.0, score))
    # --- LEARNING LOOP (Oct 19): apply empirical weight overrides from
    # aegis_weight_overrides.json (generated by aegis_learn.py, bounded ±6/flag).
    # Cached 5 min; absent/garbage file = no change. Re-clamped after.
    try:
        import time as _t
        _ov_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "aegis_weight_overrides.json")
        _now_t = _t.time()
        if _LRN.get("ts", 0) < _now_t - 300:
            with open(_ov_path) as _f:
                _LRN["data"] = json.load(_f).get("deltas", {})
                _LRN["ts"] = _now_t
        for _f in set(flags):
            _d = _LRN.get("data", {}).get(_f)
            if isinstance(_d, (int, float)):
                score += float(_d)
                flags.append(f"learned_{_f.replace(' ', '_')}({_d:+.1f})")
    except Exception:
        pass  # learning layer never blocks scoring
    score = max(0.0, min(100.0, score))
    if verdict != "RUGGED":
        if score < 40: verdict = "RISK"
        elif score < 60: verdict = "CAUTION"
        else: verdict = "LIVE"

    strategy = strategy_for(mint) if deep else "default"
    # USER MANDATE (Oct 20): EVERY scan is a deep scan. Audit-proof marker so
    # scan_log flags_json carries the depth used — a scan_log row with deep_scan
    # ran the full pipeline (deployer memory, first buyers, WalletIntel, live
    # tape checks 8-24); a non-deep call (only the CLI --shallow debug probe)
    # is self-evident by its absence. Also recorded in log_event.
    if deep:
        flags.append("deep_scan")
    if m.get("symbol"):
        con = db()
        con.execute("""INSERT INTO coins(mint, symbol, mc_at_call, liq_at_call, deployer, score, verdict, strategy, updated_ts)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(mint) DO UPDATE SET
                         symbol=excluded.symbol,
                         mc_at_call=COALESCE(coins.mc_at_call, excluded.mc_at_call),
                         liq_at_call=COALESCE(coins.liq_at_call, excluded.liq_at_call),
                         deployer=COALESCE(coins.deployer, excluded.deployer),
                         score=excluded.score, verdict=excluded.verdict,
                         strategy=excluded.strategy, updated_ts=excluded.updated_ts""",
                    (mint, m.get("symbol"), m.get("mc"), m.get("liq"), deployer, score, verdict, strategy, time.time()))
        con.execute("INSERT INTO scan_log(mint, ts, score, verdict, strategy, checks_json, flags_json) VALUES(?,?,?,?,?,?,?)",
                    (mint, time.time(), score, verdict, strategy, json.dumps({"authorities": a, "risks": r, "market": {k: m.get(k) for k in ("mc","liq","vol_h24","buys24","sells24")}, "ave": ave, "goplus": gp}), json.dumps(flags)))
        con.commit(); con.close()
    log_event({"event": "AEGIS_SCAN", "mint": mint, "score": score, "verdict": verdict,
               "strategy": strategy, "flags": flags, "deployer": deployer})

    report = {"mint": mint, "symbol": m.get("symbol"), "score": round(score, 1), "verdict": verdict,
              "strategy": strategy, "flags": flags, "deployer": deployer,
              "mc": m.get("mc"), "liq": m.get("liq"), "price": m.get("price_usd"),
              "sub_scores": {"bad": cat_bad, "good": cat_good},
              "checks": {"authorities": a, "risks": r, "market": m, "ave": ave}}
    if deep:
        deep_cache_put(mint, report)
    return report

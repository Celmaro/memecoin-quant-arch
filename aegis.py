#!/usr/bin/env python3
"""AEGIS-lite: scam filter with a second brain (SQLite memory across launches).

Six checks (mint/freeze/top-10/bundle/LP/metadata) + deployer & first-buyer memory.
Score 0-100; deployer history adjusts ±25; clean contract from dirty wallet = distrust.
Also hosts the behavior index (actors -> post-buy strategy).
"""
import json, time, re
from desk_common import (db, init_schema, dex_pair, rugcheck, rpc, log_event, helius_txs,
                         helius_asc_txs, mother_wallet, ave_risk, fomo_holders, fomo_user, fomo_board,
                         goplus_security)

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
    """
    try:
        sigs = []
        before = None
        for _ in range(40):  # max 40 pages back
            params = [mint, {"limit": 100}]
            if before:
                params[1]["before"] = before
            page = rpc("getSignaturesForAddress", params) or []
            if not page:
                break
            sigs.extend(page)
            if len(page) < 100:
                break
            before = page[-1]["signature"]
        # oldest first: we walked newest->oldest, so reverse, take first n
        sigs.sort(key=lambda s: s.get("blockTime") or 0)
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

def fomo_overlay(mint, chain="solana", max_resolve=3):
    """Smart-money overlay at intake: FomoAPI holders -> trader track records -> actor tags.
    Tags smart_wallet (verified, positive PnL), kol (high followers), and links their solana
    wallets to the actors table so strategy_for() can trigger conviction_hold / front_run_exit.
    Falls back to the working token boards (fomo_board) when the holders endpoint is down.
    Returns {'holders': [...], 'tagged': [...], 'smart_money_count': N, 'board': {...} or None}."""
    holders = fomo_holders(mint, chain=chain)
    board = None
    if not holders:
        # holders endpoint is upstream-flaky (503s); use board context as fallback
        board = fomo_board(mint, chain=chain)
    tagged = []
    sm_count = 0
    # Read-only lookups on a short-lived connection; never hold a write txn
    # across slow network calls (that locks out other desk processes).
    con = db()
    try:
        cached = {}
        for h in [x.get("handle") for x in holders if x.get("handle")]:
            row = con.execute("SELECT class, strategy FROM actors WHERE actor=?", (h,)).fetchone()
            if row:
                cached[h] = (row["class"], row["strategy"])
    finally:
        con.close()
    resolved = 0
    for h in holders[:max_resolve + 4]:
        handle = h.get("handle")
        if not handle:
            continue
        # cache check: already an actor with a class? (prefetched above, no open conn)
        cls_cached = cached.get(handle)
        if cls_cached and cls_cached[0] in ("smart_wallet", "kol", "rug_deployer"):
            if cls_cached[0] == "smart_wallet": sm_count += 1
            tagged.append({"handle": handle, "class": cls_cached[0]})
            continue
        if resolved >= max_resolve:
            continue
        u = fomo_user(handle)
        resolved += 1
        if not u:
            continue
        pnl = u.get("pnl") or {}
        verified = bool(u.get("verified"))
        followers = int(u.get("followers") or 0)
        trades = int(u.get("trades") or 0)
        pnl_all = float((pnl.get("all") if isinstance(pnl, dict) else 0) or 0)
        pnl_7d = float((pnl.get("7d") if isinstance(pnl, dict) else 0) or 0)
        if verified and trades >= 3 and (pnl_7d > 0 or pnl_all > 0):
            cls, strat, conf = "smart_wallet", "conviction_hold", 0.8
            sm_count += 1
        elif followers >= 5000 or (pnl_all > 100_000 and trades > 0):
            cls, strat, conf = "kol", "front_run_exit", 0.6
        else:
            cls, strat, conf = "trader", "default", 0.4
        tag_actor(handle, cls, strategy=strat, confidence=conf,
                  notes=f"fomo overlay pnl7d={pnl_7d:.0f} pnlAll={pnl_all:.0f} followers={followers} verified={verified}")
        wallets = u.get("wallets") or {}
        if wallets.get("solana"):
            # short-lived write conn per row (no held txn across the loop)
            wcon = db()
            try:
                wcon.execute("INSERT OR REPLACE INTO actors(actor, class, confidence, strategy, notes, updated_ts) VALUES(?,?,?,?,?,?)",
                             (wallets["solana"], cls, conf, strat, f"fomo wallet for {handle}", time.time()))
                wcon.execute("INSERT OR IGNORE INTO first_buyers(coin_mint, wallet, rank, ts, size) VALUES(?,?,?,?,?)",
                             (mint, wallets["solana"], 100 + len(tagged), time.time(), 0))
                wcon.commit()
            finally:
                wcon.close()
        tagged.append({"handle": handle, "class": cls, "pnl_7d": round(pnl_7d), "followers": followers})
    return {"holders": holders, "tagged": tagged, "smart_money_count": sm_count, "board": board}

# ---------- Score ----------

def score_coin(mint, deep=False):
    """Full AEGIS pipeline. Returns report dict; writes DB rows."""
    init_schema()
    a = check_authorities(mint)
    r = check_top10_and_lp(mint)
    m = check_metadata_and_liquidity(mint)

    score = 50.0
    verdict = "LIVE"
    flags = []

    # Contract snapshot checks
    if a.get("mint_authority") or r.get("mint_authority"):
        score -= 25; flags.append("mint_authority_present")
    if a.get("freeze_authority") or r.get("freeze_authority"):
        score -= 15; flags.append("freeze_authority_present")
    if r.get("rugged"):
        score -= 40; flags.append("RUGGED_FLAG")
        verdict = "RUGGED"
    if r.get("graph_insiders"):
        score -= 15; flags.append("insider_graph_detected")
    if r.get("transfer_fee"):
        score -= 10; flags.append("transfer_fee_present")
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
    if isinstance(r.get("top10_pct"), (int, float)) and r["top10_pct"] > 70:
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

    # --- 8th check: LIVE MOMENTUM from PumpDev WS tape (bounded +/-10) ---
    try:
        from ws_momentum import sample_trades
        trades, _err = sample_trades(mint, seconds=12, max_trades=120)
        if trades and len(trades) >= 3:
            buys = sum(t["usd"] for t in trades if t["side"] == "buy")
            sells = sum(t["usd"] for t in trades if t["side"] == "sell")
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
                    score -= 10; flags.append("ws_dumping")
        # too few trades = dead tape at scan time
        if len(trades) < 3:
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

    # --- 11th check: SMART MONEY overlay (FomoAPI, bounded +/-6) ---
    try:
        ov = fomo_overlay(mint)
        n_sm = (ov or {}).get("smart_money_count") or 0
        if n_sm >= 3:
            score += 6; flags.append(f"smart_money_x{n_sm}")
        elif n_sm == 2:
            score += 3; flags.append("smart_money_x2")
        # smart money tagged but SELLING is handled by behavior index elsewhere
    except Exception:
        pass

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

    # Second brain: deployer + first buyers
    deployer = None
    dts = None
    if deep:
        deployer = r.get("creator")  # stable source: Rugcheck report creator
        if not deployer:
            deployer, dts, _ = resolve_deployer(mint)
        if deployer:
            remember_deployer(deployer, mint, dts)
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
                        score -= 10; flags.append("known_rugger_in_first_buys")

    score = max(0.0, min(100.0, score))
    # Clean token bonus: no NEGATIVE flags + LP locked => genuinely clean snapshot.
    # Positive/neutral flags (smart_money, deep_pool_liq, deployer_other_coins,
    # clean_snapshot itself) must NOT block the bonus (audit F6).
    _NEG_PREFIXES = ("insider", "transfer_fee", "bundle", "lp_", "mint_", "freeze_",
                     "copycat", "top10", "AVE_", "dirty", "rugger", "wash",
                     "slow", "fragility", "vol_liq", "slippage", "RUGGED", "drained",
                     "lp_unlocked", "mint_risk", "freeze_risk")
    _neg = [f for f in flags if any(f.startswith(p) for p in _NEG_PREFIXES)]
    if not _neg and verdict != "RUGGED" and isinstance(r.get("lp_locked_pct"), (int, float)) and r["lp_locked_pct"] >= 90:
        score += 15
        flags.append("clean_snapshot(+15)")
    score = max(0.0, min(100.0, score))
    if verdict != "RUGGED":
        if score < 40: verdict = "RISK"
        elif score < 60: verdict = "CAUTION"
        else: verdict = "LIVE"

    strategy = strategy_for(mint) if deep else "default"
    if m.get("symbol"):
        con = db()
        con.execute("""INSERT OR REPLACE INTO coins(mint, symbol, mc_at_call, liq_at_call, deployer, score, verdict, strategy, updated_ts)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (mint, m.get("symbol"), m.get("mc"), m.get("liq"), deployer, score, verdict, strategy, time.time()))
        con.execute("INSERT INTO scan_log(mint, ts, score, verdict, strategy, checks_json) VALUES(?,?,?,?,?,?)",
                    (mint, time.time(), score, verdict, strategy, json.dumps({"authorities": a, "risks": r, "market": {k: m.get(k) for k in ("mc","liq","vol_h24","buys24","sells24")}, "ave": ave, "goplus": gp})))
        con.commit(); con.close()
    log_event({"event": "AEGIS_SCAN", "mint": mint, "score": score, "verdict": verdict,
               "strategy": strategy, "flags": flags, "deployer": deployer})

    return {"mint": mint, "symbol": m.get("symbol"), "score": round(score, 1), "verdict": verdict,
            "strategy": strategy, "flags": flags, "deployer": deployer,
            "mc": m.get("mc"), "liq": m.get("liq"), "price": m.get("price_usd"),
            "checks": {"authorities": a, "risks": r, "market": m, "ave": ave}}

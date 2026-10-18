#!/usr/bin/env python3
"""Rich forensics DM card builder (Oct 19) — the full narrative format the user wants."""
import json, os, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
sys.path.insert(0, DESK)


def fmt_money(v):
    try:
        v = float(v or 0)
        if v >= 1e6: return f"${v/1e6:.1f}M"
        if v >= 1e3: return f"${v/1e3:.1f}K"
        return f"${v:.0f}"
    except Exception:
        return "$0"


def build_forensics_card(mint, call=None):
    """Build the rich 'Forensics on $SYM (CA) — mixed card' DM card.

    Surface: source channel + call MC (latency delta), AEGIS score + flags with a
    one-line explanation each, live market side (MC change, liq, vol, m5 buy/sell),
    holder distribution (top-10, biggest wallet), authorities, socials, migration
    state, verdict line, and an action plan (quick-flip sizing, stop, clips, whale watch).
    Returns card string or None on scan failure.
    """
    import aegis
    from desk_common import dex_pair, blix_token, blix_alive

    rep = None
    try:
        rep = aegis.score_coin(mint, deep=True)
    except Exception:
        return None
    score = rep.get("score")
    verdict = rep.get("verdict") or "?"
    flags = rep.get("flags") or []
    sym = (rep.get("symbol") or (call or {}).get("symbol") or mint[:6])
    chan = (call or {}).get("channel") or "?"
    call_mc = (call or {}).get("mc")
    sym_ca = f"${sym} ({mint[:6]}…{mint[-4:]})"

    # market via DexScreener
    pair = dex_pair(mint) or {}
    mc = float(pair.get("marketCap") or 0)
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    vol_h1 = float((pair.get("volume") or {}).get("h1") or 0)
    txns = pair.get("txns") or {}
    m5 = txns.get("m5") or {}
    b5, s5 = int(m5.get("buys") or 0), int(m5.get("sells") or 0)
    chg = pair.get("priceChange") or {}
    chg_h1 = float(chg.get("h1") or 0)
    chg_m5 = float(chg.get("m5") or 0)

    L = []
    L.append(f"Forensics on {sym_ca} — mixed card")
    L.append("")
    L.append(f"🛡 AEGIS score: {score}/100 — {verdict} (deep scan). Read the flags before acting:")
    L.append("")
    # flag explanations (curated map; fallback = plain flag)
    FLAG_EXPLAIN = {
        "insider_graph_detected": "deployer/insider wallets cluster in the funding graph — 'clean contract, dirty wallet' pattern",
        "transfer_fee_present": "transfer fee on the contract — tax risk on every sell",
        "lp_partially_locked": "LP only partially locked = ruggable tail risk",
        "mint_authority_present": "mint authority still live = supply can be printed",
        "freeze_authority_present": "freeze authority live = funds can be frozen",
        "top10_concentration": "top-10 holders overly concentrated",
        "RUGGED_FLAG": "Rugcheck flagged rugged",
        "bundle_or_sniper_risk": "bundled/sniped entry cluster detected",
        "deployer_wi_weak": "deployer's historical wallet score is weak",
        "deployer_wi_sniper": "deployer historically snipes/scalps",
        "whale_holds_top10": "a tracked whale holds this token",
    }
    shown = 0
    for f in flags:
        if f.startswith(("clean_snapshot", "deployer_wi_score", "bs_", "goplus_", "ave_")):
            continue
        if shown >= 7:
            L.append(f"- ⚠ {f} … (+{(len(flags)-shown)} more)")
            break
        expl = next((v for k, v in FLAG_EXPLAIN.items() if f.startswith(k)), "")
        if expl:
            L.append(f"- ⚠ {f} — {expl}")
        else:
            L.append(f"- ⚠ {f}")
        shown += 1

    L.append("")
    L.append("But the market side:")
    call_note = ""
    if call_mc:
        mv = (mc / call_mc - 1) * 100 if call_mc else 0
        call_note = f" (vs ${call_mc:,.0f} call MC — {'lag up' if mv > 0 else 'lag down'} {mv:+.1f}%)"
    L.append(f"- MC {fmt_money(mc)} and {'moving' if abs(chg_h1) > 5 else 'flat'} ({chg_h1:+.1f}% 1h){call_note}")
    L.append(f"- Liq {fmt_money(liq)}, vol {fmt_money(vol_h1)} last hour, m5 buys {b5} vs sells {s5} — {'active inflow' if b5 > s5 else 'sell pressure'}")
    # holder distribution from rugcheck topHolders
    try:
        from desk_common import rugcheck
        rc = rugcheck(mint, full=True) or {}
        top = rc.get("topHolders") or []
        if top:
            pcts = [float(t.get("pct") or 0) for t in top[:10]]
            t10 = sum(pcts[:10])
            biggest = max(pcts[:10]) if pcts else 0
            L.append(f"- Holders: top-10 {t10:.1f}%, biggest wallet {biggest:.1f}% — "
                     f"{'healthy distribution, not whale-held' if t10 < 50 else 'concentrated'}")
    except Exception:
        pass
    # authorities
    try:
        from aegis import check_authorities
        a = check_authorities(mint)
        L.append(f"- Mint/freeze authority: {('revoked' if not (a.get('mint_authority') or a.get('freeze_authority')) else 'PRESENT — risk')}")
    except Exception:
        pass
    # socials + migration from blix
    try:
        if blix_alive():
            t = blix_token(mint) or {}
            links = t.get("links") or []
            tags = []
            for l in links:
                u = (l.get("url") or "").lower()
                if "x.com" in u or "twitter" in u: tags.append("X")
                elif "t.me" in u: tags.append("TG")
                elif "tiktok" in u: tags.append("TikTok")
            if tags:
                L.append(f"- Socials: {' + '.join(sorted(set(tags)))} live")
            if t.get("migrated_at"):
                L.append("- Migrated to PumpSwap ~fresh, price discovery in progress")
    except Exception:
        pass

    L.append("")
    L.append(f"Verdict: {'live momentum, structurally imperfect' if score < 40 else 'moderate'}. "
             f"The contradiction: contract checks say {score}/100, but tape/holders may look organic. "
             f"Trust the tape while it works, never trust it overnight.")
    L.append("")
    L.append(f"Source: {chan} · call MC ${call_mc:,.0f}" if call_mc else f"Source: {chan}")
    L.append("💠 CA: " + mint)
    return "\n".join(L)


if __name__ == "__main__":
    print(build_forensics_card(sys.argv[1] if len(sys.argv) > 1 else "AeLonprCt3y4dZEWEeRTkPqnUPW2FngSD2YeUmPP6GDk"))
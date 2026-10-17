#!/usr/bin/env python3
"""desk.py — Memecoin Quant Desk orchestrator.

Commands:
  crawl                 Poll Telegram channels, AEGIS-scan new calls
  scan <mint> [--deep]  Full AEGIS scan (+ deployer memory when deep)
  intel <mint>          Signal engine: slow-grad / wash / fragility
  desk.py buy <mint> <entry> <size> [--conviction] [--strategy X]   (alias for add)
  status [mint]         Position monitor status
  watch [--interval S] [--minutes N]
  close <mint> <exit_px> <reason>
  quote <mint> [usd]    Jupiter fee/slip estimate
  vet <wallet>          Smart-wallet vettor
  journal [days]        Trade + filter-precision report
  followups             Run due PRFS follow-up checks
  daily                 crawl + followups + journal (for cron)
"""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return 1
    cmd = args[0]

    if cmd == "crawl":
        import telegram_crawler
        new = telegram_crawler.crawl_all(scan=True)
        for c in new: telegram_crawler.desk_alert(c)
        import journal
        if new:
            for c in new:
                journal.schedule_followup(c["mint"], "call_outcome", 24, f"call {c.get('symbol')}")
            print(f"\n{len(new)} new call(s); follow-ups scheduled (+24h).")
        return 0

    if cmd == "scan":
        mint = args[1]
        deep = "--deep" in args
        import aegis
        rep = aegis.score_coin(mint, deep=deep)
        print(f"AEGIS {rep['symbol'] or mint[:10]}...")
        print(f"  score: {rep['score']}/100 [{rep['verdict']}] strategy={rep['strategy']}")
        if rep["deployer"]: print(f"  deployer: {rep['deployer']}")
        for f in rep["flags"]: print(f"  ⚠ {f}")
        print(f"  MC=${rep.get('mc') or 0:,.0f} liq=${rep.get('liq') or 0:,.0f} px=${rep.get('price')}")
        import journal
        journal.schedule_followup(mint, "scan_outcome", 24, "aegis scan")
        return 0

    if cmd == "intel":
        from signals import token_intel
        import json as _json
        i = token_intel(args[1])
        print(_json.dumps({k: v for k, v in i.items() if k != "pair_ok"}, indent=2, default=str))
        return 0

    if cmd in ("buy", "status", "watch", "close", "list"):
        import position_monitor as pm
        if cmd == "buy":
            # 'buy' is the desk-facing verb; position_monitor's verb is 'add'
            args = ["add"] + args[1:]
        sys.argv = [pm.__file__] + args
        return pm.main()

    if cmd == "quote":
        from jupiter_estimator import report
        report(args[1], float(args[2]) if len(args) > 2 else 20)
        return 0

    if cmd == "cohort":
        # Build 6: FomoAPI 3-window cohort service (24h ∩ 7d ∩ 30d, entity-collapsed)
        from bot.intelligence.cohort import refresh, qualifying_cohort, credit_budget_check, ensure_schema
        ensure_schema()
        force = "--refresh" in args
        res = refresh(force=force)
        if res.get("skipped"):
            print(f"cohort: skipped (last refresh {res['last_refresh_ago_s']/3600:.1f}h ago; use --refresh to force)")
        else:
            print(f"cohort: refreshed {res.get('saved')} snapshot rows at {time.strftime('%H:%M')}")
        co = qualifying_cohort()
        print(f"Qualifying 24h∩7d∩30d cohort: {len(co)} wallets (entity-collapsed)")
        for c in co[:15]:
            print(f"  {c['wallet'][:16]}... entity={c['entity'][:10]} wr={c['win_rate']:.0%}")
        cb = credit_budget_check()
        print(f"Credit budget: {cb['credits_per_month']:,.0f}/mo @ 8h refresh (free cap 250k: {cb['within_free_250k']})")
        return 0

    if cmd == "gmgn":
        # GMGN rank discovery feed (free, keyless — verified Oct 16)
        from desk_common import gmgn_rank
        tf = args[1] if len(args) > 1 else "1m"
        order = args[2] if len(args) > 2 else "volume"
        n = int(args[3]) if len(args) > 3 else 10
        rows = gmgn_rank(timeframe=tf, order=order, limit=n)
        if not rows:
            print("gmgn: no data (Cloudflare block / rate limit)"); return 1
        print(f"GMGN {tf} top {len(rows)} by {order}:")
        for i, r in enumerate(rows, 1):
            sym = r.get("symbol") or "?"
            addr = (r.get("address") or "")[:10]
            mc = (r.get("market_cap") or 0)
            v = (r.get("volume") or 0)
            h = r.get("holder_count") or 0
            sniper = r.get("sniper_count") or 0
            rug = (r.get("rug_ratio") or 0)
            flag = "HONEYPOT?" if r.get("is_honeypot") else ("CREATOR_CLOSED" if r.get("creator_close") else "")
            print(f"  {i:2}. {sym:12s} {addr}.. MC=${mc:,.0f} vol=${v:,.0f} holders={h} snipers={sniper} rug={rug:.2f} {flag}")
        return 0

    if cmd == "vet":
        from wallet_vettor import vet_wallet
        vet_wallet(args[1], int(args[2]) if len(args) > 2 else 100)
        return 0

    if cmd == "journal":
        from journal import report
        report(int(args[1]) if len(args) > 1 else 14)
        return 0

    if cmd == "followups":
        from journal import run_followups
        n = run_followups()
        print(f"{n} follow-up(s) processed.")
        return 0

    if cmd == "daily":
        print(f"--- DESK DAILY {time.strftime('%Y-%m-%d %H:%M')} ---")
        import telegram_crawler
        new = telegram_crawler.crawl_all(scan=True, verbose=False)
        for c in new: telegram_crawler.desk_alert(c)
        import journal
        journal.run_followups(verbose=True)
        print()
        journal.report(14)
        return 0

    if cmd == "card":
        """Playbook card for a curated call (Tier 2.1 metrics)."""
        mint = args[1]
        import sqlite3
        con = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "aegis.db"), timeout=15)
        try:
            con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA busy_timeout=15000")
        except Exception:
            pass
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM call_metrics WHERE mint=?", (mint,)).fetchone()
        con.close()
        if not row:
            print(f"No playbook card for {mint[:16]}... (run a crawl first)")
            return 1
        print(f"PLAYBOOK CARD: ${row['symbol']} via {row['channel']}")
        for k in ("mc","liq","fill_class","fill_min","aegis_score","verdict","ave_risk_score","top10_pct","smart_money_count","buys5","sells5","mc_delta_pct"):
            print(f"  {k:18s}: {row[k]}")
        return 0

    if cmd == "backfill":
        from replay_backfill import main as rbf
        sys.argv = ["replay_backfill.py"] + args[1:]
        return rbf()

    if cmd == "fomo":
        """Smart-money overlay for a curated call (Tier 1.2)."""
        import aegis
        r = aegis.fomo_overlay(args[1])
        print(f"FOMO overlay {args[1][:12]}...")
        print(f"  holders listed: {len(r['holders'])} | smart-money tagged: {r['smart_money_count']}")
        if r.get("board"):
            b = r["board"]
            print(f"  board fallback: {b.get('symbol')} on {b.get('board')} — holders={b.get('holders')} net={b.get('network')}")
        for t in r["tagged"]:
            print(f"  - {t.get('handle')} [{t.get('class')}] pnl7d=${t.get('pnl_7d',0):,} followers={t.get('followers',0)}")
        return 0

    if cmd == "holders":
        """Holder growth + concentration for any mint (free: Helius + Rugcheck)."""
        from signals import holder_growth, holder_concentration
        hg = holder_growth(args[1])
        hc = holder_concentration(args[1])
        print(f"HOLDER GROWTH {args[1][:12]}... (Helius sample)")
        if hg:
            print(f"  unique buyers (sample): {hg['unique_buyers']} | sellers: {hg['unique_sellers']}")
            print(f"  buyers 1h/6h/24h: {hg['buyers_1h']}/{hg['buyers_6h']}/{hg['buyers_24h']}")
            print(f"  txs sampled: {hg['txs_sampled']} | span: {hg['sample_span_h']}h")
        else:
            print("  no Helius data")
        print("CONCENTRATION (Rugcheck)")
        if hc:
            print(f"  top1/top10/top20: {hc['top1_pct']}%/{hc['top10_pct']}%/{hc['top20_pct']}%")
            print(f"  HHI: {hc['hhi']} | pseudo-Gini: {hc['pseudo_gini']} | total holders: {hc['total_holders']}")
        else:
            print("  no Rugcheck report")
        return 0

    if cmd == "scan-ask":
        """Curated HMQuant review: post CA to scan group as human, collect ALL bot
        cards, merge with AEGIS into ONE card, post it. (scan_curator.py once mode)"""
        import asyncio
        import scan_curator as sc
        mint = args[1]
        card = asyncio.run(sc.run_once(mint))
        print("\n--- posted to HMQ group ---")
        return 0

    if cmd == "scan-watch":
        """Run the scan-group curator daemon: watch for human-posted CAs, curate + post one card each."""
        import asyncio
        from scan_curator_daemon import run_daemon
        asyncio.run(run_daemon())
        return 0

    if cmd == "stream":
        """Post-buy WS monitor (monitoring ONLY, never signal source)."""
        from stream_listener import main as slm
        sys.argv = ["stream_listener.py"] + args[1:]
        import asyncio
        asyncio.run(slm())
        return 0

    if cmd == "bracket":
        """PAPER bracket plan for a curated call — quotes + TP/SL ladder. NO execution."""
        mint = args[1]
        usd = float(args[2]) if len(args) > 2 else 20
        from jupiter_estimator import quote
        import position_monitor as pm
        q = quote(mint, usd)
        if "error" in q:
            print(q["error"]); return 1
        pair = __import__("desk_common").dex_pair(mint)
        if not pair: print("no pair data"); return 1
        mc = float(pair.get("marketCap") or 0)
        tier = pm.tier_for(mc)
        print(f"PAPER BRACKET ${usd} -> {mint[:12]}... (MC ${mc:,.0f}, tier {tier})")
        print(f"  quote via {q['via']}: impact {q['price_impact_pct']}% + fee drag ${q['fee_drag_usd']}")
        print(f"  TP1 (sell 50%): +{(tier[1]-1)*100:.0f}% -> ${usd*tier[1]:.2f}")
        print(f"  TP2 (exit rest): +{(tier[2]-1)*100:.0f}% -> ${usd*tier[2]:.2f}")
        print(f"  STOP: -{tier[3]*100:.0f}% -> ${usd*(1-tier[3]):.2f}")
        print("  (paper plan only — no execution until user enables trading)")
        return 0

    if cmd == "whales":
        """NEW PROVIDERS (Oct 17): whale-flow tape + wallet PnL + chain metrics.
        Usage: desk.py whales [recent|traders|wallets|chain|pf] [n]"""
        from desk_common import (sol_wales_traders, sol_wales_moves_recent,
                                 walletintel_leaderboard, solscan_chaininfo,
                                 walletintel_summary)
        sub = args[1] if len(args) > 1 else "recent"
        n = int(args[2]) if len(args) > 2 else 10
        if sub == "recent":
            moves = sol_wales_moves_recent(limit=n)
            print(f"🐋 Sol.wales recent moves ({len(moves)}):")
            for m in moves[:n]:
                _mn = m.get('mint') or 'SOL'
                print(f"  {m.get('kind','?'):10s} {_mn[:12]:14s} {str(m.get('amount','?')):>14s}  "
                      f"@ {(m.get('trader') or {}).get('label') or (m.get('trader') or {}).get('address','?')[:10]}")
        elif sub == "traders":
            tr = sol_wales_traders(limit=n)
            print(f"🐋 Sol.wales tracked whales ({len(tr)}):")
            for t in tr[:n]:
                print(f"  {t.get('label') or t.get('address','?')[:10]:20s} sol={t.get('sol',0):,} "
                      f"tokens={t.get('tokenCount',0)} moves24h={t.get('moves24h',0)} moves7d={t.get('moves7d',0)}")
        elif sub == "wallets":
            lb = walletintel_leaderboard(limit=n)
            print(f"💎 WalletIntel leaderboard ({len(lb)}):")
            for w in lb[:n]:
                print(f"  {w.get('wallet','?')[:12]}  score={w.get('score')} wr={w.get('win_rate')}% "
                      f"pnl={w.get('pnl_sol'):+.2f}SOL {w.get('strategy')} trades={w.get('total_trades')}")
        elif sub == "chain":
            ci = solscan_chaininfo()
            print("⛓ Solscan chaininfo:", ci)
        elif sub == "pf":
            # portfolio of a specific wallet (walletintel PnL)
            w = args[2] if len(args) > 2 else None
            if not w:
                print("usage: desk.py whales pf <wallet>"); return 1
            s = walletintel_summary(w)
            print("💎 WalletIntel summary:", s)
        else:
            print(__doc__); return 1
        return 0

    print(f"unknown command: {cmd}"); print(__doc__); return 1

if __name__ == "__main__":
    sys.exit(main())
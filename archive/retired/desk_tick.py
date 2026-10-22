#!/usr/bin/env python3
"""Cron watchdog for the memecoin desk.
Prints ONLY actionable lines — empty stdout sends nothing (watchdog pattern).
Runs: crawl channels -> AEGIS-scan new calls -> PRFS follow-ups -> position alerts.
"""
import sys, os, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

def _plain_signal(s):
    """Translate engine signal codes into plain English."""
    s = s or ""
    if s.startswith("SLOW-GRAD: fast"):
        return "Bots piled in fast at launch — expect quick dumps, we flip fast and keep nothing."
    if s.startswith("SLOW-GRAD:"):
        return "Real people filled in slowly — healthier chart, can hold a bit longer."
    if s.startswith("WASH/LPI"):
        return "Volume looks fake (few wallets trading back and forth) — don't trust the pump."
    if s.startswith("VOLUME_CLIMAX_STALL"):
        return "Trading volume just spiked to its ceiling but price stopped rising — classic top warning."
    if s.startswith("VOLUME_DRYING"):
        return "Buyers are disappearing — momentum is gone."
    if s.startswith("ROUND_STALL") or "round-number" in s.lower():
        return "Price froze right under a big round number — sellers usually wait there."
    if s.startswith("BUY IMBALANCE") or "buy" in s.lower() and "imbalance" in s.lower():
        return "Big buying pressure right now (good)."
    if s.startswith("SELL IMBALANCE"):
        return "Sellers are overwhelming buyers — exit window is closing."
    if s.startswith("CLIP1"):
        return "Hit first profit target — sell half, let the rest ride."
    if s.startswith("CLIP2"):
        return "Hit final target — take everything off the table."
    if s.startswith("STOP"):
        return "Fell through the stop loss — cutting, no hoping."
    if s.startswith("LIQ") or "liq collapse" in s.lower():
        return "Liquidity is draining out — emergency exit, pool may be pulling."
    if s.startswith("FRAGILITY"):
        return "Structure is fragile (whales hold too much) — quick trade only."
    if s.startswith("BEHAVIOR") or "front-run" in s.lower():
        return "This call came from a KOL who usually dumps fast — we exit before they do."
    if s.startswith("intel_err"):
        return None  # internal noise, hide from user
    return s

def _plain_action(a):
    return {
        "EXIT_ALL": "SELL EVERYTHING NOW",
        "EXIT_HALF": "SELL HALF NOW",
        "CLIP1": "TAKE PROFIT — sell half",
        "CLIP2": "TAKE PROFIT — sell all",
        "STOP": "STOPPED OUT — sell all",
        "EMERGENCY": "EMERGENCY EXIT — sell all immediately",
        "EMERGENCY_EXIT": "EMERGENCY EXIT — liquidity collapse, sell all immediately",
        "CLIP_50": "TAKE PROFIT — sell half now",
        "CLIP_100": "TAKE PROFIT — sell everything",
        "DEAD": "DEAD POSITION — zero liquidity, nothing to exit into",
    }.get(a, a.replace("_", " ").title())

def notify_hmq(c, score):
    """Post a fresh call + desk verdict into the HMQ scan group so scan bots react."""
    import json as _json
    try:
        from desk_common import load_config, http_json
        tok = load_config().get("HMQ_BOT_TOKEN")
        if not tok:
            return
        sym = c.get("symbol") or "?"
        mint = c["mint"]
        mc = c.get("live_mc") or c.get("mc") or 0
        chan = (c.get("channel") or "?").replace("Trending", "").replace("trending", "")
        qual = f"Contract {score:.0f}/100" if score is not None else "Contract: no data"
        text = (f"📡 Channel call: ${sym} from {chan} at ${mc:,.0f} MC\n"
                f"CA: {mint}\n{qual} — scan bots, second opinion?")
        http_json(f"https://api.telegram.org/bot{tok}/sendMessage", method="POST",
                  body={"chat_id": -1004430296219, "text": text},
                  headers={"Content-Type": "application/json"}, retries=1)
    except Exception:
        pass


def main():
    import telegram_crawler, journal
    out = []
    # 1) Crawl for new calls — scan at most MAX_SCANS per tick to avoid cron overrun (SIGTERM -15)
    MAX_SCANS = 4
    try:
        new = telegram_crawler.crawl_all(scan=True, verbose=False, max_scan=MAX_SCANS)
        for c in new:
            sym = c.get('symbol') or '?'
            chan = (c.get('channel') or '').replace('Trending', '').replace('trending', '')
            mc = c.get('live_mc')
            score = c.get('score')
            stale = c.get('mc') and mc and c['live_mc'] < c['mc'] * 0.5
            conf = c.get('confluence') or 1
            lines = []
            if stale:
                lines.append(f"📢 ${sym} was called by {chan}, but it's already dumping (way below call price) — skip, channel was late.")
            else:
                notify_hmq(c, score)
                qual = ""
                if score is not None:
                    qual = f" Contract check: {score:.0f}/100"
                    if score >= 50: qual += " (clean)."
                    elif score >= 30: qual += " (some red flags — fast flip only, tight stop)."
                    else: qual += " (multiple red flags — if we play it, fastest flips, hardest stop)."
                lines.append(f"📢 New call: ${sym} (from {chan}) at ${mc:,.0f} market cap.{qual}")
                if conf >= 2:
                    lines.append(f"   🔥 {conf} channels called this same token — stronger signal ({', '.join(c.get('channels') or [])}).")
                # GMGN second-opinion context (never entry logic — pure confluence display)
                try:
                    from desk_common import gmgn_lookup, gmgn_summary
                    g = gmgn_summary(gmgn_lookup(c["mint"]))
                    if g:
                        lines.append(f"   🧪 {g}")
                except Exception:
                    pass
                lines.append(f"   CA: {c['mint']}")
            out.extend(lines)
        if new:
            for c in new:
                journal.schedule_followup(c["mint"], "call_outcome", 24, f"call {c.get('symbol')}")
    except Exception as e:
        out.append(f"⚠ crawl error: {str(e)[:120]}")

    # 2) Due PRFS follow-ups (silent unless something notable)
    try:
        journal.run_followups(verbose=False)
    except Exception:
        pass

    # 3) Position alerts: only when action != HOLD
    try:
        import position_monitor as pm
        pos = pm.load_positions()
        for m, p in list(pos.items())[:8]:
            try:
                r = pm.analyze(m, p)
                if r["ok"] and r["action"] != "HOLD":
                    tag = "🔒" if r.get("conviction") else ""
                    pnl_s = f"{'+' if r['pnl_pct'] >= 0 else ''}{r['pnl_pct']:.0f}%"
                    out.append(f"🚨 {tag}${r['symbol']} is {pnl_s} ({r['mult']:.2f}x from entry) — {_plain_action(r['action'])}")
                    if r.get("lifecycle"):
                        out.append(f"   • phase: {str(r['lifecycle']).lower().replace('_', ' ')}")
                    if (r.get("thesis") or {}).get("confidence") is not None:
                        out.append(f"   • conviction score {r['thesis']['confidence']:.0f}/100")
                    reasons = [x for x in (_plain_signal(s) for s in r["signals"][:3]) if x]
                    for reason in reasons:
                        out.append(f"   • {reason}")
            except Exception as e:
                out.append(f"⚠ monitor err {m[:10]}: {str(e)[:60]}")
        pm.save_positions(pos)
    except Exception as e:
        out.append(f"⚠ positions error: {str(e)[:80]}")

    if out:
        print("\n".join(out))

if __name__ == "__main__":
    main()
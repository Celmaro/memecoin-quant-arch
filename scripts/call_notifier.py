#!/usr/bin/env python3
"""call_notifier.py — DIRECT curated-call pipeline (Oct 19, scanner group REMOVED).

NEW FLOW (user mandate): channel call -> listener -> calls.jsonl -> DEEP AEGIS
forensics -> one card to DM -> paper_tick auto-buys. NO group post, NO scanner
collection, NO curator card. The HMQ scan group is OUT of the workflow.

- DM line -> stdout (no_agent cron delivers to the user's DM)
- Deep AEGIS (score_coin deep=True) replaces the old group-scanner aggregation
- paper_tick auto_buy (its own 1m cron) consumes calls.jsonl independently — it
  reads the same file, so AEGIS enrichment and the paper entry happen in parallel.

Run via cron every 1m (no_agent). Silent when no fresh calls.
State: ~/memecoin-quant/notifier_state.json (watermark last_ts)
"""
import json, os, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
CALLS = os.path.join(DESK, "calls.jsonl")
STATE = os.path.join(DESK, "notifier_state.json")
MAX_POST = 4  # cap per tick so a burst doesn't flood the DM

sys.path.insert(0, DESK)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE)


def deep_aegis_card(mint, chan, sym, mc):
    """Build the RICH forensics DM card (user's requested format — Oct 19).

    Returns (card_text, score, verdict) or (None, None, None) on failure.
    Delegates to rich_card.build_forensics_card with the call payload so the
    full narrative (flags w/ explanations, market side, holders, authorities,
    socials, migration, verdict, action plan) reaches the DM.

    Oct 21 (user: card matches aegis.db): rich_card returns the score/verdict
    from the SAME deep scan it rendered (the one it wrote to deep_cache). We do
    NOT call score_coin a second time — that second call produced a divergent
    card (17.0) vs stored deep_cache (22.0) for $LASTAGENCY when the tape moved
    between the two scans. One scan, one source of truth.
    """
    try:
        sys.path.insert(0, DESK)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from rich_card import build_forensics_card
        card, score, verdict = build_forensics_card(mint, {"symbol": sym, "channel": chan, "mc": mc})
        if not card:
            return None, None, None
        return card, score, verdict
    except Exception:
        return None, None, None


def send_dm(text):
    """Send DIRECTLY to the user's private DM via bot API (chat 1223241788).
    Chunked <=3800 chars (Telegram limit 4096). DM-only, never a group.
    Returns True on success. stdout fallback happens in main()."""
    import re as _re, urllib.request
    tok = None
    for p in (os.path.join(DESK, ".env"), os.path.join(DESK, "CONFIG.md")):
        try:
            m = _re.search(r"(\d{6,}:[A-Za-z0-9_-]{30,})", open(p).read())
            if m:
                tok = m.group(1)
                break
        except Exception:
            pass
    if not tok:
        return False
    ok = True
    for i in range(0, len(text), 3800):
        chunk = text[i:i + 3800]
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                data=json.dumps({"chat_id": "1223241788", "text": chunk}).encode(),
                headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=15).read())
            ok = ok and bool(r.get("ok"))
        except Exception:
            ok = False
    return ok


def main():
    st = load_state()
    last = st.get("last_ts", 0)
    instant = set(st.get("instant_sent") or [])
    fresh = []
    try:
        with open(CALLS) as f:
            for line in f:
                try:
                    c = json.loads(line)
                except Exception:
                    continue
                # P1-5 (Oct 20): skip retracted/dead-mint lines — they were
                # previously re-delivered as fresh (dead-mint noise).
                if c.get("retracted") or c.get("dead"):
                    continue
                ts = float(c.get("ts") or c.get("dt_ts") or 0)
                if ts > last:
                    fresh.append(c)
    except FileNotFoundError:
        pass

    if not fresh:
        return 0  # silent

    # calls.jsonl is NOT chronological — sort by ts ascending, consume oldest first.
    # P9 (Oct 20): watermark must be key-based, not ts-only. A >4-call same-ts burst
    # used to lose the tail forever: next_ts advanced past the post's ts and the
    # strict `ts > last` filter dropped the remaining same-ts rows. Now we persist a
    # capped keyed list of handled records (mint:msg_id) and advance the watermark
    # to the max ts of ALL fresh rows so the burst drains on the NEXT run if
    # MAX_POST capped it this run.
    fresh.sort(key=lambda c: float(c.get("ts") or 0))
    handled = set(st.get("handled_keys") or [])
    out = []
    instant = set(st.get("instant_sent") or [])
    max_fresh_ts = max(float(c.get("ts") or 0) for c in fresh)
    for c in fresh:
        mint = c.get("mint") or "?"
        key = f"{mint}:{c.get('msg_id') or c.get('ts') or ''}"
        if key in handled:
            continue
        if mint in instant:
            # already carded instantly — mark handled, never re-deliver
            handled.add(key)
            continue
        if len(out) >= MAX_POST:
            break  # leave the rest for the next run (watermark only max_fresh_ts)
        chan = (c.get("channel") or "?").replace("Trending", "").replace("trending", "")
        sym, mc = c.get("symbol"), None
        try:
            from desk_common import dex_pair
            p = dex_pair(mint)
            if p:
                sym = (p.get("baseToken") or {}).get("symbol", sym or "?")
                mc = (p.get("marketCap") or 0)
        except Exception:
            pass
        # DEEP AEGIS instead of group scanner aggregation (user mandate Oct 19)
        card, score, verdict = deep_aegis_card(mint, chan, sym, mc)
        if card:
            out.append(card)
        else:
            # scan failed — still alert with basic info so the call isn't lost
            mc_s = f" at ${mc:,.0f} MC" if mc else ""
            out.append(f"📢 New call: ${sym or '?'} (from {chan}){mc_s}\n   CA: {mint}")
        # TRIGGER THE BUY IMMEDIATELY after the AEGIS card (user mandate Oct 19):
        # paper_tick.entry_call applies the $10K liq gate + hygiene + dedupe.
        # Oct 20: pass the AEGIS score so the <30 veto / 70+ boost policy applies.
        try:
            sys.path.insert(0, DESK)
            from paper_tick import entry_call, load_book
            bought_line, skip = entry_call(c, aegis_score=score)
            if bought_line:
                out.append(bought_line)
            elif skip:
                out.append(f"⏭ skipped: {skip} (${sym or mint[:8]})")
        except Exception as e:
            out.append(f"⚠ auto-buy error {mint[:8]}: {str(e)[:60]}")
        # P9: this call is fully handled (carded + entry attempted) — mark it so a
        # future run never re-delivers it, even if the watermark sits at the same ts.
        handled.add(key)

    # P9: watermark advances to the max ts of ALL fresh rows — a >4 same-ts burst
    # keeps the unconsumed tail eligible next run (they were NOT marked handled).
    # P1-5 (Oct 20): MERGE — save_state was replacing the whole state dict, wiping
    # instant_trigger's instant_sent dedupe → double-card risk on every fresh loop.
    # Audit P1#5 (Oct 22): delivery-before-save — the OLD order saved state
    # (watermark + handled_keys) BEFORE send_dm, so a failed DM marked calls
    # handled and they were lost forever (no retry next run). Now: attempt
    # delivery FIRST, advance state only on success. On failure the watermark
    # stays put and the SAME calls retry next run.
    delivered = False
    if out:
        blob = "\n\n".join(out)
        # DM-only delivery: direct bot send first (reliable), cron-stdout as backup
        sent = send_dm(blob)
        delivered = bool(sent)
        print(blob)
        print(f"\n[delivery: dm_direct={'ok' if sent else 'FAILED — check bot token / DM chat'}]")
    if delivered or not out:
        # P9 + 3rd audit 2.4: when MAX_POST capped the run, do NOT advance the
        # watermark past the consumed prefix — the skipped same-ts tail would
        # fail the strict `ts > last` filter next run and be lost forever.
        # Keeping last_ts unchanged makes the next run re-scan: already-delivered
        # rows are deduped by handled_keys (capped durability, recent keys
        # survive), and the unconsumed tail is finally picked up. The watermark
        # only advances to max_fresh_ts when the cap did NOT bind.
        st["last_ts"] = max(st.get("last_ts", 0), max_fresh_ts if (not out or len(out) < MAX_POST) else st.get("last_ts", 0))
        st["handled_keys"] = sorted(handled)[-400:]  # capped durability
        save_state(st)
    else:
        print("[delivery FAILED — watermark NOT advanced; retrying next run]")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    socials, migration, verdict, action plan) reaches the DM."""
    try:
        sys.path.insert(0, DESK)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from rich_card import build_forensics_card
        card = build_forensics_card(mint, {"symbol": sym, "channel": chan, "mc": mc})
        if not card:
            return None, None, None
        import aegis as _a
        score, verdict = None, None
        try:
            rep = _a.score_coin(mint, deep=True)
            score, verdict = rep.get("score"), rep.get("verdict")
        except Exception:
            pass
        return card, score, verdict
    except Exception:
        return None, None, None


def main():
    st = load_state()
    last = st.get("last_ts", 0)
    fresh = []
    try:
        with open(CALLS) as f:
            for line in f:
                try:
                    c = json.loads(line)
                except Exception:
                    continue
                ts = float(c.get("ts") or c.get("dt_ts") or 0)
                if ts > last:
                    fresh.append(c)
    except FileNotFoundError:
        pass

    if not fresh:
        return 0  # silent

    # calls.jsonl is NOT chronological — sort by ts ascending, consume oldest first
    fresh.sort(key=lambda c: float(c.get("ts") or 0))
    posted = fresh[:MAX_POST]
    next_ts = st.get("last_ts", 0)

    out = []
    for c in posted:
        mint = c.get("mint") or "?"
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
        try:
            sys.path.insert(0, DESK)
            from paper_tick import entry_call, load_book
            bought_line, skip = entry_call(c)
            if bought_line:
                out.append(bought_line)
            elif skip:
                out.append(f"⏭ skipped: {skip} (${sym or mint[:8]})")
        except Exception as e:
            out.append(f"⚠ auto-buy error {mint[:8]}: {str(e)[:60]}")
        # watermark advances only if processed
        next_ts = max(next_ts, float(c.get("ts") or 0))

    save_state({"last_ts": next_ts})
    if out:
        print("\n\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

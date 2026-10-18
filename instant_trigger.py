#!/usr/bin/env python3
"""instant_trigger.py <mint> <channel> — fired by telethon_listener the moment a
curated call lands. Sends the rich AEGIS card to the DM immediately (no 1m cron
lag) and triggers paper entry. Lock + consumed-marking prevents double-send when
the 1m cron also picks the call up (cron remains the safety net)."""
import json, os, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(DESK, ".instant_trigger.lock")
SENT = os.path.join(DESK, "notifier_state.json")
sys.path.insert(0, DESK)
sys.path.insert(0, HERE)


def main():
    if len(sys.argv) < 2:
        return 1
    mint = sys.argv[1]
    # cross-process lock: only one instant card at a time (cron + trigger overlap safe)
    deadline = time.time() + 60
    while os.path.exists(LOCK):
        if time.time() > deadline:
            return 0  # another card is being built — cron will cover this one
        time.sleep(2)
    open(LOCK, "w").write(str(os.getpid()))
    try:
        # consumed check: was this mint already carded?
        sys.path.insert(0, "/home/agentuser/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239/scripts")
        from call_notifier import deep_aegis_card, send_dm, load_state, save_state
        # mark consumed BEFORE the slow AEGIS build so the 1m cron never double-sends
        st = load_state()
        st.setdefault("instant_sent", [])
        if mint in st["instant_sent"][-200:]:
            return 0
        st["instant_sent"].append(mint)
        st["instant_sent"] = st["instant_sent"][-200:]
        save_state(st)

        # find the call record (symbol/mc) from calls.jsonl
        sym, mc = None, None
        try:
            with open(os.path.join(DESK, "calls.jsonl")) as f:
                for line in reversed(f.readlines()[-200:]):
                    c = json.loads(line)
                    if c.get("mint") == mint:
                        sym = c.get("symbol")
                        mc = c.get("mc")
                        break
        except Exception:
            pass

        card, score, verdict = deep_aegis_card(mint, sys.argv[2] if len(sys.argv) > 2 else "?", sym, mc)
        if not card:
            card = f"📢 New call: ${sym or '?'} ({sys.argv[2] if len(sys.argv) > 2 else '?'})\n   CA: {mint}\n   (AEGIS scan failed — cron will follow up)"
        out = [card]

        # immediate paper entry (same $10K gate as cron path)
        try:
            from paper_tick import entry_call
            bought, skip = entry_call({"mint": mint, "symbol": sym, "mc": mc})
            if bought:
                out.append(bought)
            elif skip:
                out.append(f"⏭ skipped: {skip} (${sym or mint[:8]})")
        except Exception as e:
            out.append(f"⚠ auto-buy error {mint[:8]}: {str(e)[:60]}")

        send_dm("\n\n".join(out))
        return 0
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

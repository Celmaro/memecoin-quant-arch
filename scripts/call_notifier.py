#!/usr/bin/env python3
"""call_notifier.py — announce FRESH curated calls.

- DM line -> stdout (no_agent cron delivers to the user's DM)
- HMQ scan group post -> Telethon HUMAN account (treizodemayo) — NOT the bot token
  (bot token is reserved for the gateway's DM polling; one token = one getUpdates
  consumer). Posting as human ALSO triggers the scan bots, which the curator then
  collects into one curated card — the full loop.

Run via cron every 1m (no_agent). Silent when no fresh calls.
State: ~/memecoin-quant/notifier_state.json (watermark last_ts)
"""
import asyncio, json, os, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
CALLS = os.path.join(DESK, "calls.jsonl")
STATE = os.path.join(DESK, "notifier_state.json")
POSTBOX = os.path.join(DESK, "pending_group_posts.jsonl")
GROUP = "HMQcheckergroup"
MAX_POST = 4  # cap per tick so a burst doesn't flood the group

sys.path.insert(0, DESK)


def queue_hmq_post(text, mint=None, channel=None):
    """Queue a group post for the scan curator daemon to send (it owns the human
    account connection). One Telethon connection for group posting = no session
    conflicts, and the curator naturally triggers scan bots on these posts.
    mint + channel ride along so the curated card can attribute the source."""
    try:
        with open(POSTBOX, "a") as f:
            f.write(json.dumps({"ts": time.time(), "text": text,
                                "mint": mint, "channel": channel}) + "\n")
        return True
    except Exception:
        return False


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


def post_hmq_human(text):
    """Post into the HMQ scan group as the human account via Telethon (own session)."""
    try:
        import asyncio
        from telethon import TelegramClient

        async def _go():
            creds = json.load(open(f"{DESK}/.tg_credentials.json"))
            client = TelegramClient(f"{DESK}/tg_session_curator",
                                    creds["api_id"], creds["api_hash"])
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                return False
            g = await client.get_entity(GROUP)
            await client.send_message(g, text)
            await client.disconnect()
            return True
        return asyncio.run(_go())
    except Exception:
        return False


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

    # audit P2 (Oct 18): watermark must advance ONLY past calls actually POSTED,
    # not to the max across all fresh — a >4-call burst silently dropped later calls.
    posted = fresh[:MAX_POST]
    next_ts = max((float(c.get("ts") or 0) for c in posted), default=last)

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
        mc_s = f" at ${mc:,.0f} MC" if mc else ""
        dm = f"📢 New call: ${sym or '?'} (from {chan}){mc_s}\n   CA: {mint}"
        out.append(dm)
        hmq_text = f"📡 Channel call: ${sym or '?'} from {chan}{mc_s}\nCA: {mint}"
        # queue with channel + mint so the curator card can attribute the source
        queue_hmq_post(hmq_text, mint=mint, channel=chan)  # curator sends as human -> scan bots -> curated card

    save_state({"last_ts": next_ts})
    if out:
        print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""send_ca_human.py — post a CA into the HMQ scan group as a HUMAN (treizodemayo via
Telethon, NOT the bot token), wait, then pull + print all replies/cards mentioning it.

Why: scan bots (PIRBView/Phanes/MASTR) ignore bot accounts but respond to real users.
"""
import asyncio, json, sys, time

DESK = "/home/agentuser/memecoin-quant"
sys.path.insert(0, DESK)

async def main():
    from telethon import TelegramClient
    creds = json.load(open(f"{DESK}/.tg_credentials.json"))
    client = TelegramClient(f"{DESK}/tg_session", creds["api_id"], creds["api_hash"])
    await client.connect()
    if not await client.is_user_authorized():
        print("NOT_AUTHORIZED"); return

    group = await client.get_entity("HMQcheckergroup")
    mint = sys.argv[1] if len(sys.argv) > 1 else "94YZQRL1ruSvqyRBdRsRo7k2UyQBonNNX38mQmERpump"
    text = sys.argv[2] if len(sys.argv) > 2 else f"scan this: {mint}"

    me = await client.get_me()
    sent = await client.send_message(group, text)
    print(f"SENT as {me.username or me.id} msg_id={sent.id}: {text[:80]}")

    # wait for bot replies (bots usually respond within ~30-60s)
    await asyncio.sleep(60)

    msgs = await client.get_messages(group, limit=12)
    print(f"\n=== last {len(msgs)} messages after our human post ===")
    for m in msgs:
        sender = None
        try:
            if m.sender_id:
                sender = (await client.get_entity(m.sender_id)).username or str(m.sender_id)
        except Exception:
            sender = m.sender_id
        txt = (m.message or "").replace("\n", " | ")[:260]
        print(f"[{m.id}] {sender}: {txt}")
    await client.disconnect()

asyncio.run(main())

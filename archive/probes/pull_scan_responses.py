#!/usr/bin/env python3
"""pull_scan_responses.py — enumerate ALL responders to a CA in the HMQ scan group.

Pulls a wide window (default 300 msgs), lists every distinct sender + count, and
extracts every message mentioning the mint (so we catch bots that reply late).
"""
import asyncio, json, sys, collections

DESK = "/home/agentuser/memecoin-quant"
MINT = sys.argv[1] if len(sys.argv) > 1 else "94YZQRL1ruSvqyRBdRsRo7k2UyQBonNNX38mQmERpump"
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 300

async def main():
    from telethon import TelegramClient
    creds = json.load(open(f"{DESK}/.tg_credentials.json"))
    client = TelegramClient(f"{DESK}/tg_session", creds["api_id"], creds["api_hash"])
    await client.connect()
    group = await client.get_entity("HMQcheckergroup")

    msgs = await client.get_messages(group, limit=LIMIT)
    senders = collections.Counter()
    mentions = []
    mint_l = MINT[:12]  # match prefix (cards truncate CA)

    for m in msgs:
        sender = None
        try:
            if m.sender_id:
                sender = (await client.get_entity(m.sender_id)).username or str(m.sender_id)
        except Exception:
            sender = m.sender_id
        senders[sender] += 1
        txt = m.message or ""
        if mint_l in txt:
            mentions.append((m.id, sender, txt.replace("\n", " | ")[:200]))

    print(f"=== distinct senders in last {len(msgs)} messages ===")
    for s, n in senders.most_common():
        print(f"  {s}: {n}")
    print(f"\n=== messages mentioning {MINT[:12]}: {len(mentions)} ===")
    for mid, s, txt in mentions:
        print(f"  [{mid}] {s}: {txt}")

    await client.disconnect()

asyncio.run(main())
#!/usr/bin/env python3
"""scan_curator_daemon.py — watch HMQ group; when a HUMAN posts a CA, collect ALL
scanner cards, curate ONE HMQuant card, post it. Deduped per mint (state file).

Imported by scan_curator.py in daemon mode (mode=run)."""
import asyncio, json, os, re, sys

DESK = os.path.expanduser("~/memecoin-quant")
sys.path.insert(0, DESK)
from scan_curator import (GROUP, STATE, COLLECT_WINDOW, OWN_BOTS, creds, _client,
                          collect_for_mint, curate_card, MINT_RE, SOL_MINT)

async def run_daemon():
    from desk_common import load_config, http_json
    state = {}
    try:
        state = json.load(open(STATE))
    except Exception:
        pass
    # heartbeat pidfile so watchdog can check liveness without pgrep self-match
    import os as _os
    with open(os.path.join(DESK, "scan_curator.pid"), "w") as f:
        f.write(str(_os.getpid()))
    print(f"pidfile scan_curator.pid: {_os.getpid()}", flush=True)
    client = await _client()
    g = await client.get_entity(GROUP)
    curated = set(state.get("curated", []))
    print(f"curator watching {GROUP} (deduped {len(curated)} mints)…", flush=True)
    seen_upto = state.get("upto", 0)
    while True:
        try:
            msgs = await client.get_messages(g, limit=10)
            for m in msgs:
                if m.id <= seen_upto:
                    continue
                seen_upto = max(seen_upto, m.id)
                txt = m.message or ""
                if m.sender_id is None:
                    continue
                try:
                    sender = (await client.get_entity(m.sender_id)).username or str(m.sender_id)
                except Exception:
                    sender = m.sender_id
                if sender in OWN_BOTS:
                    continue  # never curate our own posts (feedback loop)
                mints = [x for x in MINT_RE.findall(txt)
                         if x != SOL_MINT and not x.startswith("0x") and not x.isdigit()]
                if not mints:
                    continue
                mint = mints[0]
                if mint in curated:
                    continue
                print(f"CA {mint[:10]} from {sender} — collecting ALL bot cards…", flush=True)
                await asyncio.sleep(COLLECT_WINDOW)  # let every bot post
                trigger_ts = m.date.timestamp() if m.date else None
                got = await collect_for_mint(client, mint, after_ts=trigger_ts)
                import aegis
                rep = aegis.score_coin(mint, deep=False)
                card = curate_card(mint, got, rep)
                print(card, flush=True)
                tok = load_config().get("HMQ_BOT_TOKEN")
                if tok:
                    st, _ = http_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                                      method="POST",
                                      body={"chat_id": -1004430296219, "text": card},
                                      headers={"Content-Type": "application/json"}, retries=1)
                    print(f"posted curated card: {st}", flush=True)
                curated.add(mint)
                json.dump({"curated": sorted(curated), "upto": seen_upto},
                          open(STATE, "w"), indent=1)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"watch err: {str(e)[:100]}", flush=True)
            await asyncio.sleep(30)
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(run_daemon())
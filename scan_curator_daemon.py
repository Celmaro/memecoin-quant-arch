#!/usr/bin/env python3
"""scan_curator_daemon.py — watch HMQ group; when a HUMAN posts a CA, collect ALL
scanner cards, curate ONE HMQuant card, post it. Deduped per mint (state file).

Imported by scan_curator.py in daemon mode (mode=run)."""
import asyncio, json, os, re, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
sys.path.insert(0, DESK)
from scan_curator import (GROUP, STATE, COLLECT_WINDOW, OWN_BOTS, creds, _client,
                          collect_for_mint, curate_card, MINT_RE, SOL_MINT)

async def run_daemon():
    from desk_common import load_config, http_json
    import os as _os
    state = {}
    try:
        state = json.load(open(STATE))
    except Exception:
        pass

    def record_curated_card(mint, channel, rep, done_ts):
        """Enrich the call_metrics row with the curated-card deep-scan verdict.
        Keeps original call_ts/channel (latency + confluence stats), refreshes
        aegis_score/verdict/flags from the card's deep scan."""
        try:
            from desk_common import db
            con = db()
            flags = json.dumps(rep.get("flags") or []) if rep else None
            con.execute("""UPDATE call_metrics SET aegis_score=?, verdict=?, flags_json=?,
                           smart_money_count=COALESCE(smart_money_count,0)
                           WHERE mint=?""",
                        (rep.get("score") if rep else None,
                         rep.get("verdict") if rep else None, flags, mint))
            # row may not exist (call logged by a different path) — insert a minimal one
            if con.total_changes == 0:
                con.execute("""INSERT OR REPLACE INTO call_metrics
                               (mint, symbol, channel, call_ts, aegis_score, verdict, created_ts)
                               VALUES(?,?,?,?,?,?,?)""",
                            (mint, (rep or {}).get("symbol"), channel, done_ts,
                             rep.get("score") if rep else None,
                             rep.get("verdict") if rep else None, done_ts))
            con.commit(); con.close()
        except Exception as e:
            print(f"record_curated_card err: {str(e)[:80]}", flush=True)
    # heartbeat pidfile so watchdog can check liveness without pgrep self-match
    with open(os.path.join(DESK, "scan_curator.pid"), "w") as f:
        f.write(str(_os.getpid()))
    print(f"pidfile scan_curator.pid: {_os.getpid()}", flush=True)
    POSTBOX = os.path.join(DESK, "pending_group_posts.jsonl")

    async def drain_postbox(client):
        """Send queued group posts (from call_notifier) as the human account,
        then remove them. One connection = no session conflicts.
        Returns [(mint, channel)] we just posted so we can curate them immediately
        with channel attribution."""
        posted = []
        if not os.path.exists(POSTBOX):
            return posted
        lines = [l for l in open(POSTBOX).read().splitlines() if l.strip()]
        if not lines:
            return posted
        try:
            g = await client.get_entity(GROUP)
            for line in lines:
                try:
                    post = json.loads(line)
                    await client.send_message(g, post.get("text", ""))
                    mint = post.get("mint")
                    if not mint:
                        for mm in MINT_RE.findall(post.get("text", "")):
                            if mm != SOL_MINT and not mm.startswith("0x") and not mm.isdigit():
                                mint = mm; break
                    posted.append((mint, post.get("channel")))
                except Exception as e:
                    print(f"postbox send err: {str(e)[:80]}", flush=True)
            os.remove(POSTBOX)
        except Exception as e:
            print(f"postbox drain err: {str(e)[:80]}", flush=True)
        return posted

    client = await _client()
    g = await client.get_entity(GROUP)
    curated = set(state.get("curated", []))
    print(f"curator watching {GROUP} (deduped {len(curated)} mints)…", flush=True)
    seen_upto = state.get("upto", 0)
    while True:
        try:
            posted = await drain_postbox(client)  # send queued notifier posts first
            for mint, channel in posted:
                if not mint or mint in curated:
                    continue
                t_posted = time.time()
                print(f"[{time.strftime('%H:%M:%S')}] curating queued call {mint[:10]} (src={channel})…", flush=True)
                await asyncio.sleep(COLLECT_WINDOW)
                got = await collect_for_mint(client, mint)
                import aegis
                rep = aegis.score_coin(mint, deep=True)
                card = curate_card(mint, got, rep, channel=channel)
                print(card, flush=True)
                tok = load_config().get("HMQ_BOT_TOKEN")
                if tok:
                    st, _ = http_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                                      method="POST",
                                      body={"chat_id": -1004430296219, "text": card},
                                      headers={"Content-Type": "application/json"}, retries=1)
                    print(f"posted curated card: {st}", flush=True)
                    st2, _ = http_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                                       method="POST",
                                       body={"chat_id": 1223241788, "text": card},
                                       headers={"Content-Type": "application/json"}, retries=1)
                    print(f"sent curated card to DM via bot: {st2}", flush=True)
                print(f"[{time.strftime('%H:%M:%S')}] card done {round(time.time()-t_posted,1)}s after postbox drain", flush=True)
                record_curated_card(mint, channel, rep, time.time())
                curated.add(mint)
                json.dump({"curated": sorted(curated), "upto": seen_upto},
                          open(STATE, "w"), indent=1)
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
                rep = aegis.score_coin(mint, deep=True)  # deep: deployer memory + full checks
                card = curate_card(mint, got, rep)
                print(card, flush=True)
                tok = load_config().get("HMQ_BOT_TOKEN")
                if tok:
                    st, _ = http_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                                      method="POST",
                                      body={"chat_id": -1004430296219, "text": card},
                                      headers={"Content-Type": "application/json"}, retries=1)
                    print(f"posted curated card: {st}", flush=True)
                    # ALSO deliver the card to the user's DM via the BOT token (same
                    # reliable path as call alerts; sendMessage never conflicts with
                    # gateway getUpdates). This is the autobuy trigger — the user sees
                    # the card as a normal bot message and can act (or the gateway relays).
                    st2, _ = http_json(f"https://api.telegram.org/bot{tok}/sendMessage",
                                       method="POST",
                                       body={"chat_id": 1223241788, "text": card},
                                       headers={"Content-Type": "application/json"}, retries=1)
                    print(f"sent curated card to DM via bot: {st2}", flush=True)
                # channel attribution: prefer a known call row; else parse from cards
                src_ch = None
                try:
                    import os as _os
                    cp = _os.path.join(DESK, "calls.jsonl")
                    for line in open(cp):
                        try:
                            cj = json.loads(line)
                            if cj.get("mint") == mint:
                                src_ch = cj.get("channel"); break
                        except Exception:
                            pass
                except Exception:
                    pass
                record_curated_card(mint, src_ch, rep, time.time())
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
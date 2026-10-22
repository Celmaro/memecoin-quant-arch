#!/usr/bin/env python3
"""HMQ group listener — the desk's live socket into the Telegram scan group.

Long-polls the Bot API, and for every group message:
  - extracts Solana CAs -> runs AEGIS -> posts verdict card back to the group
  - captures scan-bot replies (for the per-bot scoreboard, later)
  - handles commands from the owner: /scan, /positions, /momentum, /skip
Writes seen message ids to hmq_state.json (dedupe across restarts).
"""
import sys, os, json, time, re

sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import load_config, http_json, db
import aegis

STATE = os.path.expanduser("~/memecoin-quant/hmq_state.json")
MINT_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
OWNER = "treizodemayo"
SOL_MINT = "So11111111111111111111111111111111111111112"

def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"offset": 0, "group_id": None, "seen": []}

def save_state(s):
    json.dump(s, open(STATE, "w"), indent=1)

def api(tok, method, timeout=None, **params):
    st, d = http_json(f"https://api.telegram.org/bot{tok}/{method}", retries=2,
                      timeout=timeout,
                      method="POST" if params else "GET",
                      body=params if params else None,
                      headers={"Content-Type": "application/json"})
    return d

def valid_mint(s):
    return (32 <= len(s) <= 44 and s != SOL_MINT and not s.startswith("0x")
            and not s.isdigit())

def verdict_card(mint, rep):
    f = rep.get("flags") or []
    score = rep.get("score")
    lines = [f"🔍 Desk scan: {mint}"]
    if score is None:
        lines.append("Contract: N/A (no score returned)")
    else:
        qual = "clean" if score >= 50 else ("some red flags" if score >= 30 else "multiple red flags")
        lines.append(f"Contract: {score:.0f}/100 ({qual})")
    if f:
        lines.append("Flags: " + ", ".join(str(x) for x in f[:5]))
    return "\n".join(lines)

def handle_message(tok, state, msg, group_id):
    text = msg.get("text") or msg.get("caption") or ""
    frm = (msg.get("from") or {}).get("username") or "?"
    chat_id = (msg.get("chat") or {}).get("id")

    # owner commands
    if text.startswith("/"):
        cmd = text.split()[0].lower()
        arg = text.split()[1] if len(text.split()) > 1 else None
        if cmd == "/scan" and arg:
            mints = [m for m in MINT_RE.findall(arg) if valid_mint(m)]
            if mints:
                try:
                    rep = aegis.score_coin(mints[0], deep=False)
                    api(tok, "sendMessage", chat_id=group_id, text=verdict_card(mints[0], rep))
                except Exception as e:
                    api(tok, "sendMessage", chat_id=group_id, text=f"⚠ scan failed: {str(e)[:60]}")
            else:
                api(tok, "sendMessage", chat_id=group_id, text="Send a CA: /scan <mint>")
        elif cmd == "/momentum" and arg:
            ms = [m for m in MINT_RE.findall(arg) if valid_mint(m)]
            if ms:
                try:
                    import ws_momentum as wm
                    r = wm.momentum_report(ms[0], seconds=15)
                    api(tok, "sendMessage", chat_id=chat_id, text=r)
                except Exception as e:
                    api(tok, "sendMessage", chat_id=chat_id, text=f"⚠ momentum error: {str(e)[:60]}")
        elif cmd == "/positions":
            b = json.load(open(os.path.expanduser("~/memecoin-quant/paper_book.json")))
            lines = [f"{s}: ${p['size_usd']:.2f} @ {p['entry_px']}" for s, p in b["positions"].items()]
            api(tok, "sendMessage", chat_id=group_id,
                text="📒 Book: $" + f"{b.get('cash',0):.2f} cash | " + ("; ".join(lines) or "no positions"))
        return

    # CA detection from any member's message
    mints = [m for m in MINT_RE.findall(text) if valid_mint(m)]
    if mints:
        mint = mints[0]
        try:
            rep = aegis.score_coin(mint, deep=False)
            card = verdict_card(mint, rep)
            api(tok, "sendMessage", chat_id=chat_id, text=card)
            log = {"ts": time.time(), "msg_date": msg.get("date"), "mint": mint,
                   "from": frm, "score": rep.get("score")}
            with open(os.path.expanduser("~/memecoin-quant/hmq_scans.jsonl"), "a") as f:
                f.write(json.dumps(log) + "\n")
        except Exception as e:
            api(tok, "sendMessage", chat_id=chat_id, text=f"⚠ scan failed: {str(e)[:60]}")

def main():
    tok = load_config()["HMQ_BOT_TOKEN"]
    state = load_state()
    group_id = state.get("group_id") or -1004430296219
    state["group_id"] = group_id
    print(f"HMQ listener up. group={group_id}", flush=True)
    offset = state.get("offset", 0)
    while True:
        try:
            d = api(tok, "getUpdates", timeout=35, offset=offset,
                    allowed_updates=["message", "edited_message"])
            for u in d.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message") or {}
                chat = (msg.get("chat") or {})
                if chat.get("id") == group_id and msg.get("text"):
                    mid = msg.get("message_id")
                    key = f"{chat.get('id')}:{mid}"
                    if key not in state.get("seen", []):
                        state.setdefault("seen", []).append(key)
                        state["seen"] = state["seen"][-500:]
                        handle_message(tok, state, msg, group_id)
                save_state(state)
        except Exception as e:
            print(f"listener retry: {str(e)[:80]}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()

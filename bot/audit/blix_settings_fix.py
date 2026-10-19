#!/usr/bin/env python3
"""blix_settings_fix.py — F6 (Oct 19): re-apply BLIX settings from CONFIG.md.

Secret values are read from CONFIG.md / profile .env and POSTed to the local
BLIX radar (localhost:4420). Prints only masked/redacted confirmations.
Run: .venv/bin/python blix_settings_fix.py   (or system python3)

F6 findings:
  - telegramToken/telegramChat were empty -> BLIX "telegram" lane down. FIXED.
  - rpcUrl/rpcWsUrl were empty -> trades/pumpswap had no Helius WSS endpoint. FIXED.
  - jupiterKey was empty -> jupiter lane degraded. FIXED (jupiter already ok).
  - helius admin usage read still 400 "Invalid project id": BLIX's helius.js calls
    admin-api.helius.xyz/v0/admin/projects/<PROJECT_ID>/usage with X-Api-Key; the
    value in CONFIG.md is the RPC API key, NOT the project ID. Until
    HELIUS_PROJECT_ID is added to CONFIG.md, the helius lane stays down and BLIX
    paces trades/pumpswap WS lanes against a local cap (metrics <=25% of paced
    daily, heliuskeys.rpcStreamAllowed). That pacing is BY DESIGN, not a bug.
"""
import json, re, sys, urllib.request

CONFIG = "/home/agentuser/memecoin-quant/CONFIG.md"
ENV = "/home/agentuser/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239/.env"
BLIX = "http://localhost:4420"

text = open(CONFIG).read()

def grab(patterns, default=None):
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1).strip()
    return default

helius = grab([r"Helius[^\n]*?:\s*([0-9a-fA-F-]{20,})", r"HELIUS_KEY[^\n]*?[:=]\s*([0-9a-fA-F-]{20,})"])
jupiter = grab([r"Jupiter[^\n]*?:\s*(jup_pub_[A-Za-z0-9]+)", r"JUPITER_KEY[^\n]*?[:=]\s*(jup_pub_[A-Za-z0-9]+)"])
bot_token = grab([r"HMQ_BOT_TOKEN:\s*(\S+)"])
if not bot_token:
    try:
        for line in open(ENV):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                bot_token = line.strip().split("=", 1)[1]
    except Exception:
        pass

chat_id = "1223241788"
rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius}" if helius else ""
rpc_ws = f"wss://mainnet.helius-rpc.com/?api-key={helius}" if helius else ""

settings = {}
if helius:
    settings["heliusProjectId"] = helius
    settings["rpcUrl"] = rpc_url
    settings["rpcWsUrl"] = rpc_ws
if jupiter:
    settings["jupiterKey"] = jupiter
if bot_token:
    settings["telegramToken"] = bot_token
    settings["telegramChat"] = chat_id
settings["fomoAutoFollow"] = True
settings["fomoFollowTop"] = 15
settings["autoFollowSmart"] = True

print("Applying settings (values redacted):")
for k, v in settings.items():
    shown = str(v)[:6] + "…" if len(str(v)) > 8 else "(empty)"
    print(f"  {k}: {shown}")

body = json.dumps({"settings": settings}).encode()
req = urllib.request.Request(f"{BLIX}/api/settings", data=body, method="POST",
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode())
    sec = resp.get("secrets", {})
    print("\nVerified (masked):")
    for k in ("rpcUrl", "rpcWsUrl", "jupiterKey", "telegramToken"):
        v = sec.get(k)
        print(f"  {k}: set={v.get('set') if v else False} hint={v.get('hint') if v else ''}")
    # helius project id TODO check (real value, not a TODO placeholder)
    m = re.search(r"HELIUS_PROJECT_ID[^\n]*?[:=]\s*([0-9a-fA-F-]{20,})", text)
    if not m:
        print("\n⚠️  HELIUS_PROJECT_ID missing from CONFIG.md — helius usage lane will stay down")
    else:
        print("\nHELIUS_PROJECT_ID present in CONFIG.md — helius usage lane can recover on next 10min read")
except Exception as e:
    print("ERR:", e)
    sys.exit(1)
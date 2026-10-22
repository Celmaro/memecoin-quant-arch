#!/usr/bin/env python3
"""
stream_listener.py — MONITORING ONLY (HARD RULE: never generates buy signals).

Connects to keyless WS feeds (PumpDev, PumpAPI, optional Birdeye) and watches ONLY
mints that came from the curated Telegram list (positions.json + recent calls.jsonl).
Sub-second tape informs EXITS: whale sells, volume stalls, large trades.

Usage:
  python3 stream_listener.py [--seconds N] [--birdeye] [--pumpapi]
  (Ctrl+C to stop; run under a process manager for persistence)

No signal is ever emitted as an entry — the curated list remains the sole entry source.
"""
import json, os, sys, time, asyncio, argparse

DESK = os.path.expanduser("~/memecoin-quant")
POS_FILE = os.path.join(DESK, "positions.json")
CALLS_FILE = os.path.join(DESK, "calls.jsonl")
JOURNAL_DIR = os.path.join(DESK, "journal")

PUMPDEV_WS = "wss://pumpdev.io/ws"
PUMPAPI_WS = "wss://stream.pumpapi.io/"
BIRDEYE_WS = "wss://public-api.birdeye.so/socket"

WHALE_SELL_SOL = 1.0   # alert if a single sell > 1 SOL (≈ $100+)
WHALE_BUY_SOL = 2.0    # alert if single buy > 2 SOL


def watched_mints(cutoff_h=6):
    """Mints to monitor = positions + recent curated calls (ONLY those sources)."""
    mints = {}
    try:
        if os.path.exists(POS_FILE):
            pos = json.load(open(POS_FILE))
            for m, p in pos.items():
                mints[m] = f"POS {p.get('symbol','?')}"
    except Exception:
        pass
    try:
        if os.path.exists(CALLS_FILE):
            cutoff = time.time() - cutoff_h * 3600
            with open(CALLS_FILE) as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if e.get("ts", 0) >= cutoff and e.get("mint"):
                            mints.setdefault(e["mint"], f"CALL {e.get('symbol','?')}")
                    except Exception:
                        continue
    except Exception:
        pass
    return mints


def log_event(entry):
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    entry["ts"] = time.time()
    day = time.strftime("%Y-%m-%d")
    with open(os.path.join(JOURNAL_DIR, f"{day}.jsonl"), "a") as f:
        f.write(json.dumps(entry) + "\n")


async def run_pumpdev(mints, seconds, alerts):
    import websockets
    try:
        async with websockets.connect(PUMPDEV_WS, open_timeout=15) as ws:
            # subscribe to each watched mint's trades (monitoring only)
            for mint in list(mints)[:50]:
                try:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
                except Exception:
                    pass
            print(f"[stream] pumpdev subscribed {len(mints)} mints — {seconds}s")
            end = time.time() + seconds
            while time.time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                try:
                    ev = json.loads(msg)
                except Exception:
                    continue
                handle_pumpdev_event(ev, mints, alerts)
    except Exception as e:
        print(f"[stream] pumpdev error: {e}")


def handle_pumpdev_event(ev, mints, alerts):
    mint = ev.get("mint") or ""
    if mint not in mints:
        return
    typ = ev.get("type") or ""
    side = ev.get("side") or ev.get("txType") or ""
    sol = float(ev.get("solAmount") or 0)
    px = ev.get("price") or ev.get("marketCapSol") or 0
    # PumpDev event shapes vary; treat explicit sell/large-buy as tape signals
    if "sell" in str(side).lower() and sol >= WHALE_SELL_SOL:
        alerts.append(f"⚠ {mints[mint]} WHALE SELL {sol:.2f} SOL ({mint[:10]}...)")
        log_event({"event": "STREAM_WHALE_SELL", "mint": mint, "sol": sol, "px": px})
    elif "buy" in str(side).lower() and sol >= WHALE_BUY_SOL:
        alerts.append(f"🐋 {mints[mint]} BIG BUY {sol:.2f} SOL ({mint[:10]}...)")
        log_event({"event": "STREAM_BIG_BUY", "mint": mint, "sol": sol, "px": px})


async def run_pumpapi(mints, seconds, alerts):
    import websockets
    try:
        async with websockets.connect(PUMPAPI_WS, open_timeout=15) as ws:
            print(f"[stream] pumpapi connected — {seconds}s (filtering client-side)")
            end = time.time() + seconds
            while time.time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                try:
                    ev = json.loads(msg)
                except Exception:
                    continue
                mint = ev.get("mint") or (ev.get("token") or {}).get("mint") or ""
                if mint not in mints:
                    continue
                typ = ev.get("txType") or ev.get("type") or ""
                amt = float(ev.get("solAmount") or ev.get("amount") or 0)
                if "sell" in str(typ).lower() and amt >= WHALE_SELL_SOL:
                    alerts.append(f"⚠ {mints[mint]} PUMPAPI SELL {amt:.2f} SOL ({mint[:10]}...)")
                    log_event({"event": "STREAM_WHALE_SELL", "mint": mint, "sol": amt, "src": "pumpapi"})
    except Exception as e:
        print(f"[stream] pumpapi error: {e}")


async def run_birdeye(mints, seconds, alerts):
    import websockets
    try:
        async with websockets.connect(BIRDEYE_WS, open_timeout=15) as ws:
            await ws.send(json.dumps({"type": "SUBSCRIBE_LARGE_TRADE_TXS", "data": {"chain": "solana"}}))
            print(f"[stream] birdeye large-trade feed connected — {seconds}s")
            end = time.time() + seconds
            while time.time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                try:
                    ev = json.loads(msg)
                except Exception:
                    continue
                if ev.get("type") == "LARGE_TRADE_TXS" or ev.get("event") == "large_trade":
                    m = ev.get("data", {}).get("mint") or ev.get("data", {}).get("tokenAddress") or ""
                    side = ev.get("data", {}).get("side") or ""
                    if m in mints and "sell" in str(side).lower():
                        alerts.append(f"⚠ {mints[m]} BIRDEYE large sell ({m[:10]}...)")
                        log_event({"event": "STREAM_WHALE_SELL", "mint": m, "src": "birdeye"})
    except Exception as e:
        print(f"[stream] birdeye error: {e}")


async def main():
    ap = argparse.ArgumentParser(description="Post-buy WS monitor (never a signal source)")
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--birdeye", action="store_true")
    ap.add_argument("--pumpapi", action="store_true")
    args = ap.parse_args()

    mints = watched_mints()
    if not mints:
        print("No positions / recent curated calls to monitor. Nothing to watch.")
        return
    # WS feeds rate-limit subscription count (PumpDev 1008 policy violation at 67);
    # monitor only positions first, then most-recent calls, capped.
    MAX_SUB = 25
    sub_mints = list(mints)[:MAX_SUB]
    print(f"[stream] watching {len(sub_mints)}/{len(mints)} curated-sourced mint(s) (cap {MAX_SUB} on WS): "
          f"{', '.join(sub_mints[:6])}...")

    alerts = []
    tasks = [run_pumpdev(sub_mints, args.seconds, alerts)]
    if args.pumpapi:
        tasks.append(run_pumpapi(mints, args.seconds, alerts))
    if args.birdeye:
        tasks.append(run_birdeye(mints, args.seconds, alerts))
    await asyncio.gather(*tasks, return_exceptions=True)

    if alerts:
        print("\n=== ALERTS (monitoring only — decisions are yours) ===")
        for a in alerts:
            print(a)
    else:
        print("\nNo whale activity on watched mints during window.")


if __name__ == "__main__":
    asyncio.run(main())
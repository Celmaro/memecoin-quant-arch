#!/usr/bin/env python3
"""WS trade monitor — real-time exit eyes for open positions.

Channels give the entry signal; this daemon subscribes (PumpDev WS, keyless) to
every open position's mint and evaluates exit conditions tick-by-tick.

Writes alerts to ws_alerts.jsonl + prints them; desk-tick/paper-tick surface them.
Run as a background daemon: python3 ws_monitor.py [--interval-check 30]
"""
import sys, os, json, time, socket, threading

sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
from desk_common import dex_pair, log_event
import position_monitor as pm

PUMPDEV_WS = "wss://pumpdev.io/ws"
ALERTS_FILE = os.path.expanduser("~/memecoin-quant/ws_alerts.jsonl")
STATE_FILE = os.path.expanduser("~/memecoin-quant/ws_state.json")

def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"active_subs": [], "last_ping": 0}

def save_state(s):
    json.dump(s, open(STATE_FILE, "w"), indent=1)

def alert(sym, mint, kind, detail):
    entry = {"ts": time.time(), "symbol": sym, "mint": mint, "kind": kind, "detail": detail}
    with open(ALERTS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log_event({"event": f"WS_{kind}", "mint": mint, "symbol": sym, **detail})
    print(f"WS-ALERT {kind} ${sym}: {detail}", flush=True)

def trade_stats_window(trades, window_s=60):
    """Aggregate trades in last N seconds -> buy/sell pressure + net flow."""
    now = time.time()
    recent = [t for t in trades if now - t["ts"] <= window_s]
    buys = sum(t["usd"] for t in recent if t["side"] == "buy")
    sells = sum(t["usd"] for t in recent if t["side"] == "sell")
    n = len(recent)
    return {"buy_usd": round(buys, 2), "sell_usd": round(sells, 2),
            "ratio": round(buys / (buys + sells), 3) if buys + sells else None, "n": n}

def evaluate(mint, sym, trades):
    """Real-time exit signals from the live tape."""
    pos = pm.load_positions().get(mint)
    if not pos:
        return None
    stats = trade_stats_window(trades, 60)
    if stats["n"] < 3:
        return None  # not enough tape
    ratio = stats["ratio"]
    # 1) sell avalanche
    if ratio is not None and ratio < 0.3 and stats["sell_usd"] > 800:
        alert(sym, mint, "SELL_AVALANCHE", stats)
        return "EXIT"
    # 2) absorption: heavy buys, price flat over 5m -> whales exiting into retail
    px0 = trades[-40]["px"] if len(trades) >= 40 else None
    px1 = trades[-1]["px"] if trades else None
    if px0 and px1 and ratio is not None and ratio > 0.65 and abs(px1 / px0 - 1) < 0.02 and stats["buy_usd"] > 1500:
        alert(sym, mint, "ABSORPTION", stats)
        return "WATCH"
    # 3) momentum confirmation (good sign — nothing to do)
    if ratio is not None and ratio > 0.7 and stats["buy_usd"] > 2000:
        alert(sym, mint, "MOMENTUM", stats)
    return None

def ws_loop():
    """Connect to PumpDev WS (keyless), subscribe to open position mints, consume trades.

    Idles (sleeps, does NOT hammer the socket) when there are no open positions
    — the keyless tier 429s on rapid empty reconnect loops.
    """
    import websocket  # websocket-client
    from desk_common import load_config
    key = load_config().get("PUMPDEV_KEY") or ""
    url = PUMPDEV_WS + (f"?key={key}" if key else "")
    state = load_state()
    while True:
        pos = pm.load_positions()
        mints = list(pos.keys())
        if not mints:
            # no positions -> nothing to monitor. idle quietly, re-check every 60s
            state["active_subs"] = []
            save_state(state)
            time.sleep(60)
            continue
        try:
            # WS is keyless per ToolStacks; fall back to keyed URL if pumpdev adds auth
            ws = websocket.create_connection(PUMPDEV_WS, timeout=10)
            # subscribe to each mint's trade feed
            for m in mints:
                ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [m]}))
            state["active_subs"] = mints
            save_state(state)
            trades = {}  # mint -> list of trade dicts
            last_eval = 0
            while True:
                # resubscribe if positions changed
                cur = list(pm.load_positions().keys())
                if set(cur) != set(state["active_subs"]):
                    break
                try:
                    msg = json.loads(ws.recv())
                except Exception:
                    break
                d = msg.get("data") or msg
                mint = d.get("mint") or d.get("token")
                if not mint or mint not in mints:
                    continue
                tx_type = str(d.get("txType") or d.get("side") or "").lower()
                side = "buy" if "buy" in tx_type else "sell"
                # USD value: quote token is SOL -> use solAmount*~SOL px if no usd field
                usd = float(d.get("usdAmount") or d.get("usd_amount") or 0)
                if not usd:
                    sol = float(d.get("solAmount") or d.get("sol_amount") or 0)
                    usd = sol * 103.0  # SOL approx (ToolStacks: ~$103)
                px = float(d.get("price") or d.get("priceUsd") or 0)
                trades.setdefault(mint, []).append({"ts": time.time(), "side": side, "usd": usd, "px": px})
                # keep last 500 trades per mint
                trades[mint] = trades[mint][-500:]
                # evaluate every 10s
                if time.time() - last_eval > 10:
                    last_eval = time.time()
                    for m in mints:
                        if m in trades:
                            evaluate(m, pos.get(m, {}).get("symbol", "?"), trades[m])
        except Exception as e:
            # 429 backoff: pumpdev rate-limits rapid reconnects
            wait = 30 if "429" in str(e) else 5
            print(f"WS reconnect in {wait}s ({str(e)[:60]})", flush=True)
            time.sleep(wait)

if __name__ == "__main__":
    print("WS monitor starting — subscribing to open positions...", flush=True)
    ws_loop()

import sys, os, json, time
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))

"""WS momentum sampler — collects live trades from PumpDev for a mint and
returns human-readable momentum. Used to enrich AEGIS with real-time tape."""

PUMPDEV_WS = "wss://pumpdev.io/ws"
SOL_USD = 103.0

def sample_trades(mint, seconds=30, max_trades=300):
    """Connect to PumpDev WS and collect trades for `mint` for N seconds."""
    import websocket
    from desk_common import load_config
    key = load_config().get("PUMPDEV_KEY") or ""
    url = PUMPDEV_WS + (f"?key={key}" if key else "")
    trades = []
    try:
        ws = websocket.create_connection(url, timeout=15)
        ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
        t0 = time.time()
        while time.time() - t0 < seconds and len(trades) < max_trades:
            try:
                m = json.loads(ws.recv())
            except Exception:
                break
            if "signature" in m and m.get("mint") == mint:
                tx = str(m.get("txType") or "").lower()
                side = "buy" if "buy" in tx else "sell"
                sol = float(m.get("solAmount") or 0)
                trades.append({"ts": time.time(), "side": side,
                               "usd": sol * SOL_USD,
                               "mc_sol": float(m.get("marketCapSol") or 0)})
        ws.close()
    except Exception as e:
        return trades, str(e)[:60]
    return trades, None

def momentum_report(mint, seconds=30):
    """Human-readable momentum read from the live tape."""
    trades, err = sample_trades(mint, seconds)
    if err:
        return f"⚠ Live tape unavailable ({err}) — falling back to polled data."
    if not trades:
        return "🔇 Live tape: ZERO trades in the last 30s — nobody is trading. Momentum: DEAD."
    buys = [t for t in trades if t["side"] == "buy"]
    sells = [t for t in trades if t["side"] == "sell"]
    buy_usd = sum(t["usd"] for t in buys)
    sell_usd = sum(t["usd"] for t in sells)
    total = buy_usd + sell_usd
    ratio = buy_usd / total if total else 0
    last_mc_sol = trades[-1]["mc_sol"]
    mc_usd = last_mc_sol * SOL_USD if last_mc_sol else 0
    lines = []
    lines.append(f"📊 Live tape ({len(trades)} trades in {seconds}s):")
    lines.append(f"   • Buyers put in ${buy_usd:,.0f}, sellers took out ${sell_usd:,.0f}")
    if ratio >= 0.7:
        lines.append(f"   • Verdict: STRONG BUYING — {ratio*100:.0f}% of the money is flowing IN. Momentum: 🔥 HOT")
    elif ratio >= 0.55:
        lines.append(f"   • Verdict: buyers in control — {ratio*100:.0f}% inflow. Momentum: ✅ HEALTHY")
    elif ratio >= 0.45:
        lines.append(f"   • Verdict: balanced tug-of-war ({ratio*100:.0f}% inflow). Momentum: 😐 NEUTRAL")
    elif ratio >= 0.3:
        lines.append(f"   • Verdict: sellers winning — only {ratio*100:.0f}% inflow. Momentum: ⚠️ WEAKENING")
    else:
        lines.append(f"   • Verdict: DUMPING — {100-ratio*100:.0f}% of the money is flowing OUT. Momentum: 🚨 EXIT TERRITORY")
    if mc_usd:
        lines.append(f"   • Live market cap: ${mc_usd:,.0f}")
    if buys:
        big = max(buys, key=lambda t: t["usd"])
        lines.append(f"   • Biggest buy: ${big['usd']:,.0f}")
    if sells:
        big = max(sells, key=lambda t: t["usd"])
        lines.append(f"   • Biggest sell: ${big['usd']:,.0f}")
    return "\n".join(lines)

if __name__ == "__main__":
    mint = sys.argv[1] if len(sys.argv) > 1 else None
    secs = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    if not mint:
        print("usage: ws_momentum.py <mint> [seconds]"); sys.exit(1)
    print(momentum_report(mint, secs))

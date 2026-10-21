#!/usr/bin/env python3
"""pumpdata_feed.py — live market cache daemon for the paper desk.

Consumes the free keyless PumpData websocket (sol.shrine.trade, see ToolStacks.md
PumpData entry) and maintains a small atomic JSON cache of the desk's WATCHLIST
mints (open paper positions + recent curated calls). paper_tick reads this cache
as its PRIMARY market source and falls back to DexScreener/Gecko when absent or
stale — killing the 429s / wrong-pool / dead-DNS fragility of polling.

Feed: socket.io subscribe_stream (protocols PUMPFUN+PUMPSWAP). We receive every
trade on the network and keep only mints in the watchlist (typically < 30).
Each watched mint's row carries the SAME fields paper_tick's dex_pair dict uses:
  priceUsd, marketCap, liquidity.usd, priceChange.m5, txns.m5.{buys,sells},
  volume.m5, plus px_sol/reserves for future constant-product fills.

Write cadence: every 5s (or on any watched-mint change), atomic rename.
Reconnect: socket.io auto-reconnect; sinceSlot = last seen block so nothing is
lost across a drop (24h replay per docs).

Graceful degradation: on ANY exception the daemon keeps retrying and the cache
file simply goes stale — paper_tick then falls back to dex_pair. The live
paper_book.json is never touched here.
"""
import os, sys, json, time, threading, collections, urllib.request, urllib.error

DESK = os.path.expanduser("~/memecoin-quant")
CACHE = os.path.join(DESK, "pumpdata_cache.json")
BOOK = os.path.join(DESK, "paper_book.json")
CALLS = os.path.join(DESK, "calls.jsonl")
SOCKET_URL = "https://sol.shrine.trade"
SOL_PRICE_URL = "https://sol.shrine.trade/sol-price"
WATCH_REFRESH_S = 30          # re-read book+calls for watchlist every 30s
WRITE_EVERY_S = 5             # flush cache every 5s if changed
FRESH_CALL_S = 3600           # keep calls in watchlist for 1h
MAX_MINTS = 400               # cap total tracked (should stay < 40)

def _log(msg, *args):
    """Log with optional printf-style args (Oct 21 fix): callers use
    _log('cycle err: %s', str(ex)) — a single-arg lambda crashed the feed
    whenever a cycle raised, and the supervisor kept restarting a dead child."""
    try:
        if args:
            print(f"[{time.strftime('%H:%M:%S')}] {msg % args}", flush=True)
        else:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    except Exception:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

_watch = set()                # mints to track
_state = {}                   # mint -> market dict
_last_block = 0               # last seen Solana slot for sinceSlot resume
_changed = False
_cache_lock = threading.Lock()


def read_watchlist():
    """Open paper positions (mint keys) + recent curated calls -> mint set."""
    out = set()
    try:
        b = json.load(open(BOOK))
        if isinstance(b.get("positions"), dict):
            for mint in b["positions"].keys():
                if isinstance(mint, str) and len(mint) > 20:
                    out.add(mint)
    except Exception:
        pass
    try:
        t0 = time.time()
        with open(CALLS) as f:
            for line in f:
                try:
                    c = json.loads(line)
                except Exception:
                    continue
                m = c.get("mint")
                if not isinstance(m, str) or len(m) < 20:
                    continue
                ts = c.get("ts") or 0
                if isinstance(ts, (int, float)) and ts > 1000000000 and (t0 - ts) < FRESH_CALL_S:
                    out.add(m)
    except Exception:
        pass
    return out


def sol_price():
    try:
        req = urllib.request.Request(SOL_PRICE_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode())
        v = float(d.get("priceUSD") or 0)
        return v if v > 0 else None
    except Exception:
        return None


_SOL_USD = 104.0


def apply_trade(e):
    """Update a watched mint's market row from a stream trade event."""
    global _changed
    mint = e.get("mint")
    if mint not in _watch:
        return
    ts_ms = e.get("localTimestamp") or (e.get("timestamp") or 0) * 1000
    px = e.get("price")
    B = e.get("tokensInPool")
    Q = e.get("quoteInPool")
    mcap_q = e.get("marketCapQuote")
    act = e.get("action")
    qa = e.get("quoteAmount")
    blk = e.get("block")
    if blk:
        global _last_block
        _last_block = max(_last_block, int(blk))
    if not px or float(px) <= 0:
        return
    px = float(px)
    with _cache_lock:
        row = _state.setdefault(mint, {})
        row["px_sol"] = px
        row["px_usd"] = px * _SOL_USD
        row["t"] = ts_ms
        if isinstance(B, (int, float)) and isinstance(Q, (int, float)):
            row["B"] = float(B)
            row["Q"] = float(Q)
            row["liq_usd"] = 2 * float(Q) * _SOL_USD       # constant-product total liq
        if isinstance(mcap_q, (int, float)) and mcap_q > 0:
            row["mc_usd"] = float(mcap_q) * _SOL_USD       # quote is SOL for our coins
        # rolling 1h windows: (ts_ms, side, quoteAmount) for volume; price samples for chg_m5
        hist = row.setdefault("_hist", [])
        pxs = row.setdefault("_pxs", [])
        if isinstance(ts_ms, (int, float)):
            hist.append((ts_ms, act, float(qa) if isinstance(qa, (int, float)) else 0.0))
            pxs.append((ts_ms, px))
        cutoff1h = (ts_ms or 0) - 3_600_000
        while hist and hist[0][0] < cutoff1h:
            hist.pop(0)
        while pxs and pxs[0][0] < cutoff1h:
            pxs.pop(0)
        if hist:
            w5 = [h for h in hist if h[0] >= (ts_ms - 300_000)]
            b5 = sum(1 for h in w5 if h[1] == "buy")
            s5 = sum(1 for h in w5 if h[1] == "sell")
            v5 = sum(h[2] for h in w5 if h[2] > 0) * _SOL_USD
            v1h = sum(h[2] for h in hist if h[2] > 0) * _SOL_USD
            row["b5"], row["s5"], row["vol_m5_usd"], row["vol_h1_usd"] = b5, s5, v5, v1h
        if pxs:
            # chg_m5 = vs the first sample at least 300s older (or oldest if newer coin)
            old = None
            for s in pxs:
                if s[0] <= (ts_ms - 300_000):
                    old = s
            if old is None:
                old = pxs[0]
            row["chg_m5"] = (px / old[1] - 1.0) * 100.0 if old[1] > 0 else None
    _changed = True


def write_cache():
    """Atomic JSON cache: {updated, sol_usd, mints:{mint: market}}."""
    global _changed
    with _cache_lock:
        if not _state:
            return
        body = {"updated": time.time(), "sol_usd": _SOL_USD,
                "mints": {m: {k: v for k, v in row.items() if not k.startswith("_")}
                          for m, row in _state.items()}}
        tmp = CACHE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(body, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CACHE)
            _changed = False
        except Exception:
            pass


def run():
    global _watch, _SOL_USD, _changed
    import socketio

    def make_client():
        """FRESH client each cycle: auto-reconnect stacks zombie connections and
        the API allows ONE stream per IP — a second connection from our address
        is rejected (too_many_connections). Manual connect/disconnect per cycle
        guarantees exactly one live socket at any time. (Oct 20 fix.)"""
        sio = socketio.Client(reconnection=False, reconnection_attempts=0)
        seen = {"n": 0, "last": time.time(), "last_evt": time.time()}

        @sio.on("connect")
        def on_connect():
            _log(f"connected — watch {len(_watch)} mints, sinceSlot={_last_block}")
            seen["last_evt"] = time.time()
            def ack(resp):
                if isinstance(resp, dict) and resp.get("error"):
                    _log(f"sub ack err: {str(resp)[:120]}")
            sio.emit("subscribe_stream",
                     {"protocols": ["PUMPFUN", "PUMPSWAP"],
                      "sinceSlot": _last_block or None}, callback=ack)
            # subscribe to SOL price too (keyless): keeps the write queue fed so
            # engineio's write loop never aborts on 30s idle, AND gives us a
            # live SOL rate from the stream.
            sio.emit("subscribe_sol_price")

        @sio.on("stream")
        def on_stream(e):
            seen["n"] += 1
            seen["last_evt"] = time.time()
            try:
                apply_trade(e)
            except Exception:
                pass
            now = time.time()
            if now - seen["last"] >= 60:
                _log(f"alive — {seen['n']} events/min, watch {len(_watch)}, rows {len(_state)}")
                seen["n"], seen["last"] = 0, now

        @sio.on("*")
        def on_any(ev, data):
            if ev not in ("stream", "connect", "disconnect", "sol_price"):
                _log(f"ev {ev}: {str(data)[:120]}")

        return sio, seen

    # refresh SOL/USD periodically (free REST)
    def _sol_loop():
        global _SOL_USD, _changed
        while True:
            v = sol_price()
            if v:
                _SOL_USD = v
                _changed = True
            time.sleep(120)

    threading.Thread(target=_sol_loop, daemon=True).start()

    # main loop: one clean connection per cycle; refresh watchlist; write cache
    while True:
        _watch = read_watchlist() or _watch
        if len(_watch) > MAX_MINTS:
            _watch = set(list(_watch)[:MAX_MINTS])
        sio = None
        try:
            sio, seen = make_client()
            sio.connect(SOCKET_URL, wait_timeout=12)
            last_ka = 0
            while sio.connected:
                write_cache()
                time.sleep(1.0)
                # keepalive (Oct 20): engineio's write loop self-destructs after
                # 30s with an idle write queue, which kills the read loop too.
                # A periodic emit keeps the queue fed and the connection alive
                # (subscribe_sol_price is keyless + gives a live SOL rate).
                if time.time() - last_ka > 15:
                    sio.emit("subscribe_sol_price")
                    last_ka = time.time()
                # stall watchdog (Oct 20): the transport can die silently
                # ("packet queue is empty" without a disconnect event) — if no
                # stream event for 60s, break out and force a fresh cycle.
                if time.time() - seen["last_evt"] > 60:
                    _log("stall watchdog — no stream event for 60s, reconnecting")
                    break
        except Exception as ex:
            _log("cycle err: %s", str(ex)[:120] if str(ex) else type(ex).__name__)
        finally:
            try:
                if sio is not None:
                    sio.disconnect()
            except Exception:
                pass
        write_cache()
        time.sleep(3)


if __name__ == "__main__":
    _log("pumpdata_feed starting (watchlist from book+calls)")
    run()
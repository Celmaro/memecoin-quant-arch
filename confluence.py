"""confluence.py — cross-channel confluence detector (Oct 19).

The listener dedupes calls.jsonl by mint (first sighting wins), so the 2nd/3rd
channel posting the SAME mint never reaches the pipeline as a separate row —
the old `d.get("confluence")`/`d["channels"]` fields were always empty.

This module reads the FULL ledger (telegram_messages.jsonl) which records every
parsed mint per channel, and answers: "which curated channels have mentioned
this mint, and when?" in a configurable window. Pure stdlib, no network.
"""
import json, os, time, calendar

DESK = os.path.expanduser("~/memecoin-quant")
LEDGER = os.path.join(DESK, "telegram_messages.jsonl")

_cache = {"ts": 0.0, "map": {}}


def _ledger_ts(r):
    """Ledger `dt` is ISO-8601 UTC (suffix +00:00). Parse as UTC epoch —
    time.mktime would assume LOCAL time and shift rows ~8-16h out of window."""
    for field in ("dt", "edit_ts"):
        if r.get(field):
            s = str(r[field])[:19]
            try:
                return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                pass
    return None


def _load(force=False):
    """Load ledger rows once per 60s: mint -> {channel: last_ts}."""
    now = time.time()
    if not force and now - _cache["ts"] < 60 and _cache["map"]:
        return _cache["map"]
    mint_map = {}
    try:
        with open(LEDGER) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                mints = set(r.get("mints") or [])
                if r.get("dominant_url_mint"):
                    mints.add(r["dominant_url_mint"])
                if not mints:
                    continue
                ts = _ledger_ts(r)
                if ts is None:
                    continue
                ch = r.get("channel")
                if not ch:
                    continue
                for m in mints:
                    if len(m) < 32:
                        continue
                    d = mint_map.setdefault(m, {})
                    if ts > d.get(ch, 0):
                        d[ch] = ts
    except Exception:
        pass
    _cache["map"] = mint_map
    _cache["ts"] = now
    return mint_map


def confluence(mint, window_s=1800):
    """Curated channels that mentioned `mint` within `window_s`.

    Returns (channels: sorted list, span_s: first->last gap, first_ts, last_ts)
    or (None, 0, 0, 0) when the mint has no multi-channel mention.
    """
    mint_map = _load()
    d = mint_map.get(mint)
    if not d:
        return None, 0, 0, 0
    now = time.time()
    relevant = {ch: ts for ch, ts in d.items() if now - ts <= window_s}
    if len(relevant) < 2:
        # single channel recent — still report it (let caller decide boost)
        return None, 0, 0, 0
    chans = sorted(relevant, key=relevant.get)
    times = [relevant[c] for c in chans]
    return chans, times[-1] - times[0], times[0], times[-1]


def confluence_count(mint, window_s=1800):
    """Number of distinct curated channels mentioning this mint in the window."""
    chans, span, _, _ = confluence(mint, window_s)
    return len(chans) if chans else 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: confluence.py <mint> [window_s]")
        sys.exit(1)
    mint = sys.argv[1]
    win = int(sys.argv[2]) if len(sys.argv) > 2 else 1800
    chans, span, ft, lt = confluence(mint, win)
    if chans:
        print(f"{mint[:16]} — {len(chans)} channels in {win}s window (span {span/60:.0f}m):")
        for ch in chans:
            print(f"   {ch}  {time.strftime('%H:%M:%S', time.localtime(ft))}")
    else:
        print(f"{mint[:16]} — no multi-channel confluence in {win}s")
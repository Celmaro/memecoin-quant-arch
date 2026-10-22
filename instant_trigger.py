#!/usr/bin/env python3
"""instant_trigger.py <mint> <channel> — fired by telethon_listener the moment a
curated call lands. Sends the rich AEGIS card to the DM immediately (no 1m cron
lag) and triggers paper entry. Lock + consumed-marking prevents double-send when
the 1m cron also picks the call up (cron remains the safety net)."""
import json, os, sys, time, threading

DESK = os.path.expanduser("~/memecoin-quant")
HERE = os.path.dirname(os.path.abspath(__file__))
# Oct 21 latency fix: a single lock serialized ALL deep cards — burst signals
# queued 3-6 min behind the previous cold scan (user: "cards now 3-4 minutes
# late"). Replaced with a bounded slot pool so up to N deep cards run
# concurrently; per-mint dedupe (instant_sent) still prevents double-sends.
MAX_CONCURRENT = 3
LOCK_PREFIX = os.path.join(DESK, ".instant_trigger.lock")
SENT = os.path.join(DESK, "notifier_state.json")
sys.path.insert(0, DESK)
sys.path.insert(0, HERE)


def _claim_slot():
    """Claim one of MAX_CONCURRENT lock slots (pid-stamped, stale-healing).
    Returns the slot path, or None after 360s (caller retries later)."""
    deadline = time.time() + 360
    while time.time() < deadline:
        for i in range(MAX_CONCURRENT):
            path = f"{LOCK_PREFIX}.{i}"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return path
            except FileExistsError:
                # stale-heal: pid dead or file older than 10 min
                stale = False
                try:
                    holder = int(open(path).read().strip() or 0)
                    os.kill(holder, 0)
                except FileNotFoundError:
                    stale = True
                except ProcessLookupError:
                    stale = True
                except (ValueError, PermissionError):
                    try:
                        stale = time.time() - os.path.getmtime(path) > 600
                    except Exception:
                        stale = True
                except Exception:
                    stale = True
                if stale:
                    try:
                        os.remove(path)
                        continue
                    except FileNotFoundError:
                        continue
                    except Exception:
                        pass
            except Exception:
                pass
        time.sleep(2)
    return None


def main():
    if len(sys.argv) < 2:
        return 1
    mint = sys.argv[1]
    slot = _claim_slot()
    if slot is None:
        # Do not claim success: leave a clear retry marker for diagnosis.
        print(f"instant_trigger queued/delayed: all {MAX_CONCURRENT} slots busy", file=sys.stderr)
        return 2
    try:
        # consumed check: was this mint already carded?
        sys.path.insert(0, "/home/agentuser/.hermes/profiles/profile-b4632e6cdb854166b696555ae7ff4239/scripts")
        from call_notifier import deep_aegis_card, send_dm, load_state, save_state
        # mark consumed BEFORE the slow AEGIS build so the 1m cron never double-sends
        st = load_state()
        st.setdefault("instant_sent", [])
        if mint[:14] in st["instant_sent"][-200:]:
            return 0
        st["instant_sent"].append(mint[:14])   # normalize: dedupe lookup uses raw mint
        st["instant_sent"] = [m[:14] for m in st["instant_sent"] if m]
        st["instant_sent"] = st["instant_sent"][-200:]
        save_state(st)

        # find the call record (symbol/mc/ts) from calls.jsonl
        sym, mc, ts = None, None, None
        try:
            with open(os.path.join(DESK, "calls.jsonl")) as f:
                for line in reversed(f.readlines()[-200:]):
                    c = json.loads(line)
                    if c.get("mint") == mint:
                        sym = c.get("symbol")
                        mc = c.get("mc")
                        ts = c.get("ts")
                        break
        except Exception:
            pass

        card, score, verdict = deep_aegis_card(mint, sys.argv[2] if len(sys.argv) > 2 else "?", sym, mc)
        if not card:
            card = f"📢 New call: ${sym or '?'} ({sys.argv[2] if len(sys.argv) > 2 else '?'})\n   CA: {mint}\n   (AEGIS scan failed — cron will follow up)"
        out = [card]

        # immediate paper entry (same $10K gate as cron path); pass ts so
        # entry_call's freshness gate + age label see the REAL call time.
        try:
            from paper_tick import entry_call
            bought, skip = entry_call({"mint": mint, "symbol": sym, "mc": mc, "ts": ts}, aegis_score=score)
            if bought:
                out.append(bought)
            elif skip:
                out.append(f"⏭ skipped: {skip} (${sym or mint[:8]})")
        except Exception as e:
            out.append(f"⚠ auto-buy error {mint[:8]}: {str(e)[:60]}")

        send_dm("\n\n".join(out))
        return 0
    finally:
        try:
            os.remove(slot)
            # also drop any stale legacy single-lock file from the pre-slot era
            try:
                os.remove(os.path.join(DESK, ".instant_trigger.lock"))
            except Exception:
                pass
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

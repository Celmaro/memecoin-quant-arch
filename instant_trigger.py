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
        # 4th audit 4.3 (Oct 23): the OLD code appended to instant_sent BEFORE the
        # slow AEGIS build + DM send. A crash/provider hang between the state write
        # and the send made later runs believe the card was already delivered —
        # at-most-once mistaken for reliable delivery. Now: claim an IN_PROGRESS
        # lease (with expiry) for concurrent dedupe, and only commit CARD_SENT
        # AFTER Telegram confirms. A stale IN_PROGRESS lease (crash) expires and
        # is reclaimed by the next run; entry idempotency is independent and stays
        # in paper_tick's consumed ledger.
        st = load_state()
        st.setdefault("instant_sent", [])
        st.setdefault("instant_in_progress", {})   # mint14 -> lease_expiry_ts
        now_t = time.time()
        _in_prog = st["instant_in_progress"]
        # reclaim crashed leases older than 15 min
        for _k in [k for k, v in _in_prog.items() if (v or 0) < now_t - 900]:
            _in_prog.pop(_k, None)
        if mint[:14] in st["instant_sent"][-200:]:
            return 0
        if mint[:14] in _in_prog:
            return 0  # another worker is mid-flight (lease alive)
        _in_prog[mint[:14]] = now_t + 900  # 15-min lease
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

        ok = send_dm("\n\n".join(out))
        if ok:
            # COMMIT only after Telegram confirmed — the card is truly delivered.
            st = load_state()  # reload: another worker may have saved since
            st.setdefault("instant_sent", [])
            st.setdefault("instant_in_progress", {})
            st["instant_sent"] = [m[:14] for m in st["instant_sent"] + [mint[:14]] if m][-200:]
            st["instant_in_progress"].pop(mint[:14], None)
            save_state(st)
        else:
            # send failed — leave a fresh lease so a retry is possible and the
            # cron notifier can re-card. Drop the claim from instant_sent.
            st = load_state()
            st.setdefault("instant_in_progress", {})
            st["instant_in_progress"].pop(mint[:14], None)
            save_state(st)
            print("instant_trigger: DM send FAILED — card NOT committed; retry possible", file=sys.stderr)
        return 0
    finally:
        # release the concurrency slot (not the delivery lease — that was handled
        # above, or expires) so a fresh claim can run
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

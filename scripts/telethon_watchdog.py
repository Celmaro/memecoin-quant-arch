#!/usr/bin/env python3
"""telethon_watchdog.py — restart the MTProto listener if it died. Cron no_agent (5m).

Silent when healthy (empty stdout -> no Telegram delivery).
Prints a line ONLY when it (re)started something or a restart failed, so the
cron delivers only real events to the user's DM.

Liveness = pidfile (~/memecoin-quant/telethon_listener.pid) + kill(pid,0).
No pgrep -> immune to command-line self-matches.

Guards:
- restarts detached from the cron process (new session) so it survives
- verifies the restart actually stayed up 8s; reports failure instead of lying
"""
import os, signal, subprocess, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
LISTENER = os.path.join(DESK, "telethon_listener.py")
PY = "/home/agentuser/memecoin-quant/.venv/bin/python"
LOG = "/tmp/telethon_listener.log"
PID_FILE = os.path.join(DESK, "telethon_listener.pid")
STOP_FLAG = os.path.join(DESK, "telethon_listener.off")


def listener_alive():
    """True only if pidfile exists and that pid is alive and ours."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IOError):
        return False


def main():
    if os.path.exists(STOP_FLAG):
        # user asked to stop: never resurrect while the flag is present
        return 0
    if listener_alive():
        return 0  # healthy — stay silent

    # dead -> restart detached from cron so it survives the cron process exit
    try:
        logf = open(LOG, "a")
        logf.write(f"\n===== watchdog restart {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        logf.flush()
        proc = subprocess.Popen([PY, LISTENER, "run"],
                                cwd=DESK,
                                stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                start_new_session=True)
        time.sleep(8)
        if listener_alive():
            print(f"\U0001f6e0\ufe0f telethon listener restarted (pid {proc.pid}) \u2014 was dead. Log: {LOG}")
            return 0
        print(f"\u274c telethon listener restart FAILED (exited rc={proc.poll()}). Log: {LOG}")
        return 1
    except Exception as e:
        print(f"\u274c telethon listener restart error: {str(e)[:120]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
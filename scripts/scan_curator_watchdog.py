#!/usr/bin/env python3
"""scan_curator_watchdog.py — restart the HMQ scan curator if it died. Cron no_agent (5m).

Silent when healthy (empty stdout -> no Telegram delivery). Prints ONLY when it
restart-s something or a restart failed. Liveness = pidfile + kill(pid,0), no pgrep.
Honors scan_curator.off stop-flag (user said stop -> never resurrect).
"""
import os, signal, subprocess, sys, time

DESK = os.path.expanduser("~/memecoin-quant")
SCRIPT = os.path.join(DESK, "scan_curator.py")
PY = "/home/agentuser/memecoin-quant/.venv/bin/python"
LOG = "/tmp/scan_curator.log"
PID_FILE = os.path.join(DESK, "scan_curator.pid")
STOP_FLAG = os.path.join(DESK, "scan_curator.off")


def curator_alive():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IOError):
        return False


def main():
    if os.path.exists(STOP_FLAG):
        return 0  # user asked to stop: never resurrect
    if curator_alive():
        return 0  # healthy — silent

    try:
        logf = open(LOG, "a")
        logf.write(f"\n===== scan curator restart {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        logf.flush()
        proc = subprocess.Popen([PY, SCRIPT, "run"],
                                cwd=DESK,
                                stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                                start_new_session=True)
        time.sleep(8)
        if curator_alive():
            print(f"\U0001f6e0\ufe0f HMQ scan curator restarted (pid {proc.pid}) \u2014 was dead. Log: {LOG}")
            return 0
        print(f"\u274c HMQ scan curator restart FAILED (exited rc={proc.poll()}). Log: {LOG}")
        return 1
    except Exception as e:
        print(f"\u274c HMQ scan curator restart error: {str(e)[:120]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
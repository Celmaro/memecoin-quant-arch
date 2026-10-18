#!/usr/bin/env python3
"""blix_watchdog.py — keep the Blix radar (localhost:4420) alive.

Checks /api/health every run; if down, spawns the Node server with node24.
Run every 5m via cron (silent when healthy).
"""
import json, os, subprocess, sys, time, urllib.request

BLIX_DIR = os.path.expanduser("~/blixtrade")
NODE24 = os.path.expanduser("~/.local/node24/bin/node")
PORT = int(os.environ.get("RADAR_PORT", "4420"))
PID_FILE = os.path.expanduser("~/blixtrade/blix.pid")


def _alive():
    try:
        with urllib.request.urlopen(f"http://localhost:{PORT}/api/health", timeout=8) as r:
            d = json.loads(r.read().decode())
            return bool(d.get("t"))
    except Exception:
        return False


def main():
    if _alive():
        return 0  # healthy, silent
    # try pidfile kill first (avoid stacking duplicates)
    try:
        pid = int(open(PID_FILE).read().strip())
        os.kill(pid, 9)
        time.sleep(1)
    except Exception:
        pass
    log = open(os.path.expanduser("~/blixtrade/blix_restart.log"), "a")
    log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} restarting blix\n")
    log.flush()
    env = dict(os.environ)
    env["RADAR_PORT"] = str(PORT)
    proc = subprocess.Popen(
        [NODE24, "--no-warnings", "server/server.js"],
        cwd=BLIX_DIR, env=env, stdout=log, stderr=log,
        start_new_session=True,
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    time.sleep(10)
    if _alive():
        print(f"🔄 Blix radar restarted (pid {proc.pid})")
    else:
        print("❌ Blix restart failed — check ~/blixtrade/blix_restart.log")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
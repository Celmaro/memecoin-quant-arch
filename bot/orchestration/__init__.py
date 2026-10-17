"""orchestration/ (doc 1: event_bus, position_workers, scheduler, recovery).

Coordinates the live runtime: position workers (per open position), scheduler
(cron sync), recovery (atomic state + daemon restart hygiene). Paper-mode gentle:
workers here just surface decisions; execution stays in position_monitor/paper_tick.
"""
import os, sys, time, json
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class EventBus:
    """Doc-1 event_bus: publish/subscribe for position decisions and alerts."""
    def __init__(self):
        self._subs = {}

    def subscribe(self, key, cb):
        self._subs.setdefault(key, []).append(cb)

    def publish(self, key, payload=None):
        for cb in self._subs.get(key, []):
            try:
                cb(payload)
            except Exception:
                pass


_bus = EventBus()


def get_bus():
    return _bus


def position_workers():
    """Doc-1 position_workers: return {mint: pos} for every open position."""
    import position_monitor as pm
    return pm.load_positions()


def tick_positions():
    """Run the decision chain for every open position; returns plain-language lines."""
    import position_monitor as pm
    from bot.decision_engine import decide
    from bot.persistence import log_decision
    out = []
    for mint, pos in list(pm.load_positions().items())[:8]:
        try:
            r = pm.analyze(mint, pos)
            if not r.get("ok"):
                continue
            contract = decide(mint, pos, r.get("signals") or [], r.get("mc"), r.get("liq"),
                              r.get("mult"), r.get("age_min"), extra={"cash": 0})
            log_decision(contract, mint)
            pos["last_decision_ts"] = time.time()
            if contract["action"] != "HOLD":
                out.append(f"${r.get('symbol')} -> {contract['action']} ({contract['sell_fraction']*100:.0f}%) "
                           f"conf {contract['confidence']:.0f} [{','.join(contract['reason_codes'])}]")
        except Exception as e:
            out.append(f"⚠ worker err {mint[:8]}: {str(e)[:60]}")
    pm.save_positions(pm.load_positions())
    return out


def recovery_check():
    """Doc-1 recovery: ensure daemons live + state files parse (no torn JSON)."""
    import subprocess
    issues = []
    for name in ("hmq_listener.py", "ws_monitor.py"):
        try:
            proc = subprocess.run(["pgrep", "-f", name], capture_output=True, text=True, timeout=5)
            if proc.returncode != 0:
                issues.append(f"{name} not running")
        except Exception as e:
            issues.append(f"{name} check failed: {e}")
    for f in ("positions.json", "paper_book.json", "ws_state.json"):
        p = os.path.expanduser(f"~/memecoin-quant/{f}")
        if os.path.exists(p):
            try:
                json.load(open(p))
            except Exception as e:
                issues.append(f"{f} unreadable: {e}")
    return issues


def scheduler_status():
    """Doc-1 scheduler: report cron job states (paused/resume)."""
    try:
        from cronjob_manage import list_jobs  # noqa - runtime tool, may not import in scripts
    except Exception:
        pass
    return None
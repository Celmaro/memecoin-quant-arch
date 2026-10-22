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
    """Run the decision chain for every open position; returns plain-language lines.

    Audit P1-7 fixes:
      - iterate ALL positions (no `[:8]` skip)
      - persist the SAME in-memory dict that was mutated (no reload-discard)
      - no duplicate decision: pm.analyze() already runs decide()+log_decision();
        this worker surfaces pos['last_decision'] instead of deciding again.
    """
    import position_monitor as pm
    out = []
    positions = pm.load_positions()          # single authoritative snapshot
    for mint, pos in list(positions.items()):
        try:
            r = pm.analyze(mint, pos)        # logs the decision contract itself
            if not r.get("ok"):
                continue
            contract = pos.get("last_decision") or {}
            action = contract.get("action") or "HOLD"
            frac = contract.get("sell_fraction") or 0
            pos["last_decision_ts"] = time.time()
            if action != "HOLD":
                out.append(f"${r.get('symbol')} -> {action} ({frac*100:.0f}%) "
                           f"conf {contract.get('confidence', 0):.0f} [{','.join(contract.get('reason_codes') or [])}]")
        except Exception as e:
            out.append(f"⚠ worker err {mint[:8]}: {str(e)[:60]}")
    pm.save_positions(positions)             # persists the same mutated objects
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
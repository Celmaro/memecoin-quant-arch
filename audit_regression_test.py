#!/usr/bin/env python3
"""P13 shim (Oct 20): implementation moved to bot/audit/audit_regression_test.py — kept
at root so absolute-path callers (cron runners, aliases, docs) keep working.
Run directly or via `python3 ~/memecoin-quant/audit_regression_test.py arg...`."""
import os, sys, runpy
sys.path.insert(0, os.path.expanduser("~/memecoin-quant"))
sys.path.insert(0, os.path.expanduser("~/memecoin-quant/bot/audit"))
_here = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.join(_here, "bot", "audit", "audit_regression_test.py"), run_name="__main__")

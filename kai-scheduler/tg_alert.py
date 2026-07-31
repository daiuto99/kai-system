"""Back-compat shim — the alert helper now lives in the notify() gateway (KAI-1004).

Historically kai-scheduler kept its own raw Telegram send here (it did not mount
/shared). As of COMMS Phase 1, kai-scheduler mounts /shared:ro (PYTHONPATH=/shared)
and the single chokepoint is shared/notify_gateway.py; this file re-exports tg_alert()
so the scheduler/invariants/watchdog/triage call sites keep working while flowing
through the gateway. A bare alert defaults to the dashboard audience (Rule B) — it
does not push to Leo. Do not add a raw send here; the enforcement check
(scripts/check_notify_chokepoint.py) forbids it.
"""
from notify_gateway import tg_alert

__all__ = ["tg_alert"]

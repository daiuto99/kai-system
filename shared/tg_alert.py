"""Back-compat shim — the alert helper now lives in the notify() gateway (KAI-1004).

Historically this module owned a raw Telegram send. As of COMMS Phase 1 the single
chokepoint is shared/notify_gateway.py; this file re-exports tg_alert() so the many
`from tg_alert import tg_alert` call sites keep working while flowing through the
gateway (reality gate + classify + Rule-A log). A bare alert defaults to the dashboard
audience (Rule B) — it does not push to Leo. Do not add a raw send here; the
enforcement check (scripts/check_notify_chokepoint.py) forbids it.
"""
from notify_gateway import tg_alert

__all__ = ["tg_alert"]

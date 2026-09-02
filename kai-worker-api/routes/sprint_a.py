"""Sprint A Slice 4 — FastAPI routes.

Exposes one endpoint to the worker:

- POST /sprint-a/expire-stale — fired by the kai-scheduler container hourly.
  Calls expire_and_notify; when notification is requested the notifier routes a
  single expiry summary through the notify gateway (Telegram, single voice).

Sprint-A clarification replies are handled on Telegram (routes/telegram.py) via
sprint_a_handlers.handle_clarification_choice. The former button-click ingress
and its signature verification were removed in the AR-2 comms purge (KAI-1243).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from safe_http import safe_body

import sprint_a_handlers as handlers

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/sprint-a/expire-stale")
async def expire_stale_endpoint(request: Request):
    """Scheduler tick. Body is JSON: {expiry_hours?: int, notify_channel?: str}.
    Default expiry_hours=24. A truthy notify_channel enables the summary alert;
    the summary is delivered through the notify gateway (single voice).
    """
    body = await safe_body(request)
    expiry_hours = int(body.get("expiry_hours", 24))
    notify = bool(body.get("notify_channel", True))

    notifier = None
    if notify:
        def _notify(text: str) -> None:
            try:
                from tg_alert import tg_alert
                tg_alert(text)
            except Exception as exc:
                logger.warning("expire-stale alert failed: %s", exc)
        notifier = _notify

    return handlers.expire_and_notify(expiry_hours=expiry_hours, notifier=notifier)

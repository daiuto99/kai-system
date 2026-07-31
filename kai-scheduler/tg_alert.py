"""AR-5.1 — Telegram alert helper for kai-scheduler.

Telegram is the sole surface (AR-5, docs/AR5_TELEGRAM_SOLE_SURFACE_DESIGN.md).
System / security / triage alerts route here instead of Slack. Reads
telegram_bot_token + telegram_allowed_chat_ids from /run/secrets. Best-effort and
fail-soft: never raises into the caller's alert path. No parse_mode — alert text
is arbitrary and must not trip Markdown parsing.
"""
import logging
import os
import sys
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
_API = "https://api.telegram.org"


def _notify_suppressed_in_test() -> bool:
    """COMMS P0 reality-gate: a test/synthetic context must never reach Leo's real Telegram.
    True under pytest or when a harness sets KAI_NOTIFY_TEST_SINK=1. Inert in production."""
    return ("pytest" in sys.modules) or (os.environ.get("KAI_NOTIFY_TEST_SINK") == "1")


def _secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    return p.read_text().strip() if p.exists() else ""


def _chat_ids() -> list[str]:
    raw = _secret("telegram_allowed_chat_ids")
    return [c.strip() for c in raw.replace("\n", ",").split(",") if c.strip()]


def tg_alert(message: str) -> bool:
    """Send an alert to every allowed Telegram chat. Returns True if any send ok."""
    if _notify_suppressed_in_test():
        log.info("tg_alert SUPPRESSED (test context, not sent): %s", (message or "")[:180])
        return False
    token = _secret("telegram_bot_token")
    if not token:
        log.error("tg_alert: telegram_bot_token missing — alert dropped")
        return False
    ok = False
    for chat_id in _chat_ids():
        try:
            r = httpx.post(
                f"{_API}/bot{token}/sendMessage",
                json={"chat_id": int(chat_id), "text": message},
                timeout=10,
            )
            ok = ok or (r.status_code == 200)
        except Exception as e:
            log.error("tg_alert send failed for chat %s: %s", chat_id, type(e).__name__)
    return ok

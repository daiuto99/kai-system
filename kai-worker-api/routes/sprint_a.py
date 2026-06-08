"""Sprint A Slice 4 — FastAPI routes.

Adds two endpoints to the worker:

- POST /slack/interactions — Slack receives button clicks here. Verifies the
  Slack signature, parses the payload, and (if it's a sprint_a_clarify action)
  calls handle_clarification_choice and posts the reply back in the same
  Slack thread.

- POST /sprint-a/expire-stale — fired by the kai-scheduler container hourly.
  Calls expire_and_notify; the notifier posts a single message to #kai-system
  if anything expired.

This module is the *only* new mounted router for Sprint A. Slack thread-reply
fallback and Telegram callback_query handling are inlined into the existing
routes/slack.py + routes/telegram.py files (minimal edits).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, HTTPException, Request

import clarification_surface as surface
import sprint_a_handlers as handlers

logger = logging.getLogger(__name__)
router = APIRouter()

SLACK_API = "https://slack.com/api"


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_signing_secret() -> str:
    p = Path("/run/secrets/slack_signing_secret")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_SIGNING_SECRET", "")


def _verify_slack_sig(raw_body: bytes, ts: str, sig: str) -> bool:
    secret = _slack_signing_secret()
    if not secret:
        logger.warning("SLACK_SIGNING_SECRET not configured — skipping verification")
        return True
    base = f"v0:{ts}:{raw_body.decode('utf-8', errors='replace')}".encode()
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _slack_post(channel: str, text: str, thread_ts: str | None = None) -> dict:
    payload = {"channel": channel, "text": text, "username": "KAI",
               "icon_url": "https://kai.sonicink.space/icon-192.png"}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        r = httpx.post(f"{SLACK_API}/chat.postMessage",
                       headers={"Authorization": f"Bearer {_slack_token()}"},
                       json=payload, timeout=15)
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception as e:
        logger.warning("slack post failed: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/slack/interactions")
async def slack_interactions(request: Request):
    raw = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_sig(raw, ts, sig):
        raise HTTPException(403, "Invalid Slack signature")

    form = parse_qs(raw.decode("utf-8", errors="replace"))
    payload_raw = (form.get("payload") or [""])[0]
    if not payload_raw:
        return {"ok": True}
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return {"ok": True}

    if payload.get("type") != "block_actions":
        return {"ok": True}

    actions = payload.get("actions") or []
    if not actions:
        return {"ok": True}
    action = actions[0]
    parsed = surface.parse_callback(action.get("action_id", ""))
    if not parsed:
        # Not a Sprint A action — let other interaction handlers (when they exist) deal.
        return {"ok": True}

    channel = (payload.get("channel") or {}).get("id")
    msg = payload.get("message") or {}
    thread_ts = msg.get("thread_ts") or msg.get("ts")

    result = handlers.handle_clarification_choice(
        parsed["pending_id"], parsed["field"], parsed["choice"],
    )

    if channel:
        _slack_post(channel, result.get("reply_text") or "_(no reply text)_",
                    thread_ts=thread_ts)

    return {"ok": True, "status": result.get("status")}


@router.post("/sprint-a/expire-stale")
async def expire_stale_endpoint(request: Request):
    """Scheduler tick. Body is JSON: {expiry_hours?: int, notify_channel?: str}.
    Default expiry_hours=24, default notify_channel=#kai-system.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    expiry_hours = int(body.get("expiry_hours", 24))
    notify_channel = body.get("notify_channel", "#kai-system")

    notifier = None
    if notify_channel:
        def _notify(text: str) -> None:
            _slack_post(notify_channel, text)
        notifier = _notify

    return handlers.expire_and_notify(expiry_hours=expiry_hours, notifier=notifier)

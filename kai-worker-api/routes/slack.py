"""RETIRED 2026-07-29 (AR-5.3 / KAI-991 follow-on): Slack is no longer a surface.

kai-slack-bot is gone (AR-5.2) and all notifications route to Telegram
(AR-5.1/5.3). This module is kept as a dormant stub so the router still mounts
and the historical endpoint paths still resolve (auth-guard + traversal tests,
and any lingering caller), but every handler is a no-op that makes no outbound
Slack API call. Deliberately no Slack web-API references remain.

Ripple flagged for a later council-side pass: the council tools
`create_slack_channel` / `invite_to_slack_channel` and the Slack behaviour of
`setup_project` (kai-council-api router.py + execute_tool.py) still assume these
endpoints provision Slack. They now receive a `retired` response; retiring those
tool definitions + dropping Slack from setup_project is a separate decision.
"""
import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter()

_RETIRED = {"ok": False, "retired": "Slack retired (AR-5) — the surface is Telegram."}


@router.post("/slack/channels")
def create_slack_channel(body: dict):
    logger.info("create_slack_channel called after Slack retirement — no-op")
    return _RETIRED


@router.post("/slack/channels/{channel_name}/invite")
def invite_to_slack_channel(channel_name: str, body: dict):
    logger.info("invite_to_slack_channel(%s) called after Slack retirement — no-op", channel_name)
    return _RETIRED


@router.get("/slack/users/lookup")
def slack_lookup_user(email: str = None, name: str = None):
    return {"found": False, "retired": "Slack retired (AR-5)."}


@router.post("/slack/events")
async def slack_events(request: Request):
    """Dormant. Slack Events ingress is retired; thread-reply clarifications and
    check-in replies are handled on Telegram (kai-scheduler poll loop). Still
    answers the URL-verification handshake so a lingering Slack app config does
    not error, but processes no events."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}
    if isinstance(body, dict) and body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}
    return {"ok": True}

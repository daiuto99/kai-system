"""slack.post capability — post a message to a Slack channel from a workflow step."""
import os
from pathlib import Path

from models import CapabilityResult
from transports.base import safe_request
from . import capability

_SLACK_API = "https://slack.com/api"


def _slack_token() -> str:
    p = Path("/run/wp_secrets/slack_bot_token.txt")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


@capability("slack.post")
def post(
    channel: str,
    text: str,
    username: str = "kai-orchestrator",
    icon_emoji: str = ":robot_face:",
    **_,
) -> CapabilityResult:
    """Post a message to a Slack channel. channel can be a name (#kai-system) or ID."""
    token = _slack_token()
    if not token:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "no_slack_token", "detail": "slack_bot_token.txt missing and SLACK_BOT_TOKEN not set"},
        )

    r = safe_request(
        "POST", f"{_SLACK_API}/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text, "username": username, "icon_emoji": icon_emoji},
        timeout=15,
    )

    if not r.ok or not isinstance(r.data, dict):
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "slack_http_error", "status_code": r.status_code, "detail": r.body_preview or r.error},
        )

    if not r.data.get("ok"):
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "slack_api_error", "error": r.data.get("error", "unknown")},
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={"channel": r.data.get("channel"), "ts": r.data.get("ts")},
        verification={"verified": True, "method": "slack_ok_field"},
    )

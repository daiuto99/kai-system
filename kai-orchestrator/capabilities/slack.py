"""slack.post capability — RETIRED to Telegram (AR-5 sole surface, 2026-07-29).

kai-slack-bot is gone and Slack is no longer a surface. The capability NAME is
kept so existing workflow steps that call `slack.post` keep resolving; the body
now routes to Telegram via the shared tg_alert chokepoint. The `channel` /
`username` / `icon_emoji` args are accepted for signature compatibility but
ignored (Telegram has one allowed chat). No Slack web-API references remain.
"""
from models import CapabilityResult
from . import capability


@capability("slack.post")
def post(
    channel: str,
    text: str,
    username: str = "kai-orchestrator",
    icon_emoji: str = ":robot_face:",
    **_,
) -> CapabilityResult:
    """Post a message. Retired from Slack to Telegram (AR-5); `channel` ignored."""
    try:
        from tg_alert import tg_alert
        ok = tg_alert(text)
    except Exception as e:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "tg_alert_error", "detail": type(e).__name__},
        )
    if not ok:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "tg_alert_failed", "detail": "no Telegram send succeeded"},
        )
    return CapabilityResult(
        ok=True, status="succeeded",
        data={"surface": "telegram"},
        verification={"verified": True, "method": "tg_alert_ok"},
    )

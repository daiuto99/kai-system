"""notify.post capability — routes operational notifications to Telegram
(AR-5 sole surface). Renamed from the retired legacy chat-post capability
(KAI-1129); that surface is gone (AR-5.2/5.3) and the body routes to Telegram
via the shared tg_alert chokepoint. The `channel` / `username` / `icon_emoji` args are accepted for
signature compatibility but ignored (Telegram has one allowed chat).
"""
from models import CapabilityResult
from . import capability


@capability("notify.post")
def post(
    channel: str,
    text: str,
    username: str = "kai-orchestrator",
    icon_emoji: str = ":robot_face:",
    **_,
) -> CapabilityResult:
    """Post a message to Telegram (AR-5 sole surface); `channel` ignored."""
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

"""WP-20.3 workflow-only write policy and DevOps violation alert."""
from __future__ import annotations

import logging
from pathlib import PurePosixPath

import httpx

log = logging.getLogger(__name__)

CANONICAL_CALLERS = frozenset({
    "kai-orchestrator/workflows/wordpress_publish_homepage.py",
    "kai-worker-api/routes/wordpress.py",
    "scripts/wp_add_site.sh",
    "scripts/wp_brand_consistency.py",
    "workflows/wordpress_publish_homepage.py",
    "routes/wordpress.py",
})


class WorkflowOnlyWriteViolation(PermissionError):
    """Raised before a WP REST write from a non-canonical surface."""


def _normalise(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix()


def is_canonical_caller(caller: str) -> bool:
    normalised = _normalise(caller)
    return any(normalised.endswith(suffix) for suffix in CANONICAL_CALLERS)


def _slack_token() -> str:
    try:
        return open("/run/secrets/slack_bot_token").read().strip()
    except OSError:
        return ""


def _alert_devops(caller: str, action: str) -> None:
    """Reuse the established direct #devops alert transport; alert failure logs."""
    token = _slack_token()
    if not token:
        log.error("WP write guard violation could not alert #devops: Slack token unavailable")
        return
    text = (
        ":rotating_light: *WP-20.3 workflow-only writes violation blocked* — "
        f"non-canonical caller `{_normalise(caller)}` attempted `{action}`. "
        "Use a canonical WordPress workflow surface."
    )
    try:
        response = httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "#devops", "text": text,
                  "username": "DevOps",
                  "icon_url": "https://kai.sonicink.space/avatar-devops.png"},
            timeout=10,
        )
        if response.status_code >= 400 or "\"ok\":true" not in response.text.replace(" ", ""):
            log.error("WP write guard #devops alert failed: HTTP %s", response.status_code)
        else:
            log.warning("WP write guard violation alert posted to #devops")
    except Exception as exc:
        log.error("WP write guard #devops alert failed: %s", exc)


def assert_canonical_caller(caller: str, action: str) -> str:
    """Allow a canonical write caller or alert #devops and block the attempt."""
    normalised = _normalise(caller)
    if is_canonical_caller(normalised):
        return normalised
    _alert_devops(normalised, action)
    raise WorkflowOnlyWriteViolation(
        "WP REST writes require a canonical workflow surface; violation alerted to #devops"
    )

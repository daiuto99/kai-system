"""WP-20.3 workflow-only write policy and DevOps violation alert."""
from __future__ import annotations

import logging
from pathlib import PurePosixPath

log = logging.getLogger(__name__)

CANONICAL_CALLERS = frozenset({
    "kai-orchestrator/workflows/wordpress_publish_homepage.py",
    "kai-orchestrator/workflows/wordpress_edit_page_draft.py",
    "kai-worker-api/routes/wordpress.py",
    "scripts/wp_add_site.sh",
    "scripts/wp_brand_consistency.py",
    "workflows/wordpress_publish_homepage.py",
    "workflows/wordpress_edit_page_draft.py",
    "routes/wordpress.py",
})


class WorkflowOnlyWriteViolation(PermissionError):
    """Raised before a WP REST write from a non-canonical surface."""


def _normalise(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix()


def is_canonical_caller(caller: str) -> bool:
    normalised = _normalise(caller)
    return any(normalised.endswith(suffix) for suffix in CANONICAL_CALLERS)


def _alert_devops(caller: str, action: str) -> None:
    """Alert through the established watchdog #devops transport."""
    from watchdog import _page_alert

    text = (
        ":rotating_light: *WP-20.3 workflow-only writes violation blocked* — "
        f"non-canonical caller `{_normalise(caller)}` attempted `{action}`. "
        "Use a canonical WordPress workflow surface."
    )
    _page_alert(text)
    log.warning("WP write guard violation alert sent through watchdog transport")


def assert_canonical_caller(caller: str, action: str) -> str:
    """Allow a canonical write caller or alert #devops and block the attempt."""
    normalised = _normalise(caller)
    if is_canonical_caller(normalised):
        return normalised
    _alert_devops(normalised, action)
    raise WorkflowOnlyWriteViolation(
        "WP REST writes require a canonical workflow surface; violation alerted to #devops"
    )

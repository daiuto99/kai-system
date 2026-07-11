"""Shared worker-auth credential loader for the kai-scheduler container.

Bug 48f85706 / aec2d486: kai-worker-api authenticates every route (bar
/health + webhooks). Internal callers attach the worker Basic-auth credential
they hold as a Docker secret rather than relying on a network-origin bypass or
a growing exempt list. Consumed by scheduler.py, watchdog.py, invariants.py.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def worker_auth() -> tuple[str, str] | None:
    """Return (user, pw) for kai-worker-api, or None if no credential mounted."""
    for p in (
        "/run/secrets/kai_worker_auth",
        "/run/wp_secrets/kai_worker_auth.txt",
        "/home/leo/kai-system/secrets/kai_worker_auth.txt",
    ):
        try:
            raw = Path(p).read_text().strip()
        except Exception:
            continue
        if ":" in raw:
            user, pw = raw.split(":", 1)
            return (user, pw)
    logger.warning("worker_auth: no kai_worker_auth credential found — worker calls will 401")
    return None

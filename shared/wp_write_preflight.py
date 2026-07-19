"""Client used by canonical non-worker surfaces to preflight a WP REST write."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

_AUTH_PATHS = (Path("/run/secrets/kai_worker_auth"),
               Path("/home/leo/kai-system/secrets/kai_worker_auth.txt"))


def _worker_auth() -> tuple[str, str]:
    for path in _AUTH_PATHS:
        try:
            user, password = path.read_text().strip().split(":", 1)
            return user, password
        except (OSError, ValueError):
            continue
    raise RuntimeError("worker API credential unavailable for WP write preflight")


def preflight(caller: str, action: str) -> None:
    user, password = _worker_auth()
    response = httpx.post(
        f"{os.environ.get('KAI_WORKER_API_URL', 'http://kai-worker-api:8001')}/wordpress/write-preflight",
        auth=(user, password),
        json={"caller": caller, "action": action},
        timeout=15,
    )
    if response.status_code >= 400:
        raise PermissionError("WP write preflight rejected the caller")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: wp_write_preflight.py <caller-path> <action>")
    preflight(sys.argv[1], sys.argv[2])

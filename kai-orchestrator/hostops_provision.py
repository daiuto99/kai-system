"""HOSTOPS provisioning: mint the publish-gate payload secret only.

Cloudways does not support KAI's former per-app deploy-key model. The server
master credential is the approved transport, so provisioning intentionally
does not create, install, read, or verify any per-app SSH key. It only creates
the mode-0600 secret that the gated workflow later sends over stdin to wp-cli.
"""
from __future__ import annotations

import os
import re
import secrets as _secrets
import stat
from pathlib import Path

_PAYLOAD_DIR = Path("/run/hostops-payload-secrets")
_PUBLISH_GATE_SECRET_NAME = "kai_publish_gate_secret"
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


class HostOpsProvisionError(RuntimeError):
    """A safe-to-report provisioning failure; never contains secret bytes."""


def _valid_secret_file(path: Path, uid: int) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISREG(st.st_mode)
        and st.st_size > 0
        and stat.S_IMODE(st.st_mode) == 0o600
        and st.st_uid == uid
    )


def _provision_payload_secret(site: str, uid: int, rotate: bool) -> str:
    site_dir = _PAYLOAD_DIR / site
    path = site_dir / _PUBLISH_GATE_SECRET_NAME
    if not rotate and _valid_secret_file(path, uid):
        return "present"

    site_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(site_dir, 0o700)
    token = _secrets.token_hex(32).encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return "rotated" if rotate else "created"


def provision_secret(site: str, *, rotate: bool = False, uid: int | None = None) -> dict[str, str]:
    """Ensure the site payload secret exists and return metadata only (L18)."""
    uid = os.geteuid() if uid is None else uid
    if not _SAFE.fullmatch(site):
        raise HostOpsProvisionError("invalid site key")
    return {"site": site, "payload_secret": _provision_payload_secret(site, uid, rotate)}


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m hostops_provision <site> [--rotate]")
    print(json.dumps(provision_secret(sys.argv[1], rotate="--rotate" in sys.argv[2:])))

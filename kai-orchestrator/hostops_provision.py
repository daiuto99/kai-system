"""HOSTOPS provisioning (KAI-928/929): the system mints and installs its own
per-app Cloudways deploy key and publish-gate payload secret.

Trust model — read before editing:
  * This runs ONLY in the orchestrator's trusted context (uid 1000), never an
    agent chat session. Private-key bytes and the minted secret are generated,
    written to the mode-0600 store, and consumed at the SSH boundary here; they
    never reach an argv, a log line, a CapabilityResult, or a transcript (L18).
    The PUBLIC key is the only key material that leaves this module.
  * The new scoped per-app key is INSTALLED using the pre-existing Cloudways
    master credential (/run/secrets/cloudways_ssh_key) — installer-only. The
    per-app key, not the master, is what every later hostops op authenticates
    with (HOSTOPS-(a) least-privilege). This resolves the chicken-and-egg: a key
    cannot install itself.
  * The store dirs are read-write to the orchestrator (and only the orchestrator)
    and remain blocked to agent sessions by the Mode Gate. The human stays in the
    council-approval loop for the site mutations this enables; provisioning KAI's
    own credentials is infrastructure, not a site-content mutation.
"""
from __future__ import annotations

import json
import os
import re
import secrets as _secrets
import shlex
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from hostops_identity import HostOpsIdentityResolver

_SITES_JSON = Path("/vault/00_System/wordpress_sites.json")
_KEY_DIR = Path("/run/hostops-deploy-keys")
_PAYLOAD_DIR = Path("/run/hostops-payload-secrets")
_MASTER_KEY = "/run/secrets/cloudways_ssh_key"
_PUBLISH_GATE_SECRET_NAME = "kai_publish_gate_secret"
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Cloudways server master login — matches transports/cloudways_ssh_purge.py and
# transports/ssh_php_eval.py. Server-scoped; used ONLY to install the per-app
# public key into the app user's authorized_keys (installer, never the deploy key).
_MASTER_LOGIN = "master_vvbwxpwpcc@134.209.166.23"
_MASTER_SSH_OPTS = [
    "-i", _MASTER_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
]


class HostOpsProvisionError(RuntimeError):
    """A safe-to-report provisioning failure; never contains key/secret bytes."""


@dataclass(frozen=True)
class ProvisionOutcome:
    """Metadata-only provisioning result — never carries key or secret bytes."""

    site: str
    deploy_key: str        # created | present | rotated
    payload_secret: str    # created | present | rotated
    key_authenticated: bool
    audit_identity: str


def _run(cmd, timeout=30, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def _site_config(site_key: str) -> dict:
    if not _SAFE.fullmatch(site_key):
        raise HostOpsProvisionError("invalid site key")
    try:
        return json.loads(_SITES_JSON.read_text())["sites"][site_key]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise HostOpsProvisionError("hostops site configuration unavailable") from exc


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


def _app_home(cfg: dict) -> str:
    server_id = str(cfg.get("cloudways_server_id", ""))
    app_user = str(cfg.get("cloudways_sys_user", ""))
    if not _SAFE.fullmatch(server_id) or not _SAFE.fullmatch(app_user):
        raise HostOpsProvisionError("invalid cloudways identifiers")
    return f"/home/{server_id}.cloudwaysapps.com/{app_user}"


def _app_target(cfg: dict) -> str:
    app_user = str(cfg.get("cloudways_sys_user", ""))
    fqdn = str(cfg.get("cloudways_fqdn", ""))
    if not _SAFE.fullmatch(app_user) or not _SAFE.fullmatch(fqdn):
        raise HostOpsProvisionError("invalid cloudways target")
    return f"{app_user}@{fqdn}"


def _install_pubkey(cfg: dict, pubkey: str) -> None:
    """Idempotently append the PUBLIC deploy key to the app authorized_keys.

    Installed over the master credential. The public key is not secret; the
    private half never leaves _provision_deploy_key's file descriptor.
    """
    home = _app_home(cfg)
    ak = f"{home}/.ssh/authorized_keys"
    q_pub = shlex.quote(pubkey)
    remote = (
        f"set -e; umask 077; mkdir -p {home}/.ssh; touch {ak}; "
        f"grep -qxF {q_pub} {ak} || printf '%s\\n' {q_pub} >> {ak}"
    )
    r = _run(["ssh"] + _MASTER_SSH_OPTS + [_MASTER_LOGIN, remote], timeout=30)
    if r.returncode != 0:
        raise HostOpsProvisionError(
            f"deploy-key install failed (rc={r.returncode}): {r.stderr[:120]}"
        )


def _ensure_ssh_home() -> None:
    """Guarantee a writable ~/.ssh so ssh can record/read the host key."""
    Path(os.path.expanduser("~/.ssh")).mkdir(mode=0o700, parents=True, exist_ok=True)


def _verify_key(cfg: dict, key_path: Path) -> bool:
    """Read-only auth probe with the per-app deploy key; no mutation, no shell.

    Uses the default user known_hosts with accept-new: the app host key is
    recorded once here, and the gated hostops transport (which pins
    StrictHostKeyChecking=yes with no override) then reuses the same file.
    """
    _ensure_ssh_home()
    r = _run(
        [
            "ssh", "-i", str(key_path),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
            _app_target(cfg), "true",
        ],
        timeout=20,
    )
    return r.returncode == 0


def _provision_deploy_key(cfg: dict, handle, uid: int, rotate: bool) -> str:
    key_path = _KEY_DIR / handle.key_name
    if not rotate and _valid_secret_file(key_path, uid) and _verify_key(cfg, key_path):
        return "present"

    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(_KEY_DIR)) as td:
        tmp = Path(td) / "k"
        gen = _run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-q",
             "-C", f"kai-hostops-{handle.site_key}", "-f", str(tmp)],
            timeout=20,
        )
        if gen.returncode != 0:
            raise HostOpsProvisionError("keypair generation failed")
        pub = Path(str(tmp) + ".pub").read_text().strip()
        _install_pubkey(cfg, pub)
        os.chmod(tmp, 0o600)
        os.replace(str(tmp), str(key_path))  # atomic, same filesystem
    if not _verify_key(cfg, key_path):
        raise HostOpsProvisionError("deploy key installed but authentication probe failed")
    return "rotated" if rotate else "created"


def _provision_payload_secret(site: str, uid: int, rotate: bool) -> str:
    site_dir = _PAYLOAD_DIR / site
    path = site_dir / _PUBLISH_GATE_SECRET_NAME
    if not rotate and _valid_secret_file(path, uid):
        return "present"

    site_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(site_dir, 0o700)
    token = _secrets.token_hex(32).encode()
    # O_TRUNC allows atomic-enough rotation; bytes never leave this fd.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, token)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return "rotated" if rotate else "created"


def provision(site: str, *, rotate: bool = False, uid: int | None = None) -> ProvisionOutcome:
    """Ensure the per-app deploy key and publish-gate secret exist for `site`.

    Idempotent: a present, valid, authenticating key and a present valid secret
    are left untouched unless ``rotate`` is set. Returns metadata only.
    """
    uid = os.geteuid() if uid is None else uid
    cfg = _site_config(site)
    handle = HostOpsIdentityResolver().resolve(site)
    deploy_key = _provision_deploy_key(cfg, handle, uid, rotate)
    payload_secret = _provision_payload_secret(site, uid, rotate)
    return ProvisionOutcome(
        site=site,
        deploy_key=deploy_key,
        payload_secret=payload_secret,
        key_authenticated=_verify_key(cfg, _KEY_DIR / handle.key_name),
        audit_identity=handle.audit_identity,
    )


if __name__ == "__main__":  # system-context CLI: `python -m hostops_provision <site> [--rotate]`
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m hostops_provision <site> [--rotate]")
    print(json.dumps(asdict(provision(sys.argv[1], rotate="--rotate" in sys.argv[2:]))))

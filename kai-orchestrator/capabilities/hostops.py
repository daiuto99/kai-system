"""Narrow, allowlisted Cloudways app host-operations for KAI-820 HOSTOPS-(b).

MASTER-OPERATOR MODEL (2026-07-22, supersedes the per-app-deploy-key transport —
see docs/HOSTOPS_MASTER_OPERATOR_DESIGN.md). Cloudways grants no per-app SSH login
on this account; the server MASTER operator is the intended, fully-capable surface
(it can write mu-plugins + wp-config and run wp-cli — verified live). Least-privilege
lives in KAI's control plane (op allowlist + council gates + fail-closed ownership +
audit reconciliation), NOT in the Cloudways credential.

There is deliberately no generic command capability. The publish-gate secret is
delivered as a wp-config constant via wp-cli and is NEVER placed in a log record or
CapabilityResult; the read-back value is compared in-process only (L18).
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import httpx

from models import CapabilityResult
from . import capability


_SITES_JSON = Path("/vault/00_System/wordpress_sites.json")
_PLUGIN_ALLOWLIST = {"kai-publish-gate"}
_OP_ALLOWLIST = {"status", "verify", "place_secret", "deploy_plugin", "publish_post"}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_SECRET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# The publish-gate payload secret KAI sends as the X-KAI-Publish-Gate header,
# minted by hostops_provision and mirrored site-side by place_secret. Read
# in-process only; the bytes never reach an argv, log, or CapabilityResult (L18).
_PAYLOAD_DIR = Path("/run/hostops-payload-secrets")
_PUBLISH_GATE_SECRET_FILE = "kai_publish_gate_secret"

# The Cloudways server master operator — the only working SSH identity on this
# account. Matches transports/ssh_php_eval.py and cloudways_ssh_purge.py.
_MASTER_LOGIN = "master_vvbwxpwpcc@134.209.166.23"
_MASTER_KEY = "/run/secrets/cloudways_ssh_key"
_MASTER_SSH_OPTS = [
    "-i", _MASTER_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
]
# The publish-gate plugin checks defined('KAI_PUBLISH_GATE_SECRET') first, before
# any file — so the secret is delivered as a wp-config constant (the app home is
# read-only to the master operator).
_SECRET_CONSTANT = "KAI_PUBLISH_GATE_SECRET"


@dataclass(frozen=True)
class InMemorySecret:
    """A short-lived secret transport wrapper, never serialized into a workflow record."""
    material: bytes


@dataclass(frozen=True)
class HostOpsTarget:
    host: str
    app_user: str
    app_id: str
    server_id: str

    @property
    def webroot(self) -> str:
        return f"/home/{self.server_id}.cloudwaysapps.com/{self.app_user}/public_html"

    @property
    def audit_identity(self) -> str:
        """Resolved app identity used for the gate and audit trail, not SSH auth."""
        return f"cloudways-app:{self.app_id}:{self.app_user}"


class HostOpsTargetError(RuntimeError):
    """A safe-to-report target-resolution failure; contains no credential data."""


class SshTransport(Protocol):
    def verify(self, target: HostOpsTarget) -> dict: ...
    def place_secret(self, target: HostOpsTarget, secret: bytes) -> dict: ...
    def deploy_plugin(self, target: HostOpsTarget, plugin: str) -> dict: ...
    def publish_post(self, target: HostOpsTarget, post_id: int) -> dict: ...


class OpenSshTransport:
    """Fixed master-operator OpenSSH invocations; no caller-controlled command/argv."""

    def __init__(self, runner: Callable = subprocess.run):
        self._runner = runner

    @staticmethod
    def _ssh(remote: str) -> list[str]:
        return ["ssh"] + _MASTER_SSH_OPTS + [_MASTER_LOGIN, remote]

    def verify(self, target: HostOpsTarget) -> dict:
        """Read-only reachability + writability probe. No mutation, no shell payload."""
        wr = shlex.quote(target.webroot)
        remote = f"test -w {wr}/wp-content/mu-plugins && test -w {wr}/wp-config.php && echo OK"
        try:
            completed = self._runner(self._ssh(remote), capture_output=True, text=True,
                                     timeout=20, check=False)
        except subprocess.TimeoutExpired:
            return {"authenticated": False, "probe": "master_ssh", "reason": "timeout"}
        return {"authenticated": completed.returncode == 0,
                "writable": (completed.stdout or "").strip() == "OK", "probe": "master_ssh"}

    def place_secret(self, target: HostOpsTarget, secret: bytes) -> dict:
        """Define the publish-gate constant in wp-config via wp-cli, then read it back.

        The value is compared in-process; it is never returned or logged (L18).
        """
        value = secret.decode("ascii")
        wr = shlex.quote(target.webroot)
        remote = (
            f"cd {wr} && wp config set {_SECRET_CONSTANT} \"$(cat)\" "
            f"--type=constant --quiet && wp config get {_SECRET_CONSTANT}"
        )
        # Secret material flows only on stdin.  The SSH argv and remote command
        # contain fixed syntax plus allowlisted identifiers (L18).
        completed = self._runner(self._ssh(remote), input=value, capture_output=True, text=True,
                                 timeout=30, check=False)
        ok = completed.returncode == 0 and completed.stdout.strip() == value
        return {"written": completed.returncode == 0, "read_back": ok, "name": _SECRET_CONSTANT}

    def deploy_plugin(self, target: HostOpsTarget, plugin: str) -> dict:
        # Source + destination fixed by the allowlisted plugin name.
        artifact = f"/opt/kai-plugins/{plugin}.php"
        destination = f"{target.webroot}/wp-content/mu-plugins/{plugin}.php"
        completed = self._runner(
            # Cloudways' server-master endpoint supports legacy scp transport;
            # force it rather than assuming an SFTP subsystem is available.
            ["scp", "-O"] + _MASTER_SSH_OPTS + [artifact, f"{_MASTER_LOGIN}:{destination}"],
            capture_output=True, text=True, timeout=40, check=False)
        if completed.returncode != 0:
            return {"deployed": False, "read_back": False, "plugin": plugin}
        readback = self._runner(self._ssh(f"test -f {shlex.quote(destination)}"),
                                capture_output=True, text=True, timeout=20, check=False)
        return {"deployed": True, "read_back": readback.returncode == 0, "plugin": plugin}

    def publish_post(self, target: HostOpsTarget, post_id: int) -> dict:
        """Transition an exact post to `publish` via wp-cli, then read the status back.

        The publish-gate mu-plugin still enforces the write-time draft filter; this
        succeeds ONLY because the gate meta was opened first via the authenticated
        REST route. post_id is an int (validated by the caller), so the remote
        command carries no caller-controlled string. No mutation of any other post.
        """
        wr = shlex.quote(target.webroot)
        pid = int(post_id)
        remote = (
            f"cd {wr} && wp post update {pid} --post_status=publish --quiet "
            f"&& wp post get {pid} --field=post_status"
        )
        completed = self._runner(self._ssh(remote), capture_output=True, text=True,
                                 timeout=40, check=False)
        status = (completed.stdout or "").strip()
        return {"published": completed.returncode == 0 and status == "publish",
                "post_status": status, "post_id": pid}


def _target(site_key: str) -> HostOpsTarget:
    try:
        site = json.loads(_SITES_JSON.read_text()).get("sites", {}).get(site_key, {})
        host = str(site.get("cloudways_fqdn", ""))
        server_id = str(site.get("cloudways_server_id", ""))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostOpsTargetError("hostops site configuration unavailable") from exc
    app_user = str(site.get("cloudways_sys_user", ""))
    app_id = str(site.get("cloudways_app_id", ""))
    if not all(_SAFE_COMPONENT.fullmatch(value) for value in (host, server_id, app_user, app_id)):
        raise HostOpsTargetError("hostops target is invalid or unavailable")
    return HostOpsTarget(host=host, app_user=app_user, app_id=app_id, server_id=server_id)


def _safe_error(exc: Exception) -> CapabilityResult:
    return CapabilityResult(ok=False, status="failed_final", error={"type": "hostops_unavailable", "message": str(exc)})


def _gate(gate_id: object, operation: str, site: str, resource: object = None, **inputs) -> str | None:
    """Use org-model policy; consume a bound human gate only when required.

    ``resource`` (optional) binds the consumed gate to an exact sub-resource of
    the site — e.g. a single post_id — so one approval cannot authorize a
    mutation on a different resource.
    """
    from policy.autonomy import check_policy
    policy_action, _ = check_policy(f"hostops.{operation}", "workflow", {"site": site, **inputs})
    if policy_action == "allow":
        return "autonomous"
    if not isinstance(gate_id, str) or not gate_id:
        return None
    from engine import engine
    bind = None if resource is None else str(resource)
    return gate_id if engine.consume_hostops_gate(gate_id, operation, site, resource=bind) else None


def _context(site: str) -> HostOpsTarget:
    return _target(site)


def audit_identity(site: str) -> str:
    """Resolve the app identity for gates without loading a per-app SSH key."""
    return _context(site).audit_identity


@capability("hostops.status")
def status(site: str, *, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Read-only master reachability + webroot writability; no mutation."""
    try:
        target = _context(site)
        proof = (transport or OpenSshTransport()).verify(target)
        return CapabilityResult(ok=proof.get("authenticated", False),
                                status="succeeded" if proof.get("authenticated") else "failed_recoverable",
                                data={"identity": target.audit_identity, "reachable": proof.get("authenticated", False)},
                                verification={"verified": proof.get("writable", False), "evidence": proof})
    except (HostOpsTargetError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)


@capability("hostops.verify")
def verify(site: str, *, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Master-operator SSH reachability probe (no mutation)."""
    try:
        target = _context(site)
        proof = (transport or OpenSshTransport()).verify(target)
        if proof.get("authenticated"):
            return CapabilityResult(ok=True, status="succeeded", data={"identity": target.audit_identity},
                                    verification={"verified": True, "evidence": proof}, transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable", error={"type": "ssh_auth_failed", "identity": target.audit_identity})
    except (HostOpsTargetError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)


@capability("hostops.place_secret")
def place_secret(site: str, secret_name: str, secret: InMemorySecret, gate_id: object = None, *, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Define the publish-gate secret in wp-config, only after a verified gate."""
    if not _SAFE_SECRET_NAME.fullmatch(secret_name) or not isinstance(secret, InMemorySecret):
        return CapabilityResult(ok=False, status="failed_final", error={"type": "input_not_allowed"})
    approved = _gate(gate_id, "place_secret", site, secret_name=secret_name)
    if approved is None:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "gate_required"})
    try:
        target = _context(site)
        proof = (transport or OpenSshTransport()).place_secret(target, secret.material)
        if proof.get("written") and proof.get("read_back"):
            evidence = {**proof, "authorization": "autonomous" if approved == "autonomous" else "gate"}
            if approved != "autonomous":
                evidence["gate_id"] = approved
            return CapabilityResult(ok=True, status="succeeded", data={"identity": target.audit_identity, "secret_name": secret_name}, verification={"verified": True, "evidence": evidence}, transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable", error={"type": "hostops_write_failed"})
    except (HostOpsTargetError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)


@capability("hostops.deploy_plugin")
def deploy_plugin(site: str, plugin: str, gate_id: object = None, *, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Deploy only a named, allowlisted plugin after a verified gate handle."""
    if plugin not in _PLUGIN_ALLOWLIST:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "plugin_not_allowed"})
    approved = _gate(gate_id, "deploy_plugin", site, plugin=plugin)
    if approved is None:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "gate_required"})
    try:
        target = _context(site)
        proof = (transport or OpenSshTransport()).deploy_plugin(target, plugin)
        if proof.get("deployed") and proof.get("read_back"):
            evidence = {**proof, "authorization": "autonomous" if approved == "autonomous" else "gate"}
            if approved != "autonomous":
                evidence["gate_id"] = approved
            return CapabilityResult(ok=True, status="succeeded", data={"identity": target.audit_identity, "plugin": plugin}, verification={"verified": True, "evidence": evidence}, transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable", error={"type": "plugin_deploy_failed"})
    except (HostOpsTargetError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)


@capability("hostops.provision")
def provision(site: str, rotate: bool = False, **_) -> CapabilityResult:
    """Mint the publish-gate payload secret for a site (master-operator model).

    Per-app deploy keys are retired (Cloudways grants no app-user SSH login); this
    now only ensures KAI's own minted publish-gate secret exists in the payload
    store — the value KAI sends as the X-KAI-Publish-Gate header and defines
    site-side via place_secret. Secret bytes never reach an argv, log, or result.
    """
    from hostops_provision import provision_secret as _provision_secret, HostOpsProvisionError
    try:
        outcome = _provision_secret(site, rotate=bool(rotate))
    except (HostOpsProvisionError, OSError, subprocess.SubprocessError) as exc:
        return _safe_error(exc)
    return CapabilityResult(
        ok=True, status="succeeded",
        data={"site": outcome["site"], "payload_secret": outcome["payload_secret"]},
        verification={"verified": True, "evidence": {"payload_secret": outcome["payload_secret"]}},
    )


def _read_payload_secret(site: str) -> str:
    """Read the site's publish-gate payload secret in-process (L18: never logged/returned)."""
    if not _SAFE_COMPONENT.fullmatch(site):
        raise HostOpsTargetError("invalid site key")
    path = _PAYLOAD_DIR / site / _PUBLISH_GATE_SECRET_FILE
    value = path.read_text().strip()
    if not value:
        raise HostOpsTargetError("publish-gate secret unavailable")
    return value


def _site_public_url(site: str) -> str:
    """Resolve the canonical public URL for the site's WordPress REST endpoint."""
    try:
        sites = json.loads(_SITES_JSON.read_text()).get("sites", {})
    except (OSError, json.JSONDecodeError) as exc:
        raise HostOpsTargetError("hostops site configuration unavailable") from exc
    url = str(sites.get(site, {}).get("url", "")).rstrip("/")
    if not url.startswith("https://"):
        raise HostOpsTargetError("hostops target is invalid or unavailable")
    return url


def _open_publish_gate(url: str, post_id: int, secret: str, resolver: str, gate_id: str,
                       *, client: Callable | None = None) -> dict:
    """Open the per-post publish gate via the plugin's authenticated REST route.

    The secret travels only in the X-KAI-Publish-Gate header (hash_equals-verified
    site-side); it is never placed in the URL, a log, or the returned dict (L18).
    """
    endpoint = f"{url}/wp-json/kai/v1/publish-gate/{int(post_id)}"
    headers = {
        "X-KAI-Publish-Gate": secret,
        "X-KAI-Resolver": resolver,
        "X-KAI-Gate-ID": gate_id,
    }
    poster = client or httpx.post
    resp = poster(endpoint, headers=headers, timeout=20)
    body = {}
    try:
        body = resp.json()
    except (ValueError, json.JSONDecodeError):
        body = {}
    return {"gate_open": resp.status_code == 200 and bool(body.get("gate_open")),
            "status_code": resp.status_code}


@capability("hostops.publish_post")
def publish_post(site: str, post_id: object, gate_id: object = None, resolver: str = "",
                 *, transport: SshTransport | None = None,
                 gate_opener: Callable | None = None, **_) -> CapabilityResult:
    """Publish an exact, already-drafted WordPress post — ONLY behind a human gate.

    JARVIS §9 drafts-only floor: publishing is never autonomous. The org-model
    "publish" high-risk threshold forces `classify` to require approval, and this
    capability additionally fails closed if it is ever handed an autonomous
    authorization (defence against org-model drift). Flow: consume the resolved
    human gate -> open the per-post plugin gate with the payload secret -> flip the
    single post to publish via wp-cli -> read the status back.
    """
    try:
        pid = int(post_id)
    except (TypeError, ValueError):
        return CapabilityResult(ok=False, status="failed_final", error={"type": "input_not_allowed"})
    if pid <= 0:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "input_not_allowed"})

    # Bind the gate to this exact post_id: an approval for one post can never
    # authorize publishing a different post on the same site (mirrors the plugin's
    # per-post gate meta). The raiser must set brief.hostops_resource = str(post_id).
    approved = _gate(gate_id, "publish_post", site, resource=pid, post_id=pid)
    if approved is None:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "gate_required"})
    if approved == "autonomous":
        # Fail closed: a live publish must never resolve autonomously (§9).
        return CapabilityResult(ok=False, status="failed_final",
                                error={"type": "autonomous_publish_forbidden"})

    try:
        target = _context(site)
        url = _site_public_url(site)
        secret = _read_payload_secret(site)
        opener = gate_opener or _open_publish_gate
        gate = opener(url, pid, secret, resolver, approved)
        if not gate.get("gate_open"):
            return CapabilityResult(ok=False, status="failed_recoverable",
                                    error={"type": "publish_gate_not_opened",
                                           "status_code": gate.get("status_code")})
        proof = (transport or OpenSshTransport()).publish_post(target, pid)
        if proof.get("published"):
            evidence = {"post_id": pid, "post_status": proof.get("post_status"),
                        "authorization": "gate", "gate_id": approved, "resolver": resolver}
            return CapabilityResult(ok=True, status="succeeded",
                                    data={"identity": target.audit_identity, "post_id": pid},
                                    verification={"verified": True, "evidence": evidence},
                                    transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "publish_failed", "post_status": proof.get("post_status")})
    except (HostOpsTargetError, OSError, subprocess.SubprocessError, RuntimeError, httpx.HTTPError) as exc:
        return _safe_error(exc)

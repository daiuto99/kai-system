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
# AR-2 fleet-host secret rail: the allowlist of fleet hosts a named secret may be
# placed onto (NOT wordpress_sites.json — fleet hosts are our own machines, not
# Cloudways apps). Only host keys present here are addressable.
_FLEET_HOSTS_JSON = Path("/vault/00_System/fleet_hosts.json")
_PLUGIN_ALLOWLIST = {"kai-publish-gate"}
_OP_ALLOWLIST = {"status", "verify", "place_secret", "deploy_plugin", "publish_post", "update_wp", "place_fleet_secret"}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_SECRET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
# A fleet target's ssh_key / dest_dir come from the (trusted, KAI-authored)
# allowlist, but are still shape-bounded so a malformed entry can never inject an
# argv token or a path-traversal into the fixed SSH command. Absolute path, no
# shell metacharacters, no "..".
_SAFE_ABS_PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")
# CUR-5 update-apply target: the literal "core" or a strict WordPress plugin
# slug (lowercase alnum + dashes). No allowlist — the human gate binds the exact
# component per approval; this only bounds the shape so it is argv-injection-safe
# once shlex-quoted. WP updates are NEVER auto-applied (plan §2.4 hard rule).
_WP_UPDATE_CORE = "core"
_SAFE_PLUGIN_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Read-back version tokens are echoed into the audit evidence, so they are
# allowlisted to a strict, LENGTH-BOUNDED version grammar — arbitrary site-side
# stdout (a stray `DB_PASSWORD=...`, a non-dotted `1hunter2`, or a megabyte of
# text) can never be reflected into a persisted result (L18).
_MAX_VERSION_LEN = 32
_SAFE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9A-Za-z]+)*(?:[-+][0-9A-Za-z.]+)?$")


def _valid_update_component(component: object) -> bool:
    """A CUR-5 update target is 'core' or a well-formed plugin slug."""
    return component == _WP_UPDATE_CORE or (
        isinstance(component, str) and bool(_SAFE_PLUGIN_SLUG.fullmatch(component))
    )


def _clean_version(token: str) -> str:
    """Only a short, version-shaped token survives into a result; else 'unknown'."""
    if not token or len(token) > _MAX_VERSION_LEN or not _SAFE_VERSION.fullmatch(token):
        return "unknown"
    return token

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
    def update_wp(self, target: HostOpsTarget, component: str) -> dict: ...


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

    def update_wp(self, target: HostOpsTarget, component: str) -> dict:
        """Apply the available WordPress core OR single-plugin update via wp-cli,
        proving an ACTUAL version transition — not merely that it is current after.

        ``component`` is either the literal "core" or a strict plugin slug
        (validated + shlex-quoted by the caller), so the remote command carries
        no caller-controlled free text. Only the named component is touched:
        `wp plugin update <slug>` / `wp core update` — never `--all`.

        Honesty (fixes the "already-current reads as applied" foot-gun): read the
        installed version BEFORE and AFTER, and query remaining availability.
        `updated` is True only when the version actually changed; `current` is
        True only when no update remains. An already-current no-op therefore
        reports updated=False, never a faked success. The result carries only
        allowlisted, version-shaped tokens — never raw site-side stdout, and on
        timeout never the SSH argv (L18).
        """
        wr = shlex.quote(target.webroot)
        if component == _WP_UPDATE_CORE:
            remaining_current = "0"  # `core check-update --format=count` -> 0 when current
            # NO `|| echo 0` fallback: if the post-update verification itself fails,
            # the && chain breaks and printf never runs, so the parse is 'unparsable'
            # and we fail CLOSED — a broken check is never read as "current" (honesty).
            remote = (
                f"cd {wr} "
                f"&& before=$(wp core version) "
                f"&& wp core update --quiet "
                f"&& after=$(wp core version) "
                f"&& rem=$(wp core check-update --format=count 2>/dev/null) "
                f"&& printf '%s\\n%s\\n%s\\n' \"$before\" \"$after\" \"$rem\""
            )
        else:
            slug = shlex.quote(component)
            remaining_current = "none"  # `plugin list --field=update` -> 'none' when current
            # `update` is a `wp plugin list` field, NOT a `wp plugin get` field —
            # querying it via `get` exits nonzero and would fail-close a genuinely
            # successful update after the gate is already consumed.
            remote = (
                f"cd {wr} "
                f"&& before=$(wp plugin get {slug} --field=version) "
                f"&& wp plugin update {slug} --quiet "
                f"&& after=$(wp plugin get {slug} --field=version) "
                f"&& upd=$(wp plugin list --name={slug} --field=update) "
                f"&& printf '%s\\n%s\\n%s\\n' \"$before\" \"$after\" \"$upd\""
            )
        try:
            completed = self._runner(self._ssh(remote), capture_output=True, text=True,
                                     timeout=180, check=False)
        except subprocess.TimeoutExpired:
            # Never surface the argv (master login / IP / key path) — fixed token only (L18).
            return {"updated": False, "current": False, "component": component, "reason": "timeout"}
        if completed.returncode != 0:
            return {"updated": False, "current": False, "component": component, "reason": "wp_cli_error"}
        # The printf trio is the LAST three lines — any --quiet-leaking chatter is ignored.
        lines = [ln.strip() for ln in (completed.stdout or "").splitlines() if ln.strip()]
        tail = lines[-3:]
        if len(tail) < 3:
            return {"updated": False, "current": False, "component": component, "reason": "unparsable"}
        before, after = _clean_version(tail[0]), _clean_version(tail[1])
        remaining = tail[2].lower()
        verifiable = before != "unknown" and after != "unknown"
        updated = verifiable and before != after
        current = remaining == remaining_current
        if updated and current:
            reason = ""
        elif not verifiable:
            reason = "version_unverifiable"   # a version token was unreadable — can't prove the transition
        elif not updated:
            reason = "no_version_change"      # nothing landed (already current / no-op)
        else:
            reason = "update_incomplete"      # a real transition, but an update still remains
        return {"updated": updated, "current": current, "component": component,
                "before": before, "after": after, "reason": reason}


# ── AR-2 fleet-host secret rail ───────────────────────────────────────────────
# Placing an EXISTING named secret onto one of our own fleet hosts (starting with
# 71-kai-mini). Distinct from the Cloudways/WP rail above: the target is a plain
# Linux host, the transport is a fixed `install -m600` over SSH with the bytes on
# stdin only, and there is no wp-cli / webroot. Reuses the SAME gate / policy /
# audit / resolver spine — no second approval surface.


@dataclass(frozen=True)
class HostOpsFleetTarget:
    host_key: str          # the allowlist key, e.g. "kai-mini" (audit identity)
    host: str              # ssh host / IP
    ssh_user: str
    ssh_key: str           # path to the mounted SSH key inside the orchestrator
    dest_dir: str          # directory the secret file is written into (0600)

    @property
    def audit_identity(self) -> str:
        """Resolved fleet identity used for the gate and audit trail, not SSH auth."""
        return f"fleet-host:{self.host_key}"


class FleetSshTransport:
    """Fixed `install -m600` OpenSSH invocation onto a fleet host; the secret bytes
    flow ONLY on stdin, never an argv/log/result token (L18). No caller-controlled
    command — dest_dir comes from the allowlist and secret_name is allowlist-shaped
    and shlex-quoted, so the remote command is fixed syntax over trusted identifiers.
    """

    def __init__(self, runner: Callable = subprocess.run):
        self._runner = runner

    @staticmethod
    def _ssh_opts(key_path: str) -> list[str]:
        return [
            "-i", key_path,
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "LogLevel=ERROR",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=15",
        ]

    def place_secret(self, target: HostOpsFleetTarget, secret: bytes, secret_name: str) -> dict:
        """Write the secret to <dest_dir>/<secret_name> as mode 0600, then read the
        mode back. The value is passed on stdin and compared/echoed nowhere (L18):
        the only thing read back is the octal file mode, never the file contents.
        """
        dest = f"{target.dest_dir.rstrip('/')}/{secret_name}"
        q_dir = shlex.quote(target.dest_dir.rstrip("/"))
        q_dest = shlex.quote(dest)
        # umask 077 + install -m600 => the file never exists group/other-readable,
        # even for the instant between create and chmod. `stat -c %a` is the ONLY
        # value that returns — the secret itself is never catted back.
        remote = (
            f"mkdir -p -m 700 {q_dir} && umask 077 && "
            f"install -m 600 /dev/stdin {q_dest} && stat -c %a {q_dest}"
        )
        login = f"{target.ssh_user}@{target.host}"
        argv = ["ssh"] + self._ssh_opts(target.ssh_key) + [login, remote]
        value = secret.decode("ascii")
        try:
            completed = self._runner(argv, input=value, capture_output=True, text=True,
                                     timeout=30, check=False)
        except subprocess.TimeoutExpired:
            # Never surface the argv (host / user / key path) — fixed token only (L18).
            return {"written": False, "read_back": False, "name": secret_name, "reason": "timeout"}
        mode = (completed.stdout or "").strip().splitlines()[-1:] or [""]
        return {"written": completed.returncode == 0,
                "read_back": completed.returncode == 0 and mode[0] == "600",
                "name": secret_name}


def _fleet_target(host_key: str) -> HostOpsFleetTarget:
    if not isinstance(host_key, str) or not _SAFE_COMPONENT.fullmatch(host_key):
        raise HostOpsTargetError("invalid fleet host key")
    try:
        hosts = json.loads(_FLEET_HOSTS_JSON.read_text()).get("hosts", {})
    except (OSError, json.JSONDecodeError) as exc:
        raise HostOpsTargetError("fleet host configuration unavailable") from exc
    entry = hosts.get(host_key, {})
    host = str(entry.get("host", ""))
    ssh_user = str(entry.get("ssh_user", ""))
    ssh_key = str(entry.get("ssh_key", ""))
    dest_dir = str(entry.get("dest_dir", ""))
    if not (host and ssh_user and ssh_key and dest_dir):
        raise HostOpsTargetError("fleet host is not on the allowlist")
    if not all(_SAFE_COMPONENT.fullmatch(v) for v in (host, ssh_user)):
        raise HostOpsTargetError("fleet host target is invalid")
    if not (_SAFE_ABS_PATH.fullmatch(ssh_key) and _SAFE_ABS_PATH.fullmatch(dest_dir)):
        raise HostOpsTargetError("fleet host path is invalid")
    if ".." in ssh_key or ".." in dest_dir:
        raise HostOpsTargetError("fleet host path traversal rejected")
    return HostOpsFleetTarget(host_key=host_key, host=host, ssh_user=ssh_user,
                              ssh_key=ssh_key, dest_dir=dest_dir)


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


@capability("hostops.place_fleet_secret")
def place_fleet_secret(host: str, secret_name: str, secret: InMemorySecret, gate_id: object = None,
                       *, transport: "FleetSshTransport | None" = None, **_) -> CapabilityResult:
    """Place an existing named secret onto an allowlisted fleet host — ONLY behind a
    human gate (AR-2 authorized-execution-path: KAI holds and moves secrets by name,
    Leo taps to authorize, every move audited).

    Placing a secret onto a host is inherently sensitive, so — mirroring the publish
    and WP-update floors — it is NEVER autonomous: policy is `requires_approval`, and
    this capability additionally fails closed if it is ever handed an autonomous
    authorization (defence against org-model drift). The gate is bound to the exact
    (host, secret_name), so one approval can never place a different secret or reach
    a different host. Flow: validate -> consume the resolved human gate bound to
    (host, secret_name) -> write 0600 via stdin -> read the file MODE (never value) back.
    """
    if not _SAFE_SECRET_NAME.fullmatch(secret_name) or not isinstance(secret, InMemorySecret):
        return CapabilityResult(ok=False, status="failed_final", error={"type": "input_not_allowed"})
    # Bind the gate to this exact (host, secret_name): an approval to place secret X
    # on host Y can never authorize placing a different secret or reaching a different
    # host. The raiser must set brief.hostops_resource = secret_name.
    approved = _gate(gate_id, "place_fleet_secret", host, resource=secret_name, secret_name=secret_name)
    if approved is None:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "gate_required"})
    if approved == "autonomous":
        # Fail closed: a secret placement must never resolve autonomously (AR-2).
        return CapabilityResult(ok=False, status="failed_final",
                                error={"type": "autonomous_fleet_secret_forbidden"})
    try:
        target = _fleet_target(host)
        proof = (transport or FleetSshTransport()).place_secret(target, secret.material, secret_name)
        if proof.get("written") and proof.get("read_back"):
            evidence = {"host": target.host_key, "secret_name": secret_name, "mode": "600",
                        "authorization": "gate", "gate_id": approved}
            return CapabilityResult(ok=True, status="succeeded",
                                    data={"identity": target.audit_identity, "secret_name": secret_name},
                                    verification={"verified": True, "evidence": evidence},
                                    transport_used="hostops_fleet_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable", error={"type": "hostops_fleet_write_failed"})
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


@capability("hostops.update_wp")
def update_wp(site: str, component: object, gate_id: object = None,
              *, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Apply a WordPress core/plugin update on a live site — ONLY behind a human gate.

    CUR-5 (System Currency Program): the apply half of the currency loop. A bad
    plugin/core update can break a live client site, so — mirroring the drafts-only
    publish floor — it is NEVER autonomous: the gate must be an explicit human
    approval bound to this exact ``component``, and the capability additionally
    fails closed if it is ever handed an autonomous authorization (defence against
    org-model drift). Flow: validate component -> consume the resolved human gate
    bound to (site, component) -> wp-cli update the single named component ->
    read back that no update remains. Report-only readers stay separate; nothing
    here scans, and nothing scans here.
    """
    if not _valid_update_component(component):
        return CapabilityResult(ok=False, status="failed_final", error={"type": "input_not_allowed"})
    component = str(component)

    # Bind the gate to this exact component: an approval to update one plugin can
    # never authorize updating core or a different plugin on the same site.
    approved = _gate(gate_id, "update_wp", site, resource=component, component=component)
    if approved is None:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "gate_required"})
    if approved == "autonomous":
        # Fail closed: WP updates are never auto-applied (plan §2.4 hard rule).
        return CapabilityResult(ok=False, status="failed_final",
                                error={"type": "autonomous_wp_update_forbidden"})
    try:
        target = _context(site)
        proof = (transport or OpenSshTransport()).update_wp(target, component)
        # Success requires a proven version transition AND nothing left to apply —
        # an already-current no-op or a silently-failed update is never "succeeded".
        if proof.get("updated") and proof.get("current"):
            evidence = {"component": component, "current": True,
                        "before": proof.get("before"), "after": proof.get("after"),
                        "authorization": "gate", "gate_id": approved}
            return CapabilityResult(ok=True, status="succeeded",
                                    data={"identity": target.audit_identity, "component": component},
                                    verification={"verified": True, "evidence": evidence},
                                    transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "wp_update_failed", "component": component,
                                       "reason": proof.get("reason") or "unverified"})
    except subprocess.TimeoutExpired:
        # Explicit: a timeout must never reach _safe_error(str(exc)) — that would
        # serialize the SSH argv (master login / IP / key path) into the result (L18).
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "wp_update_timeout", "component": component})
    except (HostOpsTargetError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)

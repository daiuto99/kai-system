"""Narrow, allowlisted Cloudways app host-operations for KAI-820 HOSTOPS-(b).

There is deliberately no generic command capability.  Private deploy-key bytes
are loaded only through ``DeployKeyLoader.with_material`` and never placed in an
argv, log record, or CapabilityResult.  The SSH client receives the validated key
*path* as ``-i`` (the approved CLI alternative); secret content reaches a remote
write only over stdin.

HOSTOPS-(c) must replace the opaque ``VerifiedGate`` seam with a council-derived
handle before either mutation can be reached from a workflow.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from models import CapabilityResult
from hostops_identity import DeployKeyHandle, DeployKeyLoader, HostOpsIdentityError, HostOpsIdentityResolver
from . import capability


_SITES_JSON = Path("/vault/00_System/wordpress_sites.json")
_PLUGIN_ALLOWLIST = {"kai-publish-gate"}
_OP_ALLOWLIST = {"status", "verify", "place_secret", "deploy_plugin"}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_SECRET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class VerifiedGate:
    """Opaque approval evidence; HOSTOPS-(c) owns construction after council verification."""
    gate_id: str
    _verified: bool = True


@dataclass(frozen=True)
class InMemorySecret:
    """A short-lived secret transport wrapper, never serialized into a workflow record."""
    material: bytes


@dataclass(frozen=True)
class HostOpsTarget:
    host: str
    app_user: str
    app_id: str


class SshTransport(Protocol):
    def verify(self, handle: DeployKeyHandle, target: HostOpsTarget, key_material: bytes) -> dict: ...
    def place_secret(self, handle: DeployKeyHandle, target: HostOpsTarget, name: str, secret: bytes, key_material: bytes) -> dict: ...
    def deploy_plugin(self, handle: DeployKeyHandle, target: HostOpsTarget, plugin: str, key_material: bytes) -> dict: ...


class OpenSshTransport:
    """Fixed OpenSSH invocations only; no caller-controlled command or argv field."""

    def __init__(self, runner: Callable = subprocess.run):
        self._runner = runner

    @staticmethod
    def _base(handle: DeployKeyHandle, target: HostOpsTarget) -> list[str]:
        # The key path is safe only after DeployKeyLoader.with_material validated it.
        return ["ssh", "-i", str(handle.secret_path), "-o", "BatchMode=yes", "-o",
                "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
                f"{target.app_user}@{target.host}"]

    def verify(self, handle, target, key_material):
        # `true` has a deterministic exit status: zero only after authentication.
        # A timeout is never evidence of a usable credential.
        try:
            completed = self._runner(self._base(handle, target) + ["true"],
                                     capture_output=True, text=True,
                                     timeout=15, check=False)
        except subprocess.TimeoutExpired:
            return {"authenticated": False, "probe": "ssh_true", "reason": "timeout"}
        return {"authenticated": completed.returncode == 0, "probe": "ssh_true"}

    def place_secret(self, handle, target, name, secret, key_material):
        remote = f"umask 077; cat > /home/{target.app_user}/{name}; chmod 600 /home/{target.app_user}/{name}; test -f /home/{target.app_user}/{name}"
        completed = self._runner(self._base(handle, target) + [remote], input=secret,
                                 capture_output=True, timeout=20, check=False)
        return {"written": completed.returncode == 0, "read_back": completed.returncode == 0, "name": name}

    def deploy_plugin(self, handle, target, plugin, key_material):
        # The source and destination are fixed by the allowlisted plugin name.
        artifact = f"/opt/kai-plugins/{plugin}.php"
        destination = f"/home/{target.app_user}/public_html/wp-content/mu-plugins/{plugin}.php"
        completed = self._runner(["scp", "-i", str(handle.secret_path), "-o", "BatchMode=yes",
                                  "-o", "StrictHostKeyChecking=yes", artifact,
                                  f"{target.app_user}@{target.host}:{destination}"],
                                 capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0:
            return {"deployed": False, "read_back": False, "plugin": plugin}
        readback = self._runner(self._base(handle, target) + [f"test -f {destination}"],
                                capture_output=True, text=True, timeout=20, check=False)
        return {"deployed": True, "read_back": readback.returncode == 0, "plugin": plugin}


def _target(site_key: str, handle: DeployKeyHandle) -> HostOpsTarget:
    try:
        site = json.loads(_SITES_JSON.read_text()).get("sites", {}).get(site_key, {})
        host = str(site.get("cloudways_fqdn", ""))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostOpsIdentityError("hostops site configuration unavailable") from exc
    if not _SAFE_COMPONENT.fullmatch(host):
        raise HostOpsIdentityError("hostops target host is invalid or unavailable")
    return HostOpsTarget(host=host, app_user=handle.cloudways_sys_user, app_id=handle.cloudways_app_id)


def _safe_error(exc: Exception) -> CapabilityResult:
    return CapabilityResult(ok=False, status="failed_final", error={"type": "hostops_unavailable", "message": str(exc)})


def _gate(gate_id: object) -> str | None:
    if isinstance(gate_id, VerifiedGate) and gate_id._verified and gate_id.gate_id:
        return gate_id.gate_id
    return None


def _context(site: str, resolver=None, loader=None):
    resolver = resolver or HostOpsIdentityResolver()
    loader = loader or DeployKeyLoader()
    handle = resolver.resolve(site)
    return handle, loader, _target(site, handle)


@capability("hostops.status")
def status(site: str, *, resolver=None, loader=None, **_) -> CapabilityResult:
    """Validate key presence/mode/owner without reading private-key bytes."""
    try:
        resolver = resolver or HostOpsIdentityResolver()
        loader = loader or DeployKeyLoader()
        handle = resolver.resolve(site)
        loader.validate(handle)
        return CapabilityResult(ok=True, status="succeeded", data={"identity": handle.audit_identity, "key_present": True},
                                verification={"verified": True, "evidence": {"mode": "0600", "owner_uid": loader._runtime_uid}})
    except (HostOpsIdentityError, OSError) as exc:
        return _safe_error(exc)


@capability("hostops.verify")
def verify(site: str, *, resolver=None, loader=None, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Perform a no-command SSH authentication probe through the loader boundary."""
    try:
        handle, loader, target = _context(site, resolver, loader)
        proof = loader.with_material(handle, lambda material: (transport or OpenSshTransport()).verify(handle, target, material))
        if proof.get("authenticated"):
            return CapabilityResult(ok=True, status="succeeded", data={"identity": handle.audit_identity}, verification={"verified": True, "evidence": proof}, transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable", error={"type": "ssh_auth_failed", "identity": handle.audit_identity})
    except (HostOpsIdentityError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)


@capability("hostops.place_secret")
def place_secret(site: str, secret_name: str, secret: InMemorySecret, gate_id: object = None, *, resolver=None, loader=None, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Place an in-memory secret through stdin only after a verified gate handle."""
    approved = _gate(gate_id)
    if approved is None:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "gate_required", "message": "HOSTOPS-(c) verified gate handle required"})
    if not _SAFE_SECRET_NAME.fullmatch(secret_name) or not isinstance(secret, InMemorySecret):
        return CapabilityResult(ok=False, status="failed_final", error={"type": "input_not_allowed"})
    try:
        handle, loader, target = _context(site, resolver, loader)
        proof = loader.with_material(handle, lambda material: (transport or OpenSshTransport()).place_secret(handle, target, secret_name, secret.material, material))
        if proof.get("written") and proof.get("read_back"):
            return CapabilityResult(ok=True, status="succeeded", data={"identity": handle.audit_identity, "secret_name": secret_name}, verification={"verified": True, "evidence": {**proof, "gate_id": approved}}, transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable", error={"type": "hostops_write_failed"})
    except (HostOpsIdentityError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)


@capability("hostops.deploy_plugin")
def deploy_plugin(site: str, plugin: str, gate_id: object = None, *, resolver=None, loader=None, transport: SshTransport | None = None, **_) -> CapabilityResult:
    """Deploy only a named, allowlisted plugin after a verified gate handle."""
    approved = _gate(gate_id)
    if approved is None:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "gate_required", "message": "HOSTOPS-(c) verified gate handle required"})
    if plugin not in _PLUGIN_ALLOWLIST:
        return CapabilityResult(ok=False, status="failed_final", error={"type": "plugin_not_allowed"})
    try:
        handle, loader, target = _context(site, resolver, loader)
        proof = loader.with_material(handle, lambda material: (transport or OpenSshTransport()).deploy_plugin(handle, target, plugin, material))
        if proof.get("deployed") and proof.get("read_back"):
            return CapabilityResult(ok=True, status="succeeded", data={"identity": handle.audit_identity, "plugin": plugin}, verification={"verified": True, "evidence": {**proof, "gate_id": approved}}, transport_used="hostops_ssh")
        return CapabilityResult(ok=False, status="failed_recoverable", error={"type": "plugin_deploy_failed"})
    except (HostOpsIdentityError, OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return _safe_error(exc)

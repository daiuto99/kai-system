"""HOSTOPS-(a): per-app Cloudways deploy-key identity and secret-store seam.

This module deliberately resolves *handles*, never private-key bytes. Future
hostops capabilities must use ``DeployKeyLoader.with_material`` so a key is
read only at the transport boundary and is never placed in a command argument,
workflow result, or log record.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_SITES = Path("/vault/00_System/wordpress_sites.json")
_DEFAULT_KEY_DIR = Path("/run/hostops-deploy-keys")
_T = TypeVar("_T")


class HostOpsIdentityError(RuntimeError):
    """A safe-to-report identity/secret-store failure; never includes key bytes."""


@dataclass(frozen=True)
class DeployKeyHandle:
    """Scoped reference to one Cloudways application's private deploy key."""

    site_key: str
    cloudways_app_id: str
    cloudways_sys_user: str
    secret_path: Path

    @property
    def key_name(self) -> str:
        return self.secret_path.name

    @property
    def audit_identity(self) -> str:
        return f"cloudways-app:{self.cloudways_app_id}:{self.cloudways_sys_user}"


def deploy_key_filename(cloudways_app_id: str, cloudways_sys_user: str) -> str:
    """Return the deterministic, rotatable secret filename for one application."""
    if not _SAFE_COMPONENT.fullmatch(str(cloudways_app_id)):
        raise HostOpsIdentityError("invalid Cloudways app id for deploy-key handle")
    if not _SAFE_COMPONENT.fullmatch(str(cloudways_sys_user)):
        raise HostOpsIdentityError("invalid Cloudways app user for deploy-key handle")
    return f"cloudways-app-{cloudways_app_id}-{cloudways_sys_user}.ed25519"


class HostOpsIdentityResolver:
    """Resolve a configured WP site to its deploy-key handle, never key bytes."""

    def __init__(self, sites_path: Path = _DEFAULT_SITES, key_dir: Path = _DEFAULT_KEY_DIR):
        self._sites_path = Path(sites_path)
        self._key_dir = Path(key_dir)

    def resolve(self, site_key: str) -> DeployKeyHandle:
        try:
            sites = json.loads(self._sites_path.read_text()).get("sites", {})
        except (OSError, json.JSONDecodeError) as exc:
            raise HostOpsIdentityError("hostops site configuration unavailable") from exc

        site = sites.get(site_key)
        if not isinstance(site, dict):
            raise HostOpsIdentityError(f"unknown hostops site '{site_key}'")
        app_id = str(site.get("cloudways_app_id", ""))
        app_user = str(site.get("cloudways_sys_user", ""))
        filename = deploy_key_filename(app_id, app_user)
        return DeployKeyHandle(site_key, app_id, app_user, self._key_dir / filename)


class DeployKeyLoader:
    """The only secret-store reader. It keeps private-key material out of logs/results."""

    def __init__(self, key_dir: Path = _DEFAULT_KEY_DIR, runtime_uid: int | None = None):
        self._key_dir = Path(key_dir).resolve()
        self._runtime_uid = os.geteuid() if runtime_uid is None else runtime_uid

    def with_material(self, handle: DeployKeyHandle, consumer: Callable[[bytes], _T]) -> _T:
        """Pass one private key to a transport callback without exposing it in a result."""
        resolved = self.validate(handle)
        try:
            material = resolved.read_bytes()
            if not material:
                raise HostOpsIdentityError(f"deploy key {handle.audit_identity} is empty")
        except HostOpsIdentityError:
            raise
        except OSError as exc:
            raise HostOpsIdentityError(f"deploy key unavailable for {handle.audit_identity}") from exc

        # Capability (b) supplies the SSH transport callback. Do not log or return
        # material from this module; the callback's return is the only result.
        return consumer(material)

    def validate(self, handle: DeployKeyHandle) -> Path:
        """Validate a key handle without reading its private-key bytes.

        Read-only hostops.status uses this path to report mount health without
        placing credential material in process memory.
        """
        path = handle.secret_path
        try:
            resolved = path.resolve(strict=True)
            if resolved.parent != self._key_dir:
                raise HostOpsIdentityError("deploy-key handle is outside the hostops secret store")
            metadata = resolved.stat()
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise HostOpsIdentityError(f"deploy key {handle.audit_identity} does not have mode 0600")
            if metadata.st_uid != self._runtime_uid:
                raise HostOpsIdentityError(f"deploy key {handle.audit_identity} is not owned by the runtime user")
        except HostOpsIdentityError:
            raise
        except OSError as exc:
            raise HostOpsIdentityError(f"deploy key unavailable for {handle.audit_identity}") from exc
        return resolved

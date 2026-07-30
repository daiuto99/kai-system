"""
provision_source — the live server-side SecretSource for the authorized provisioning path (KAI-984).

Implements the `provision_capability.SecretSource` Protocol: read the NAMED secret from the worker's
secret store and hand back its raw bytes. The value is read only AFTER the capability has confirmed
policy + a fresh approval; this boundary is where it first enters memory.

Hard contract (design R5 — no value in transcript, ever):
  - NEVER log, print, format, or otherwise render the value.
  - NEVER raise an exception whose text could embed the value — this method returns None on ANY
    problem instead of raising, so the capability records `denied_unavailable` and moves nothing.
  - Provisioning is a BYTE-IDENTICAL copy: return the file's exact bytes (no strip/normalize), so
    the node's `/run/secrets/<name>` becomes an exact replica of the worker's, matching how Docker
    secrets are consumed. An empty/zero-byte read => None (treated as unavailable, fail-closed).

Defense in depth: the secret name is re-validated as a bare identifier here even though
`provision_policy` already bounds it — a name with a path separator can never escape the secrets dir.
"""
from __future__ import annotations

import stat
from pathlib import Path

# A provisionable secret name is a bare identifier — no path parts, no separators (mirrors
# provision_policy._SAFE_NAME). This makes `<dir>/<name>.txt` un-escapable.
_SAFE_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)

_DEFAULT_SECRETS_DIR = "/home/leo/kai-system/secrets"


class FileSecretSource:
    """Reads `<secrets_dir>/<secret_name>.txt`, owner-only (mode 0600), returns exact bytes."""

    def __init__(self, secrets_dir: str = _DEFAULT_SECRETS_DIR, *, require_owner_only: bool = True) -> None:
        self._dir = Path(secrets_dir)
        self._require_owner_only = require_owner_only

    def read(self, secret_name: str) -> bytes | None:
        # Any failure below returns None (never raises) so the value can never reach an exception.
        if not isinstance(secret_name, str) or not secret_name:
            return None
        if any(ch not in _SAFE_NAME for ch in secret_name):
            return None
        path = self._dir / f"{secret_name}.txt"
        try:
            st = path.stat()
            if not stat.S_ISREG(st.st_mode):
                return None
            # Owner-only: a world/group-readable secret file is untrusted — fail closed.
            if self._require_owner_only and (stat.S_IMODE(st.st_mode) & 0o077):
                return None
            data = path.read_bytes()
        except BaseException:  # noqa: BLE001 — a read failure must never surface bytes; treat as absent
            return None
        return data if data else None

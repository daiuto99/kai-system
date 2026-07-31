"""
provision_transport — the live tailnet SSH Transport for the authorized provisioning path (KAI-984).

Implements the `provision_capability.Transport` Protocol: write the secret `material` to the target
KAI node's secret store over the tailnet and verify by read-back, returning ONLY booleans. This is
the last live adapter; the pure security decisions (policy, tailnet guard, orchestration, audit) are
already Codex-verified — this adapter is thin and cannot change any of them.

It mirrors the Codex-verified `kai-orchestrator/capabilities/hostops.py::OpenSshTransport` exactly:
  - FIXED ssh argv + a single fixed remote shell command; NO caller-controlled command/argv.
  - the secret VALUE flows ONLY on stdin — never in argv, never in the remote command string, never
    in a process listing (L18). The remote command contains only fixed syntax + the validated bare
    secret NAME + the shell-quoted config dir.
  - verification is by SHA-256: the remote `sha256sum` of the written file is compared to the local
    digest of `material`. A hash is not the value, so read-back proves byte-identity WITHOUT the
    value ever travelling back over the wire or into this process's output (R5).
  - the injected `runner` (default subprocess.run) makes every deny/verify path testable with no
    live SSH, node, or tailnet.

Fail-closed contract (design §4, R5): return {"written": bool, "verified": bool} and NOTHING else.
Any ambiguity — bad name, non-tailnet IP, ssh error, timeout, hash mismatch, unexpected output —
resolves to written/verified False. The value is never returned, logged, or embedded in an exception.

Defense in depth (the capability already pins these, but a transport must not trust blindly):
  - `tailnet_ip` MUST be a single address inside 100.64.0.0/10 (Tailscale CGNAT) — else no runner
    call happens at all (§4.3 transport pin).
  - `secret_name` MUST be a bare identifier (mirrors provision_source / provision_policy) so the
    remote `<dir>/<name>.txt` path can never escape the secrets dir.

The remote write is atomic and owner-only: written to a `mktemp` sibling under `umask 077`, then
`mv -f` into place (same filesystem => atomic rename), so a partial/interrupted transfer can never
leave a truncated secret at the real path.
"""
from __future__ import annotations

import ipaddress
import shlex
import subprocess
from hashlib import sha256
from typing import Callable

import tailnet_guard  # inc1 — reuse the single CGNAT_V4 source, never redefine the range

# A provisionable secret name is a bare identifier — mirrors provision_source._SAFE_NAME and
# provision_policy so `<dir>/<name>.txt` is un-escapable and the name is safe to interpolate into
# the fixed remote command (no shell metacharacters possible).
_SAFE_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)

# Symmetric with FileSecretSource: the node's store mirrors the worker's `<dir>/<name>.txt`, mode
# 0600. Override per deployment at wiring time; CONFIRM before any live provisioning.
_DEFAULT_REMOTE_SECRETS_DIR = "/home/leo/kai-system/secrets"
_DEFAULT_SSH_USER = "leo"
_DEFAULT_SSH_KEY = "/home/leo/.ssh/kai_worker"

# Fixed, non-interactive SSH options (mirrors hostops OpenSshTransport). BatchMode so a missing key
# fails fast instead of prompting; a short ConnectTimeout so a dead node denies rather than hangs.
_SSH_OPTS_TAIL = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=15",
]


class OpenSshSecretTransport:
    """`Transport`: copy the named secret's bytes to `<remote_dir>/<name>.txt` on the tailnet node.

    Args:
        runner: subprocess.run-compatible callable (injected for testing).
        remote_secrets_dir / ssh_user / ssh_key: node-side placement + SSH identity (confirm-before-live).
        timeout_s: hard wall-clock bound on the SSH call, then fail-closed.
    """

    def __init__(
        self,
        runner: Callable = subprocess.run,
        *,
        remote_secrets_dir: str = _DEFAULT_REMOTE_SECRETS_DIR,
        ssh_user: str = _DEFAULT_SSH_USER,
        ssh_key: str = _DEFAULT_SSH_KEY,
        timeout_s: float = 30.0,
    ) -> None:
        self._runner = runner
        self._dir = remote_secrets_dir
        self._user = ssh_user
        self._key = ssh_key
        self._timeout = timeout_s

    def _remote_command(self, secret_name: str, expected: str) -> str:
        # VERIFY-BEFORE-INSTALL: write to a mktemp sibling under umask 077 (0600), hash the TEMP,
        # and `mv` into place ONLY if that hash equals the caller's `expected` digest. So a truncated
        # / corrupted / hash-mismatched transfer NEVER reaches the real path — `mv` is gated on
        # integrity, and on any failure the trap removes the temp so nothing is installed ("moves
        # nothing on failure" holds by construction). On success the shell echoes `OK:<hash>` — the
        # single literal the caller matches. `expected` is a 64-char hex digest (NOT the secret), safe
        # to pass in the command. `secret_name` is a validated bare identifier, safe to interpolate.
        d = shlex.quote(self._dir)
        f = shlex.quote(f"{self._dir}/{secret_name}.txt")
        e = shlex.quote(expected)
        tmpl = shlex.quote(self._dir + "/.provision.XXXXXX")
        return (
            "set -e; umask 077; "
            f"mkdir -p {d}; "
            f"t=$(mktemp {tmpl}); "
            "trap 'rm -f \"$t\"' EXIT; "
            'cat > "$t"; chmod 600 "$t"; '
            'h=$(sha256sum "$t" | cut -c1-64); '
            f'[ "$h" = {e} ] || {{ echo MISMATCH; exit 3; }}; '
            f'mv -f "$t" {f}; trap - EXIT; '
            'echo "OK:$h"'
        )

    def _ssh_argv(self, tailnet_ip: str, remote: str) -> list[str]:
        return (
            ["ssh", "-i", self._key] + _SSH_OPTS_TAIL
            + [f"{self._user}@{tailnet_ip}", remote]
        )

    def provision(self, *, tailnet_ip: str, secret_name: str, material: bytes) -> dict:
        deny = {"written": False, "verified": False}

        # EXACT-TYPE boundary (not isinstance): a hostile str/bytes SUBCLASS can pass a validate-time
        # check yet lie at use-time — its __format__/__iter__/__bytes__/__len__ returning different
        # content when the value is actually placed into the argv, the remote path, or stdin. In the
        # real composed path every input is a true primitive (argparse str; capability's
        # bytes(memoryview(...))), so requiring the exact builtin type loses nothing and kills the
        # whole parse-vs-use spoof class (Codex inc4 findings #1/#3/#4/#5).
        if type(secret_name) is not str or not secret_name:
            return deny
        if any(ch not in _SAFE_NAME for ch in secret_name):
            return deny
        if type(tailnet_ip) is not str:
            return deny
        if type(material) is not bytes or len(material) == 0:
            return deny

        # Canonicalize the IP through ipaddress and USE that canonical string in the argv (never the
        # caller's object) — closes the parse/use split: the address that passed the CGNAT check IS
        # the address we ssh to.
        try:
            ip_obj = ipaddress.ip_address(tailnet_ip)
        except BaseException:  # noqa: BLE001 — malformed => fail-closed
            return deny
        if ip_obj not in tailnet_guard.CGNAT_V4:
            return deny
        ip_canon = str(ip_obj)

        # Local digest of the exact bytes we send (same object goes to stdin below) — the reference
        # the remote must reproduce before it installs anything.
        try:
            expected = sha256(material).hexdigest()
        except BaseException:  # noqa: BLE001 — fail-closed, move nothing
            return deny

        try:
            # Command/argv construction is INSIDE the guard so a hostile/malformed constructor value
            # (e.g. a `remote_secrets_dir` whose __add__ raises) denies rather than escaping provision().
            # bytes mode (no text=): the value is byte-identical on the wire. input= puts the value on
            # stdin ONLY — never in argv/the process table (L18). capture_output keeps it out of logs.
            argv = self._ssh_argv(ip_canon, self._remote_command(secret_name, expected))
            completed = self._runner(
                argv, input=material, capture_output=True, timeout=self._timeout, check=False
            )
        except BaseException:  # noqa: BLE001 — timeout/OSError/anything => fail-closed, never leak
            return deny

        # ALL verdict logic inside the guard, on ONE snapshot of each attribute. `completed.stdout`
        # and `.returncode` are read EXACTLY ONCE into locals — a hostile property that returned real
        # bytes for a type-check and a forging object on a second read (TOCTOU) has no second read to
        # exploit. Every boolean is coerced to a plain `bool` HERE (a stateful `__bool__`/`__eq__` can
        # only deny, never escape or forge), and `ok` is reduced to a true `bool` so the return below
        # touches no hostile object. We require EXACT bytes stdout, strict-ascii-decoded, equal to the
        # single `OK:<expected>` literal — and since `mv` is remotely gated on that same digest, that
        # literal is proof the correct bytes were installed (findings #2 and #6).
        try:
            out = completed.stdout
            rc = completed.returncode
            # Exact-type BOTH: a hostile returncode object whose __eq__/__bool__ resolves truthy would
            # otherwise forge success. subprocess.run always returns a real int rc + bytes stdout, so
            # exact-type loses nothing and closes the forge (findings pass-3 #1, #6).
            if type(out) is not bytes or type(rc) is not int:
                return deny
            verified_ok = rc == 0 and out.decode("ascii", "strict").strip() == f"OK:{expected}"
            ok = verified_ok is True
        except BaseException:  # noqa: BLE001 — any hostile result shape => fail-closed
            return deny
        return {"written": ok, "verified": ok}

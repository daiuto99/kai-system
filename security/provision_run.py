"""
provision_run — the composed live entrypoint for the authorized provisioning path (KAI-984).

This is the ONE invocable surface a session (or a future orchestrator route) calls to move a
server-held secret onto a tailnet KAI node. It wires the three live adapters into the Codex-verified
`provision_capability.provision_secret` and prints a value-free result:

    TelegramApprovalGate (KAI-999 /mode_lock) + FileSecretSource (server-side read)
        + OpenSshSecretTransport (tailnet write + sha256 read-back)

It adds no security decision of its own — every allow/deny is made inside the verified capability.
Its only added guard is the R1 ENROLLMENT GATE: it REFUSES to run unless the LOCK-CLASS allowlist is
marked `enrollment_status: confirmed` (the exact literal `tailnet_guard` enforces — aliased via
`tailnet_guard._CONFIRMED_ENROLLMENT` so the two gates can never drift). The seeded allowlist ships as
`seeded_pending_leo_confirmation`, so live provisioning is impossible until Leo performs the
out-of-band enrollment ceremony (a separate Leo-hand act, never something a session does). This makes
"someone runs this before enrollment is confirmed" fail-closed by construction.

The value NEVER passes through this process except opaquely inside the capability→transport boundary;
the only things printed are the secret NAME, the node, the outcome, and the approval id.

Usage:
    python3 provision_run.py --node 71-kai-mini --secret anthropic_api_key --requester kai-session
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# security/ modules import each other by bare name; put this dir first on the path.
sys.path.insert(0, str(_HERE))
# shared/ for the #devops notifier.
sys.path.insert(0, str(_HERE.parent / "shared"))

import provision_capability  # noqa: E402
import provision_transport  # noqa: E402
import tailnet_guard  # noqa: E402
from provision_gate import TelegramApprovalGate  # noqa: E402
from provision_source import FileSecretSource  # noqa: E402

_DEFAULT_ALLOWLIST = str(_HERE / "kai_node_allowlist.json")
_DEFAULT_AUDIT = "/home/leo/kai-system/logs/provision_audit.jsonl"
_DEFAULT_WORKER_API = "http://127.0.0.1:8001"
_DEFAULT_AUTH_FILE = "/home/leo/kai-system/secrets/kai_worker_auth.txt"
# On this worker `tailscaled` runs inside the `kai-tailscale` Docker container and there is NO
# `tailscale` CLI on the host PATH — a bare `tailscale status` returns nothing => the guard would
# deny every node. Read status through the container. Override with --tailscale-cmd for other hosts.
_DEFAULT_TAILSCALE_CMD = ["docker", "exec", "kai-tailscale", "tailscale", "status", "--json"]
# SINGLE SOURCE: the exact enrollment literal the tailnet guard already enforces. The whole system
# reads ONE `enrollment_status` field, so the entrypoint gate and `tailnet_guard.load_allowlist`
# MUST agree on the value — otherwise no single ceremony can ever satisfy both and provisioning is
# bricked-closed forever. Importing the constant guarantees they can never drift apart.
_CONFIRMED = tailnet_guard._CONFIRMED_ENROLLMENT


def enrollment_confirmed(allowlist_path: str) -> bool:
    """True only if the allowlist's `enrollment_status` is the confirmed marker. Fail-closed on any
    read/parse problem — an unreadable or malformed lock-class asset is NOT a confirmed enrollment.

    Uses the SAME literal as `tailnet_guard.load_allowlist` (via the shared constant) so the ONE
    enrollment ceremony that makes the guard trust the allowlist ALSO clears this entrypoint gate.
    They read the same field; they must accept the same value."""
    try:
        data = json.loads(Path(allowlist_path).read_text())
        return isinstance(data, dict) and data.get("enrollment_status") == _CONFIRMED
    except BaseException:  # noqa: BLE001 — no confirmation we cannot positively read
        return False


def _notifier(message: str) -> None:
    """Fire a #devops Telegram alert. Best-effort; never raises into the capability."""
    try:
        from tg_alert import tg_alert
        tg_alert(f"[#devops] KAI-984 {message}")
    except BaseException:  # noqa: BLE001 — notification is best-effort
        pass


def _tailscale_status(cmd: list[str] | None = None) -> dict:
    """Live `tailscale status --json` (default: via the kai-tailscale container). Returns {} on any
    failure => the tailnet guard denies all (fail-closed: an unreadable tailnet resolves nothing)."""
    try:
        out = subprocess.run(
            cmd or _DEFAULT_TAILSCALE_CMD, capture_output=True, text=True, timeout=15, check=False
        )
        return json.loads(out.stdout) if out.returncode == 0 else {}
    except BaseException:  # noqa: BLE001 — unavailable status => empty => guard denies (fail-closed)
        return {}


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KAI-984 authorized secret provisioning")
    ap.add_argument("--node", required=True, help="enrolled tailnet KAI node (e.g. 71-kai-mini)")
    ap.add_argument("--secret", required=True, help="server-held secret NAME (never a value)")
    ap.add_argument("--requester", default="kai-session", help="audit identity of the requester")
    ap.add_argument("--allowlist", default=_DEFAULT_ALLOWLIST)
    ap.add_argument("--audit", default=_DEFAULT_AUDIT)
    ap.add_argument("--worker-api", default=_DEFAULT_WORKER_API)
    ap.add_argument("--auth-file", default=_DEFAULT_AUTH_FILE)
    # Target-node SSH wiring (the node's login/identity/secret path differ per node — e.g. the mini
    # is a Mac: user `leodaiuto`, its own secret dir). Left at the transport defaults if omitted.
    ap.add_argument("--ssh-user", default=None, help="login user on the target node")
    ap.add_argument("--ssh-key", default=None, help="SSH identity file the target node accepts")
    ap.add_argument("--remote-secrets-dir", default=None, help="secret store dir ON the target node")
    ap.add_argument("--tailscale-cmd", default=None,
                    help="shell command to read `tailscale status --json` (default: via kai-tailscale container)")
    args = ap.parse_args(argv)

    tailscale_cmd = shlex.split(args.tailscale_cmd) if args.tailscale_cmd else None
    transport_kwargs = {k: v for k, v in {
        "ssh_user": args.ssh_user, "ssh_key": args.ssh_key,
        "remote_secrets_dir": args.remote_secrets_dir,
    }.items() if v is not None}

    # R1 enrollment gate — the single guard this entrypoint adds. Refuse until Leo confirms.
    if not enrollment_confirmed(args.allowlist):
        print(json.dumps({
            "ok": False, "status": "refused_unenrolled", "node": args.node, "secret_name": args.secret,
            "reason": f"allowlist enrollment_status is not '{_CONFIRMED}' — run the out-of-band "
                      "enrollment ceremony (Leo-hand, lock-class asset) before live provisioning.",
        }))
        return 2

    os.makedirs(os.path.dirname(args.audit), exist_ok=True)

    result = provision_capability.provision_secret(
        node=args.node,
        secret_name=args.secret,
        requester=args.requester,
        gate=TelegramApprovalGate(base_url=args.worker_api, auth_file=args.auth_file),
        secret_source=FileSecretSource(),
        transport=provision_transport.OpenSshSecretTransport(**transport_kwargs),
        allowlist_path=args.allowlist,
        tailscale_status=_tailscale_status(tailscale_cmd),
        audit_path=args.audit,
        notifier=_notifier,
    )

    print(json.dumps({
        "ok": result.ok, "status": result.status, "node": result.node, "node_id": result.node_id,
        "secret_name": result.secret_name, "approval_id": result.approval_id, "reason": result.reason,
    }))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(run())

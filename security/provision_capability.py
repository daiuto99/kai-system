"""
provision_capability — the authorized approve-and-execute path (KAI-984, increment 3).

The stateful capability that COMPOSES the two Codex-verified pure layers into one guarded
motion, so Leo taps once and KAI moves the secret — Leo never fetches it (the §5 no-execution-
loop the whole ticket exists to kill).

Motion (fail-closed at every step; the secret VALUE never enters a return, a log, or an
exception message — only its NAME does):
  1. authorize_provision (inc2 -> inc1): is this secret-name allowed to this tailnet KAI node?
  2. gate.request_approval: a FRESH per-action Slack tap (independent of the 90-min write
     unlock). Deny or timeout => stop, audit `denied_gate`, moved nothing.
  3. secret_source.read(name): read the named secret SERVER-SIDE into a short-lived holder.
     The capability never decodes, formats, or logs the material — it only forwards the bytes
     to the transport.
  4. transport.provision(tailnet_ip, name, material): write to the node's secret store over the
     tailnet and verify by read-back IN the transport. Returns booleans only, never the value.
  5. audit + #devops: one value-free record of requester/name/target/approval_id/outcome/ts.

Every external effect — the Slack gate, the server-side secret read, the tailnet transport, the
audit sink, the notifier, the live tailscale status — is an INJECTED dependency (mirroring the
Codex-verified hostops.py, which injects `transport`). That keeps this security logic pure
orchestration and verifiable in isolation; the live adapters (Slack polling, SSH transport) are
thin, separately-built, and cannot change these decisions.

Design: R2 blast-radius bound is inherited from inc2's module allowlist; R3 (Claude-as-requester)
is bounded by the per-action tap here + the R2 bound; R4 requires the gate's Slack card to name
the exact secret + node + requester (the capability passes all three to `request_approval`); R5
(no value in transcript) is why material is never touched except as an opaque forward.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

import provision_audit
import provision_policy


@dataclass(frozen=True)
class Approval:
    """Result of the per-action Slack approval tap. Carries NO secret."""
    approved: bool
    approval_id: str | None
    reason: str


class Gate(Protocol):
    def request_approval(self, *, secret_name: str, node: str, requester: str) -> Approval:
        """Post the specific approval card (R4: exact secret + node + requester), time-box,
        return Approval(approved, approval_id, reason). Must fail-closed (deny) on timeout/error."""
        ...


class SecretSource(Protocol):
    def read(self, secret_name: str) -> bytes | None:
        """Read the named secret server-side. Return raw bytes, or None if absent/unreadable.
        MUST NOT log the value. MUST NOT include the value in any exception it raises."""
        ...


class Transport(Protocol):
    def provision(self, *, tailnet_ip: str, secret_name: str, material: bytes) -> dict:
        """Write `material` to the node's secret store over the tailnet and verify by read-back.
        Return {"written": bool, "verified": bool} — NEVER the value. MUST NOT log/return material."""
        ...


@dataclass(frozen=True)
class ProvisionResult:
    ok: bool
    status: str          # one of provision_audit._OUTCOMES
    node: str
    node_id: str | None
    secret_name: str
    approval_id: str | None
    reason: str          # audit-safe; never contains a secret value


def _emit(audit_path: str, notifier: Callable[[str], None], *, requester: str, secret_name: str,
          node: str, node_id, tailnet_ip, approval_id, outcome: str,
          clock: Callable[[], str] | None) -> None:
    """Write the value-free audit record and fire the #devops notification. A notifier failure
    must NOT abort or leak — the record is the source of truth; the ping is best-effort."""
    ts = clock() if clock else None
    record = provision_audit.build_record(
        requester=requester, secret_name=secret_name, node=node, node_id=node_id,
        tailnet_ip=tailnet_ip, approval_id=approval_id, outcome=outcome, ts=ts)
    provision_audit.append_record(audit_path, record)
    try:
        notifier(f"provision {outcome}: secret={secret_name} node={node} "
                 f"requester={requester} approval={approval_id}")
    except Exception:  # noqa: BLE001 — notification is best-effort; the durable record already exists
        pass


def provision_secret(
    *,
    node: str,
    secret_name: str,
    requester: str,
    gate: Gate,
    secret_source: SecretSource,
    transport: Transport,
    allowlist_path: str,
    tailscale_status: dict,
    audit_path: str,
    notifier: Callable[[str], None],
    clock: Callable[[], str] | None = None,
) -> ProvisionResult:
    """Execute one authorized provisioning. Fail-closed; the secret VALUE never leaves the
    transport boundary (never in the return, the audit, a log, or an exception here).

    `tailscale_status` and the loaded allowlist are passed in (the live caller supplies
    `tailscale status --json` and the enrolled allowlist path) so the decision is reproducible.
    """
    def _audit(outcome, *, node_id=None, tailnet_ip=None, approval_id=None):
        _emit(audit_path, notifier, requester=requester, secret_name=secret_name, node=node,
              node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id, outcome=outcome,
              clock=clock)

    try:
        allowlist = provision_audit.tailnet_guard.load_allowlist(allowlist_path)  # {} => deny-all

        # 1. WHICH secret + WHERE — the two verified pure layers. Deny => nothing moves.
        decision = provision_policy.authorize_provision(node, secret_name, allowlist, tailscale_status)
        if not decision.allowed:
            _audit("denied_policy", node_id=decision.node_id)
            return ProvisionResult(False, "denied_policy", str(node), decision.node_id,
                                   str(secret_name), None, f"policy denied: {decision.reason}")

        node_id, tailnet_ip = decision.node_id, decision.tailnet_ip

        # 2. Fresh per-action Slack tap (R3/R4). Independent of the 90-min write unlock.
        approval = gate.request_approval(secret_name=secret_name, node=node, requester=requester)
        if not isinstance(approval, Approval) or not approval.approved:
            approval_id = approval.approval_id if isinstance(approval, Approval) else None
            reason = approval.reason if isinstance(approval, Approval) else "gate returned no decision"
            _audit("denied_gate", node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id)
            return ProvisionResult(False, "denied_gate", node, node_id, secret_name, approval_id,
                                   f"not approved: {reason}")

        # 3. Read the named secret SERVER-SIDE. The material is opaque to this function.
        material = secret_source.read(secret_name)
        if not isinstance(material, (bytes, bytearray)) or len(material) == 0:
            _audit("denied_unavailable", node_id=node_id, tailnet_ip=tailnet_ip,
                   approval_id=approval.approval_id)
            return ProvisionResult(False, "denied_unavailable", node, node_id, secret_name,
                                   approval.approval_id, "named secret unavailable server-side")

        # 4. Transport over the tailnet. Booleans back only — value stays inside the transport.
        try:
            result = transport.provision(tailnet_ip=tailnet_ip, secret_name=secret_name,
                                         material=bytes(material))
        except Exception as exc:  # noqa: BLE001 — a transport error must not surface secret bytes
            _audit("failed", node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval.approval_id)
            return ProvisionResult(False, "failed", node, node_id, secret_name,
                                   approval.approval_id, f"transport error: {type(exc).__name__}")
        finally:
            # Best-effort scrub of our reference; bytes are immutable but drop the name binding.
            material = None

        verified = bool(isinstance(result, dict) and result.get("written") and result.get("verified"))
        outcome = "succeeded" if verified else "failed"
        _audit(outcome, node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval.approval_id)
        return ProvisionResult(verified, outcome, node, node_id, secret_name, approval.approval_id,
                               "provisioned and verified" if verified else "transport did not verify")

    except Exception as exc:  # noqa: BLE001 — top-level fail-closed; never leak a value in the reason
        try:
            _audit("failed")
        except Exception:  # noqa: BLE001 — even the audit must not turn a failure into a leak
            pass
        return ProvisionResult(False, "failed", str(node), None, str(secret_name), None,
                               f"provision error (fail-closed): {type(exc).__name__}")

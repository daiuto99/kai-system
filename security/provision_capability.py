"""
provision_capability — the authorized approve-and-execute path (KAI-984, increment 3).

The stateful capability that COMPOSES the two Codex-verified pure layers into one guarded
motion, so Leo taps once and KAI moves the secret — Leo never fetches it (the §5 no-execution-
loop the whole ticket exists to kill).

Motion (fail-closed at every step; the secret VALUE never enters a return, a log, or an
exception message — only its NAME does):
  1. authorize_provision (inc2 -> inc1): is this secret-name allowed to this tailnet KAI node?
  2. gate.request_approval: a FRESH per-action Slack tap (independent of the 90-min write
     unlock). Deny / timeout / error => stop, audit `denied_gate`, moved nothing.
  3. secret_source.read(name): read the named secret SERVER-SIDE into a short-lived holder.
     The capability never decodes, formats, or logs the material — it only forwards the bytes
     to the transport. A read that raises is treated as unavailable; its exception is NEVER
     inspected (it could embed the value).
  4. transport.provision(tailnet_ip, name, material): write to the node's secret store over the
     tailnet and verify by read-back IN the transport. Booleans back only, never the value.
  5. audit + #devops: one value-free record of requester/name/target/approval_id/outcome/ts.

Every external effect — the Slack gate, the server-side secret read, the tailnet transport, the
audit sink, the notifier, the live tailscale status — is an INJECTED dependency (mirroring the
Codex-verified hostops.py). That keeps this security logic pure orchestration and verifiable in
isolation; the live adapters (Slack polling, SSH transport) are thin and cannot change these
decisions.

HARDENED 2026-07-27 after Codex inc3 round-1 verification:
  - strict-True checks on the approval decision and the transport verdict (a truthy string like
    "false" must NOT proceed / must NOT read as success);
  - EVERY dependency boundary catches BaseException and fails closed (a hostile __str__ raising
    SystemExit, a KeyboardInterrupt mid-call, etc. can never escape into an allow/leak);
  - NO exception object is ever formatted into a reason or log (fixed literals only), so a
    transport/source exception whose text embeds the secret value cannot surface it (R5);
  - a gate/source RAISE is classified as its own no-move outcome (denied_gate/denied_unavailable),
    reserving `failed` for a real post-transport failure;
  - an audit-write failure still fires the #devops notification (loud), never silent (R6).

Design: R2 blast-radius bound is inherited from inc2's module allowlist; R3 (Claude-as-requester)
is bounded by the per-action tap here + the R2 bound; R4 requires the gate's Slack card to name
the exact secret + node + requester (the capability passes all three to `request_approval`); R5
(no value in transcript) is why material is never touched except as an opaque forward and no
exception content is ever surfaced. The gate and transport are TRUSTED server-side components we
build; they never receive the secret value (the value is read only AFTER approval, and the
transport returns booleans) — so they cannot carry it into a reason/approval_id even in principle.
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
    reason: str          # audit-safe fixed phrasing; never contains a secret value


def _safe(x) -> str:
    """Coerce to str without ever raising past this function (hostile __str__/__repr__).

    On failure returns a FIXED literal — never repr(type(x)) — so a hostile class *name* can never
    become a leak vector (R5)."""
    try:
        return str(x)
    except BaseException:  # noqa: BLE001 — a boundary coercion must never propagate or leak
        return "<unstringifiable>"


def _emit(audit_path: str, notifier: Callable[[str], None], *, requester: str, secret_name: str,
          node: str, node_id, tailnet_ip, approval_id, outcome: str,
          clock: Callable[[], str] | None) -> bool:
    """Write the value-free audit record and fire the #devops notification. Returns whether the
    record was durably persisted.

    An audit-write failure does NOT abort and is NOT swallowed silently — the notifier still fires
    with audit_persisted=False so an integrity failure is LOUD (R6), and the caller downgrades a
    successful transport to not-ok (an unaudited privileged action is a contract failure). A
    notifier failure is best-effort (the durable record, when written, is the source of truth)."""
    audit_persisted = False
    try:
        # A raising injected clock must NOT drop the audit trail — fall back to build_record's own
        # server clock (ts=None). A flaky clock loses the timestamp precision, never the record.
        try:
            ts = clock() if clock else None
        except BaseException:  # noqa: BLE001
            ts = None
        record = provision_audit.build_record(
            requester=requester, secret_name=secret_name, node=node, node_id=node_id,
            tailnet_ip=tailnet_ip, approval_id=approval_id, outcome=outcome, ts=ts)
        provision_audit.append_record(audit_path, record)
        audit_persisted = True
    except BaseException:  # noqa: BLE001 — never let an audit-store failure abort or leak
        audit_persisted = False
    try:
        notifier(f"provision {outcome}: secret={secret_name} node={node} "
                 f"requester={requester} approval={approval_id} audit_persisted={audit_persisted}")
    except BaseException:  # noqa: BLE001 — notification is best-effort
        pass
    return audit_persisted


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
    """
    node_s, secret_s, requester_s = _safe(node), _safe(secret_name), _safe(requester)
    top_error = False

    def _audit(outcome, *, node_id=None, tailnet_ip=None, approval_id=None) -> bool:
        # approval_id is already a flattened plain str by the time it reaches here (snapshotted the
        # instant the approval arrived, BEFORE the secret was read) — no lazy object survives.
        return _emit(audit_path, notifier, requester=requester_s, secret_name=secret_s, node=node_s,
                     node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id,
                     outcome=outcome, clock=clock)

    try:
        allowlist = provision_audit.tailnet_guard.load_allowlist(allowlist_path)  # {} => deny-all

        # 1. WHICH secret + WHERE — the two verified pure layers. Deny => nothing moves.
        decision = provision_policy.authorize_provision(node, secret_name, allowlist, tailscale_status)
        if not decision.allowed:
            _audit("denied_policy", node_id=decision.node_id)
            return ProvisionResult(False, "denied_policy", node_s, decision.node_id,
                                   secret_s, None, "policy denied")

        node_id, tailnet_ip = decision.node_id, decision.tailnet_ip

        # 2. Fresh per-action Slack tap (R3/R4). A gate that RAISES = no approval (nothing moved).
        try:
            approval = gate.request_approval(secret_name=secret_name, node=node, requester=requester)
        except BaseException:  # noqa: BLE001 — a gate error is a denial, not a leak/allow
            _audit("denied_gate", node_id=node_id, tailnet_ip=tailnet_ip)
            return ProvisionResult(False, "denied_gate", node_s, node_id, secret_s, None,
                                   "gate error (fail-closed)")
        # strict: only an EXACT True boolean on a real Approval proceeds.
        if not isinstance(approval, Approval) or approval.approved is not True:
            raw_id = approval.approval_id if isinstance(approval, Approval) else None
            approval_id = _safe(raw_id) if raw_id is not None else None
            _audit("denied_gate", node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id)
            return ProvisionResult(False, "denied_gate", node_s, node_id, secret_s, approval_id,
                                   "not approved")
        # EAGER flatten to a plain str NOW — before the secret value is ever read — so a lazy
        # approval_id object can never be __str__'d after the value is in memory (R5).
        approval_id = _safe(approval.approval_id) if approval.approval_id is not None else None

        # 3. Read the named secret SERVER-SIDE. A read that raises = unavailable; its exception is
        #    NEVER inspected (it could embed the value). The material is opaque to this function.
        try:
            material = secret_source.read(secret_name)
        except BaseException:  # noqa: BLE001 — never surface a source exception (may carry the value)
            material = None
        if not isinstance(material, (bytes, bytearray)):
            _audit("denied_unavailable", node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id)
            return ProvisionResult(False, "denied_unavailable", node_s, node_id, secret_s,
                                   approval_id, "named secret unavailable server-side")
        # Read the TRUE underlying buffer via memoryview — NOT bytes(material), which would run a
        # subclass __bytes__/__len__ override that could turn an empty buffer nonempty, lie about
        # length, or raise WITH the value. memoryview reflects the actual buffer content only.
        try:
            data = bytes(memoryview(material))
        except BaseException:  # noqa: BLE001 — a hostile/unbuffered object => treat as unavailable
            data = b""
        material = None
        if len(data) == 0:
            _audit("denied_unavailable", node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id)
            return ProvisionResult(False, "denied_unavailable", node_s, node_id, secret_s,
                                   approval_id, "named secret unavailable server-side")

        # 4. Transport over the tailnet. Booleans back only — value stays inside the transport.
        #    The audit/notify for a transport FAILURE happens OUTSIDE the except block (below) so a
        #    notifier that logs the active exception can never capture a secret-bearing traceback (R5).
        transport_failed = False
        try:
            result = transport.provision(tailnet_ip=tailnet_ip, secret_name=secret_name,
                                         material=data)
        except BaseException:  # noqa: BLE001 — transport error must not surface secret bytes
            transport_failed = True
        finally:
            data = None  # drop our reference to the plain-bytes value promptly
        if transport_failed:
            _audit("failed", node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id)
            return ProvisionResult(False, "failed", node_s, node_id, secret_s, approval_id,
                                   "transport failed (fail-closed)")

        # Transport RAN (a byte may have moved). A verdict-extraction error here is executed-but-
        # unverified => `failed` (executed), never the non-executed `errored` — else a hostile
        # dict-subclass `.get` that raises would hide an executed action from the §4.5 invariant.
        try:
            verified = bool(isinstance(result, dict)
                            and result.get("written") is True and result.get("verified") is True)
        except BaseException:  # noqa: BLE001
            verified = False
        result = None  # drop the transport result before the notify — a buggy transport that stuffed
        # material into an extra field must not stay live as a caller-frame local across _audit.
        outcome = "succeeded" if verified else "failed"
        persisted = _audit(outcome, node_id=node_id, tailnet_ip=tailnet_ip, approval_id=approval_id)
        # R6: a verified transport that could NOT be durably audited is a contract failure, not a
        # clean success — an unaudited privileged action must not report ok. The #devops alert
        # already fired (audit_persisted=False). We report the honest split state.
        if verified and not persisted:
            return ProvisionResult(False, "failed", node_s, node_id, secret_s, approval_id,
                                   "provisioned but NOT durably audited (see #devops)")
        return ProvisionResult(verified, outcome, node_s, node_id, secret_s, approval_id,
                               "provisioned and verified" if verified else "transport did not verify")

    except BaseException:  # noqa: BLE001 — top-level fail-closed; never leak a value in the reason
        top_error = True

    # The top-level audit/return runs OUTSIDE the except block so a notifier that logs the active
    # exception cannot capture a secret-bearing traceback (R5). `errored` (NOT `failed`): a crash is
    # a NON-transport error — no byte moved — so it must not be marked EXECUTED for the §4.5 invariant.
    if top_error:
        try:
            _audit("errored")
        except BaseException:  # noqa: BLE001 — even the audit must not turn a failure into a leak
            pass
        return ProvisionResult(False, "errored", node_s, None, secret_s, None,
                               "provision error (fail-closed)")

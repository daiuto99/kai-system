"""
provision_audit — append-only, value-free audit + §4.5 invariant for the authorized
provisioning path (KAI-984, increment 3).

Third layer, composed with the Codex-verified `tailnet_guard` (inc1) and `provision_policy`
(inc2). It records WHAT happened (never the secret value) and provides the pure §4.5 invariant
the scheduler asserts: every provisioning the system ever executed targeted an enrolled KAI
tailnet node — no audit record proves an off-allowlist / off-tailnet write.

L18 (hardcoded): an audit record carries requester + secret-NAME + target(node/node_id/
tailnet_ip) + approval_id + outcome + timestamp ONLY. NEVER the secret value, never key bytes.
Enforced structurally: `record()` accepts a fixed set of scalar fields and cannot be handed a
value; there is no field for one.

Design R6 (audit-log integrity): records are appended with O_APPEND + fsync and there is NO
update/delete API — this module cannot rewrite history. The stronger property ("written where
the executing process cannot rewrite it") is a DEPLOY requirement: the JSONL path must be
owned by a different principal / append-only-perms so a compromised orchestrator cannot edit
prior lines. That hardening is a separate step (see build plan); this module does not silently
claim it. `verify_provision_invariant` is pure over already-read records, so it is verifiable
in isolation regardless of where the store lives.
"""
from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass

import tailnet_guard  # inc1 — reused for the CGNAT range + allowlist loader (single source)

# Outcomes an audit record may carry. `denied_*` records a request that never moved a byte;
# `succeeded`/`failed` record an APPROVED action that reached (or attempted) transport.
_OUTCOMES = frozenset({
    "denied_policy",     # authorize_provision said no (bad secret name or target)
    "denied_gate",       # Leo did not approve the per-action Slack tap (deny or timeout)
    "denied_unavailable",  # named secret could not be read server-side
    "succeeded",         # secret written to the node's store and read-back verified
    "failed",            # approved + attempted, but transport did not verify
})

# Outcomes that represent an action that actually MOVED a secret onto a node. Only these are
# blast-radius-relevant for the §4.5 invariant — a denial moved nothing and cannot be off-tailnet.
_EXECUTED_OUTCOMES = frozenset({"succeeded", "failed"})

CGNAT_V4 = tailnet_guard.CGNAT_V4


@dataclass(frozen=True)
class AuditRecord:
    ts: str
    requester: str
    secret_name: str
    node: str
    node_id: str | None
    tailnet_ip: str | None
    approval_id: str | None
    outcome: str


def _iso_now() -> str:
    # Injected in tests; the live caller passes a real clock. Kept import-light on purpose.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_record(*, requester: str, secret_name: str, node: str, node_id, tailnet_ip,
                 approval_id, outcome: str, ts: str | None = None) -> AuditRecord:
    """Construct a value-free audit record. There is deliberately NO `value` parameter (L18).

    Coerces optional fields to str|None and validates `outcome`; a bad outcome raises (a
    programming error, caught at the capability boundary — never surfaced with a secret).
    """
    if outcome not in _OUTCOMES:
        raise ValueError(f"invalid audit outcome: {outcome!r}")

    def _opt(x):
        return None if x is None else str(x)

    return AuditRecord(
        ts=ts or _iso_now(),
        requester=str(requester),
        secret_name=str(secret_name),
        node=str(node),
        node_id=_opt(node_id),
        tailnet_ip=_opt(tailnet_ip),
        approval_id=_opt(approval_id),
        outcome=outcome,
    )


def append_record(path: str, record: AuditRecord) -> None:
    """Append one record as a JSON line with O_APPEND + fsync (durable, no rewrite path)."""
    line = json.dumps({
        "ts": record.ts, "requester": record.requester, "secret_name": record.secret_name,
        "node": record.node, "node_id": record.node_id, "tailnet_ip": record.tailnet_ip,
        "approval_id": record.approval_id, "outcome": record.outcome,
    }, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read_records(path: str) -> list[AuditRecord]:
    """Read all audit records. A malformed/absent store yields [] (the invariant then trivially
    holds over zero records — the store's own integrity is a separate deploy concern, R6)."""
    out: list[AuditRecord] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(d, dict) or d.get("outcome") not in _OUTCOMES:
                    continue
                out.append(AuditRecord(
                    ts=str(d.get("ts", "")), requester=str(d.get("requester", "")),
                    secret_name=str(d.get("secret_name", "")), node=str(d.get("node", "")),
                    node_id=d.get("node_id"), tailnet_ip=d.get("tailnet_ip"),
                    approval_id=d.get("approval_id"), outcome=str(d["outcome"]),
                ))
    except OSError:
        return []
    return out


def _ip_in_cgnat(ip) -> bool:
    if not isinstance(ip, str):
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and addr in CGNAT_V4


def verify_provision_invariant(allowlist_path: str, records) -> dict:
    """§4.5 belt-and-suspenders invariant. PURE over a loaded allowlist + a list of records.

    Asserts, fail-loud:
      A. the enrolled allowlist holds ONLY valid, unique stable node IDs (via the inc1 loader,
         which already refuses an unconfirmed enrollment_status) — else the trust root is broken;
      B. every EXECUTED provisioning (succeeded/failed — i.e. a byte actually moved) targeted a
         node_id that is on the current allowlist AND recorded a tailnet (100.64/10) IP.

    Returns {"ok", "checked", "allowlist_ok", "violations": [...]}. A denial record moved nothing
    and is exempt from (B). Never raises; any surprise is itself a violation (fail-loud).
    """
    try:
        allowlist = tailnet_guard.load_allowlist(allowlist_path)  # {} on any problem => deny-all
        enrolled_ids = set(allowlist.values())
        allowlist_ok = bool(allowlist)  # inc1 loader returns {} unless confirmed + all-valid+unique

        violations: list[dict] = []
        checked = 0
        for r in records:
            outcome = getattr(r, "outcome", None)
            if outcome not in _EXECUTED_OUTCOMES:
                continue  # a denial cannot be an off-tailnet write
            checked += 1
            node_id = getattr(r, "node_id", None)
            tailnet_ip = getattr(r, "tailnet_ip", None)
            if node_id not in enrolled_ids:
                violations.append({"node": getattr(r, "node", None), "node_id": node_id,
                                   "ts": getattr(r, "ts", None),
                                   "reason": "executed provisioning target is not on the enrolled allowlist"})
                continue
            if not _ip_in_cgnat(tailnet_ip):
                violations.append({"node": getattr(r, "node", None), "node_id": node_id,
                                   "ts": getattr(r, "ts", None),
                                   "reason": "executed provisioning recorded a non-tailnet (not 100.64/10) target IP"})
        return {"ok": allowlist_ok and not violations, "checked": checked,
                "allowlist_ok": allowlist_ok, "violations": violations}
    except Exception as exc:  # fail-loud: any store/shape surprise is an alert, never a silent pass
        return {"ok": False, "checked": 0, "allowlist_ok": False,
                "violations": [{"reason": f"invariant error (fail-loud): {type(exc).__name__}"}]}

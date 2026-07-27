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
import re
from dataclasses import dataclass

import tailnet_guard  # inc1 — reused for the CGNAT range + allowlist loader (single source)

# A ts field must LOOK like the UTC stamp _iso_now emits. Anything else (e.g. a hostile clock that
# returns secret material as the "timestamp") is rejected and replaced — the ts is never a free-text
# sink that could carry a value into the audit JSONL.
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

# Outcomes an audit record may carry. `denied_*`/`errored` record a request that never moved a
# byte; `succeeded`/`failed` record an APPROVED action that REACHED (or attempted) transport.
_OUTCOMES = frozenset({
    "denied_policy",     # authorize_provision said no (bad secret name or target)
    "denied_gate",       # Leo did not approve the per-action Slack tap (deny or timeout)
    "denied_unavailable",  # named secret could not be read server-side
    "errored",           # a NON-transport error (e.g. a pre-transport crash) — no byte moved
    "succeeded",         # secret written to the node's store and read-back verified
    "failed",            # approved + transport ATTEMPTED, but did not verify
})

# Outcomes that represent an action that actually reached transport (a byte may have moved onto a
# node). Only these are blast-radius-relevant for the §4.5 invariant. A denial OR a pre-transport
# `errored` moved nothing and cannot be an off-tailnet write — so they must NOT be treated as
# executed (else a pre-transport crash with node_id=None fabricates a false §4.5 violation).
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
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:  # isinstance first: unhashable-safe
        raise ValueError("invalid audit outcome")

    def _opt(x):
        return None if x is None else str(x)

    # A provided ts must be an EXACT str matching the ISO stamp shape; otherwise fall back to a real
    # clock reading. This prevents a hostile/buggy clock from serializing arbitrary bytes as `ts`.
    safe_ts = ts if (type(ts) is str and _ISO_TS_RE.fullmatch(ts)) else _iso_now()

    return AuditRecord(
        ts=safe_ts,
        requester=str(requester),
        secret_name=str(secret_name),
        node=str(node),
        node_id=_opt(node_id),
        tailnet_ip=_opt(tailnet_ip),
        approval_id=_opt(approval_id),
        outcome=outcome,
    )


def append_record(path: str, record: AuditRecord) -> None:
    """Append one record as a JSON line with O_APPEND + fsync (durable, no rewrite path).

    The write LOOPS until every byte lands — a short `os.write` must never leave a truncated,
    unparseable record that later reads as a clean absence (R6)."""
    line = json.dumps({
        "ts": record.ts, "requester": record.requester, "secret_name": record.secret_name,
        "node": record.node, "node_id": record.node_id, "tailnet_ip": record.tailnet_ip,
        "approval_id": record.approval_id, "outcome": record.outcome,
    }, sort_keys=True) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        view = memoryview(line.encode("utf-8"))
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError("short write to audit store")
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)


def read_records_with_integrity(path: str) -> tuple[list[AuditRecord], list[str]]:
    """Read the store, returning (clean_records, corruption_reasons). NEVER raises.

    A line that is unparseable — invalid UTF-8, malformed JSON, a TRUNCATED partial write, a
    missing/unknown outcome — is NOT silently dropped: it is counted in `corruption_reasons` so a
    caller (verify_store) can fail-loud on it. A truncated executed record must never be able to
    vanish and give false all-clear. An ABSENT store is not corruption (returns [], [])."""
    clean: list[AuditRecord] = []
    corrupt: list[str] = []
    try:
        raw_bytes = open(path, "rb").read()
    except FileNotFoundError:
        return [], []  # legitimately absent (no provisioning yet) — NOT corruption
    except OSError:
        # present-but-unreadable (permissions, a directory, an I/O error) => fail-LOUD, never a
        # silent all-clear. A store we cannot read is an integrity failure, not an empty store.
        return [], ["audit store present but unreadable"]
    for raw in raw_bytes.split(b"\n"):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):  # ValueError covers JSONDecodeError + a bad decode
            corrupt.append("unparseable audit line")
            continue
        # isinstance(str) BEFORE the membership test: an unhashable outcome ([]/{}) would make
        # `outcome not in _OUTCOMES` raise TypeError and escape this never-raises reader.
        oc = d.get("outcome") if isinstance(d, dict) else None
        if not isinstance(d, dict) or not isinstance(oc, str) or oc not in _OUTCOMES:
            corrupt.append("audit line has missing or unknown outcome")
            continue
        clean.append(AuditRecord(
            ts=str(d.get("ts", "")), requester=str(d.get("requester", "")),
            secret_name=str(d.get("secret_name", "")), node=str(d.get("node", "")),
            node_id=d.get("node_id"), tailnet_ip=d.get("tailnet_ip"),
            approval_id=d.get("approval_id"), outcome=str(d["outcome"]),
        ))
    return clean, corrupt


def read_records(path: str) -> list[AuditRecord]:
    """Read all clean audit records (drops corrupt lines). NEVER raises. Use read_records_with_
    integrity / verify_store when store-integrity itself must be asserted."""
    return read_records_with_integrity(path)[0]


def _ip_in_cgnat(ip) -> bool:
    if type(ip) is not str:  # exact type: a str-subclass could lie about its parsed content
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
        for r in list(records):  # materialize first: a hostile iterator raising mid-loop is caught below
            if type(r) is not AuditRecord:
                # EXACT type (not isinstance): a subclass could override fields via properties.
                # Fail-loud on any non-exact record — never silently skip an unrecognized shape.
                violations.append({"reason": "audit record is not a well-formed AuditRecord"})
                continue
            if type(r.outcome) is not str or r.outcome not in _OUTCOMES:
                # EXACT str: an equality/hash-spoofing outcome object must not match an allowlisted
                # outcome and exempt an off-tailnet record. Fixed reason — never echo the field.
                violations.append({"reason": "audit record has an invalid or unknown outcome"})
                continue
            if r.outcome not in _EXECUTED_OUTCOMES:
                continue  # a denial / non-transport error cannot be an off-tailnet write
            checked += 1
            node_id = r.node_id
            tailnet_ip = r.tailnet_ip
            # EXACT-str membership (type() is str, not isinstance): a str-SUBCLASS with a hostile
            # __eq__/__hash__ must not impersonate an enrolled ID.
            if type(node_id) is not str or node_id not in enrolled_ids:
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
    except BaseException:  # noqa: BLE001 — fail-LOUD: any surprise (incl. SystemExit from a hostile
        # record iterator) becomes a violation, never a silent pass and never an escape. A fixed
        # literal is used (never the exception's type name — a hostile class name could carry data).
        return {"ok": False, "checked": 0, "allowlist_ok": False,
                "violations": [{"reason": "invariant error (fail-loud)"}]}


def verify_store(allowlist_path: str, audit_path: str) -> dict:
    """§4.5 over the ON-DISK store (what the scheduler wires). Reads with integrity checking, runs
    the pure invariant over the clean records, AND fail-louds on ANY corrupt/truncated/invalid line
    so a partially-written off-allowlist execution can never silently vanish into an all-clear."""
    records, corruption = read_records_with_integrity(audit_path)
    result = verify_provision_invariant(allowlist_path, records)
    if corruption:
        return {**result, "ok": False,
                "violations": result.get("violations", []) + [{"reason": c} for c in corruption]}
    return result

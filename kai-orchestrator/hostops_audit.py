"""HOSTOPS-(d) — durable audit records for executed host-op mutations + the
Layer-2 reconciler that proves every executed mutation traces to exactly one
consumed, correctly-bound council gate (KAI-820, seq915).

(c) enforces approval *at the door* (``consume_hostops_gate`` refuses a mutation
without a resolved, single-use, op/site-bound gate). §3.4 requires the second
half: an unapproved action must be *detectable and loud*, not silent. This module
is the detector — it records every executed mutation independently of the gate
store, then reconciles the two so a mutation with no matching consumed+bound gate
screams to #devops (the alert lives in kai-scheduler/watchdog.py).

Wired at the generic workflow executor boundary (workflow_base._run_step), never
inside (c)'s capability or its wiring — the scope fence for (d).

L18: an audit record carries identity + operation + target + gate_id + outcome
ONLY. Never key bytes, the secret payload, or the secret value.
"""
import json
import logging

from db import get_conn, new_id, now_iso

logger = logging.getLogger(__name__)

# The only two mutating host-ops. Read-only status/verify are NOT mutations and
# are never audited here (kickoff scope fence).
_MUTATION_CAPABILITIES = {
    "hostops.place_secret": "place_secret",
    "hostops.deploy_plugin": "deploy_plugin",
}

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS hostops_audit (
    id         TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    job_id     TEXT,
    step_id    TEXT,
    actor      TEXT,
    operation  TEXT NOT NULL,
    site       TEXT,
    gate_id    TEXT,
    authorization TEXT,
    outcome    TEXT NOT NULL
)
"""


def operation_for_capability(capability: str | None) -> str | None:
    """Map a capability name to its mutation operation, or None if not a mutation."""
    return _MUTATION_CAPABILITIES.get(capability or "")


def _ensure_table(conn) -> None:
    # Self-initializing: db.SCHEMA owns this in production, but the executor hook
    # also runs under tests with a minimal DB — create-if-not-exists keeps both
    # the write and the reconcile paths robust without a migration ordering rule.
    conn.execute(_CREATE_TABLE)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(hostops_audit)").fetchall()}
    if "authorization" not in columns:
        conn.execute("ALTER TABLE hostops_audit ADD COLUMN authorization TEXT")


def _consumed_gate(conn, job_id: str, operation: str, site: str):
    """Return (gate_id, brief) of the consumed gate bound to job+op+site, else (None, None).

    Mirrors ``engine.consume_hostops_gate``'s binding but for an already-consumed
    row — used to recover gate_id/actor on a post-gate *failure*, whose (L18)
    sparse CapabilityResult carries neither.
    """
    expected_type = f"hostops_{operation}"
    rows = conn.execute(
        "SELECT id, brief FROM gates WHERE job_id=? AND gate_type=? AND status='consumed' "
        "ORDER BY opened_at DESC",
        (job_id, expected_type),
    ).fetchall()
    for row in rows:
        try:
            brief = json.loads(row["brief"] or "{}")
        except json.JSONDecodeError:
            continue
        if brief.get("hostops_operation") == operation and brief.get("site") == site:
            return row["id"], brief
    return None, None


def record_mutation(job_id: str, step_id: str, capability: str, site: str, result) -> None:
    """Write one durable audit record for an executed mutation (success OR post-gate failure).

    ``result`` is the capability's CapabilityResult. On success it carries the
    authoritative identity + gate_id; on a sparse (L18) failure it carries
    neither, so both are recovered from the consumed gate the mutation just spent.
    Only identity + intent + gate_id + outcome are persisted — never secret bytes.
    """
    operation = operation_for_capability(capability)
    if operation is None:
        return  # not a hostops mutation — nothing to audit

    # Workflow branch skips are verified no-ops, not host mutations.  Recording
    # one as an execution creates a false gate_id=None reconciliation alert.
    data = getattr(result, "data", None) or {}
    if isinstance(data, dict) and data.get("skipped"):
        return

    outcome = "succeeded" if getattr(result, "ok", False) else "failed"

    gate_id = None
    authorization = None
    verification = getattr(result, "verification", None) or {}
    evidence = verification.get("evidence", {}) if isinstance(verification, dict) else {}
    if isinstance(evidence, dict):
        gate_id = evidence.get("gate_id")
        authorization = evidence.get("authorization")
    actor = data.get("identity") if isinstance(data, dict) else None

    conn = get_conn()
    try:
        _ensure_table(conn)
        if gate_id is None or actor is None:
            recovered_id, brief = _consumed_gate(conn, job_id, operation, site)
            gate_id = gate_id or recovered_id
            if actor is None and brief:
                actor = brief.get("audit_identity")
        conn.execute(
            "INSERT INTO hostops_audit (id,ts,job_id,step_id,actor,operation,site,gate_id,authorization,outcome) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id(), now_iso(), job_id, step_id, actor, operation, site, gate_id, authorization, outcome),
        )
        conn.commit()
        logger.info("hostops audit: %s by %s on %s outcome=%s gate=%s",
                    operation, actor, site, outcome, gate_id)
    finally:
        conn.close()


def _reconcile_one(conn, record) -> str | None:
    """Return None if the executed mutation is accounted-for, else a short reason.

    Accounted-for iff a gate row exists with id==record.gate_id, status=='consumed',
    gate_type==hostops_<op>, and a brief bound to the same op+site. Binding is
    enforced HERE (not only at the door), so a wrong-op/site/gate_id execution is
    caught in reconciliation.
    """
    gate_id = record["gate_id"]
    operation = record["operation"]
    site = record["site"]
    if record["authorization"] == "autonomous":
        return None
    if not gate_id:
        return "executed mutation has no gate_id"
    row = conn.execute(
        "SELECT gate_type, brief, status FROM gates WHERE id=?", (gate_id,)
    ).fetchone()
    if row is None:
        return f"gate_id {gate_id} absent from gate store"
    if row["status"] != "consumed":
        return f"gate {gate_id} status={row['status']} (expected consumed)"
    if row["gate_type"] != f"hostops_{operation}":
        return f"gate {gate_id} type={row['gate_type']} != hostops_{operation}"
    try:
        brief = json.loads(row["brief"] or "{}")
    except json.JSONDecodeError:
        return f"gate {gate_id} brief unparseable"
    if brief.get("hostops_operation") != operation or brief.get("site") != site:
        return (f"gate {gate_id} bound to {brief.get('hostops_operation')}/{brief.get('site')} "
                f"!= executed {operation}/{site}")
    return None


def _ensure_ack_table(conn) -> None:
    """AR-5.1: append-only acknowledgment of dev-era audit records. Records are
    never deleted (the trail is append-only); an ack marks a reviewed record as
    reconciled so a real future bypass still stands out."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hostops_audit_ack ("
        "audit_id TEXT PRIMARY KEY, acked_at TEXT, actor TEXT, reason TEXT)"
    )


def acknowledge(audit_id: str, reason: str, actor: str = "leo") -> None:
    """Mark one audit record reviewed+reconciled (retained, not deleted)."""
    conn = get_conn()
    try:
        _ensure_ack_table(conn)
        conn.execute(
            "INSERT OR IGNORE INTO hostops_audit_ack (audit_id, acked_at, actor, reason) VALUES (?,?,?,?)",
            (audit_id, now_iso(), actor, reason),
        )
        conn.commit()
    finally:
        conn.close()


def reconcile() -> dict:
    """Reconcile every executed mutation against the consumed-gate store.

    Returns ``{"ok", "checked", "unreconciled": [...], "error"}``. Any mutation
    with no matching consumed+bound gate lands in ``unreconciled`` (a possible
    bypass). Fail-loud: an unreadable store returns ok=False with an error string,
    never a silent empty pass.
    """
    conn = None
    try:
        conn = get_conn()  # inside the try: a connection-time failure IS the "store unreadable" case
        _ensure_table(conn)
        _ensure_ack_table(conn)
        acked = {r[0] for r in conn.execute("SELECT audit_id FROM hostops_audit_ack").fetchall()}
        records = conn.execute(
            "SELECT id,ts,job_id,actor,operation,site,gate_id,authorization,outcome FROM hostops_audit ORDER BY ts"
        ).fetchall()
        unreconciled = []
        for record in records:
            if record["id"] in acked:
                continue
            reason = _reconcile_one(conn, record)
            if reason:
                unreconciled.append({
                    "audit_id": record["id"], "ts": record["ts"],
                    "operation": record["operation"], "site": record["site"],
                    "gate_id": record["gate_id"], "actor": record["actor"],
                    "outcome": record["outcome"], "reason": reason,
                })
        return {"ok": not unreconciled, "checked": len(records),
                "unreconciled": unreconciled, "error": None}
    except Exception as exc:  # noqa: BLE001 — fail-loud: any store error is itself an alert
        logger.error("hostops reconcile failed: %s", exc)
        return {"ok": False, "checked": 0, "unreconciled": [], "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()

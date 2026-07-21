import json
import logging
from typing import Optional
from db import get_conn, new_id, now_iso

logger = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed_permanent", "cancelled"}

class PeerReviewRequired(ValueError):
    pass

class Engine:
    """Sole gateway for all status mutations on jobs and steps.

    No other module may write status directly to the DB.
    Enforced by tests/test_engine_sole_writer.py.
    """

    def transition(
        self,
        entity: str,
        entity_id: str,
        new_status: str,
        verification: Optional[dict] = None,
        result: Optional[dict] = None,
        error: Optional[str] = None,
        **kwargs,
    ) -> bool:
        if entity == "step":
            return self._transition_step(entity_id, new_status, verification, result, error)
        if entity == "job":
            return self._transition_job(entity_id, new_status, error)
        raise ValueError(f"Unknown entity: {entity!r}")

    def _transition_step(self, step_id, new_status, verification, result, error):
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM steps WHERE id=?", (step_id,)).fetchone()
            if not row:
                raise ValueError(f"Step not found: {step_id}")
            if row["status"] in TERMINAL:
                logger.warning("Step %s already terminal (%s)", step_id, row["status"])
                return False

            if new_status == "succeeded":
                self._require_verification(row, verification)
                self._require_peer_review_if_gated(row, verification)

            ts = now_iso()
            conn.execute(
                """UPDATE steps SET status=?, verification=?, result=?,
                   error=?, completed_at=?, updated_at=? WHERE id=?""",
                (
                    new_status,
                    json.dumps(verification) if verification else None,
                    json.dumps(result) if result else None,
                    error,
                    ts if new_status in TERMINAL else None,
                    ts,
                    step_id,
                ),
            )
            self._emit_event(conn, "state_transition", job_id=row["job_id"], step_id=step_id,
                             payload={"from": row["status"], "to": new_status})
            conn.commit()
            logger.info("Step %s → %s", step_id, new_status)
            return True
        finally:
            conn.close()

    def _transition_job(self, job_id, new_status, error):
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"Job not found: {job_id}")
            if row["status"] in TERMINAL:
                return False
            ts = now_iso()
            conn.execute(
                "UPDATE jobs SET status=?, error_summary=?, updated_at=? WHERE id=?",
                (new_status, error, ts, job_id),
            )
            self._emit_event(conn, "state_transition", job_id=job_id,
                             payload={"from": row["status"], "to": new_status})
            conn.commit()
            return True
        finally:
            conn.close()

    def create_job(self, job_type: str, inputs: dict, approval_policy: str = "auto") -> str:
        conn = get_conn()
        try:
            job_id = new_id()
            ts = now_iso()
            conn.execute(
                "INSERT INTO jobs (id,type,inputs,status,approval_policy,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (job_id, job_type, json.dumps(inputs), "queued", approval_policy, ts, ts),
            )
            self._emit_event(conn, "job_queued", job_id=job_id, payload={"type": job_type})
            conn.commit()
            logger.info("Job created: %s type=%s", job_id, job_type)
            return job_id
        finally:
            conn.close()

    def create_step(self, job_id: str, name: str, capability: str = None,
                    step_type: str = "auto") -> str:
        conn = get_conn()
        try:
            step_id = new_id()
            ts = now_iso()
            conn.execute(
                "INSERT INTO steps (id,job_id,name,capability,status,created_at) VALUES (?,?,?,?,?,?)",
                (step_id, job_id, name, capability, "pending", ts),
            )
            self._emit_event(conn, "step_created", job_id=job_id, step_id=step_id,
                             payload={"name": name, "step_type": step_type})
            conn.commit()
            return step_id
        finally:
            conn.close()

    # ── Council gate operations ────────────────────────────────────────────

    def open_gate(self, gate_id: str, job_id: str, step_id: str,
                  gate_type: str, brief: dict, callback_url: str):
        conn = get_conn()
        try:
            ts = now_iso()
            conn.execute(
                """INSERT INTO gates
                   (id,job_id,step_id,gate_type,brief,callback_url,status,opened_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (gate_id, job_id, step_id, gate_type,
                 json.dumps(brief), callback_url, "pending", ts),
            )
            self._emit_event(conn, "gate_opened", job_id=job_id, step_id=step_id,
                             payload={"gate_id": gate_id, "gate_type": gate_type})
            conn.commit()
            logger.info("Gate opened: %s type=%s job=%s", gate_id, gate_type, job_id)
        finally:
            conn.close()

    def resolve_gate(self, gate_id: str, resolution: dict) -> Optional[dict]:
        """Mark a gate resolved, transition the waiting step, return {job_id, job_type} for resume."""
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
            if not row or row["status"] != "pending":
                logger.warning("Gate %s not found or not pending", gate_id)
                return None
            gate = dict(row)

            ts = now_iso()
            conn.execute(
                "UPDATE gates SET status='resolved', resolution=?, resolved_at=? WHERE id=?",
                (json.dumps(resolution), ts, gate_id),
            )
            job_row = conn.execute("SELECT type FROM jobs WHERE id=?", (gate["job_id"],)).fetchone()
            job_type = job_row["type"] if job_row else None

            self._emit_event(conn, "gate_resolved", job_id=gate["job_id"], step_id=gate["step_id"],
                             payload={"gate_id": gate_id,
                                      "approved": resolution.get("approved", False)})
            conn.commit()
        finally:
            conn.close()

        # Transition the waiting step (opens its own connection)
        approved = resolution.get("approved", False)
        if approved:
            self._transition_step(
                gate["step_id"], "succeeded",
                verification={"verified": True, "evidence": {
                    "gate_id": gate_id,
                    "gate_type": gate["gate_type"],
                    "advisor": resolution.get("advisor", "council"),
                    "notes": resolution.get("notes", ""),
                }},
                result=resolution,
                error=None,
            )
        else:
            self._transition_step(
                gate["step_id"], "failed_permanent",
                verification=None,
                result=None,
                error=json.dumps({
                    "type": "gate_rejected",
                    "notes": resolution.get("notes", ""),
                }),
            )

        logger.info("Gate %s resolved approved=%s job=%s", gate_id, approved, gate["job_id"])
        return {"job_id": gate["job_id"], "job_type": job_type}

    def get_gate(self, gate_id: str) -> Optional[dict]:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def consume_hostops_gate(self, gate_id: str, operation: str, site: str) -> bool:
        """Atomically consume one approved gate bound to a hostops mutation.

        Gate consumption is deliberately part of the persistent gate store: a
        caller cannot manufacture an in-process approval object or replay an
        approval for another site/operation.
        """
        expected_type = f"hostops_{operation}"
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
            if not row or row["status"] != "resolved" or row["gate_type"] != expected_type:
                return False
            try:
                brief = json.loads(row["brief"] or "{}")
                resolution = json.loads(row["resolution"] or "{}")
            except json.JSONDecodeError:
                return False
            if not resolution.get("approved"):
                return False
            if brief.get("hostops_operation") != operation or brief.get("site") != site:
                return False
            changed = conn.execute(
                "UPDATE gates SET status='consumed' WHERE id=? AND status='resolved'",
                (gate_id,),
            ).rowcount
            conn.commit()
            return changed == 1
        finally:
            conn.close()

    def list_pending_gates(self) -> list:
        """Return gates whose parent job/step can still be waiting on them.

        A pending gate attached to a missing or terminal parent is orphaned
        durable state, not a live council outage. Retire those rows here so one
        abandoned workflow cannot wedge the fallback poller forever. Age is
        deliberately not part of this decision: an old gate on an active job
        and non-terminal step must still be polled and allowed to fail loud.
        """
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT g.*, j.status AS job_status,
                          s.status AS step_status, s.job_id AS step_job_id
                   FROM gates g
                   LEFT JOIN jobs j ON j.id = g.job_id
                   LEFT JOIN steps s ON s.id = g.step_id
                   WHERE g.status='pending'
                   ORDER BY g.opened_at"""
            ).fetchall()
            pollable = []
            for row in rows:
                gate = dict(row)
                reason = None
                if gate["job_status"] is None:
                    reason = "parent job missing"
                elif gate["step_status"] is None:
                    reason = "parent step missing"
                elif gate["step_job_id"] != gate["job_id"]:
                    reason = "parent step belongs to a different job"
                elif gate["job_status"] in TERMINAL:
                    reason = f"parent job is {gate['job_status']}"
                elif gate["step_status"] in TERMINAL:
                    reason = f"parent step is {gate['step_status']}"

                if reason:
                    ts = now_iso()
                    resolution = json.dumps({"reason": reason})
                    conn.execute(
                        """UPDATE gates
                           SET status='orphaned', resolution=?, resolved_at=?
                           WHERE id=? AND status='pending'""",
                        (resolution, ts, gate["id"]),
                    )
                    logger.warning("Gate %s retired as orphaned: %s", gate["id"], reason)
                    continue

                pollable.append(gate)
            conn.commit()
            return pollable
        finally:
            conn.close()

    # ── Override operations ────────────────────────────────────────────────

    def record_override(self, job_id: str, step_id: str, step_name: str,
                        reason: str, operator: str = "leo",
                        slack_ack: bool = False, bug_filed: str = None) -> str:
        conn = get_conn()
        try:
            override_id = new_id()
            ts = now_iso()
            conn.execute(
                """INSERT INTO overrides
                   (id,job_id,step_id,step_name,reason,operator,slack_ack,bug_filed,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (override_id, job_id, step_id, step_name, reason, operator,
                 1 if slack_ack else 0, bug_filed, ts),
            )
            self._emit_event(conn, "override", job_id=job_id, step_id=step_id,
                             payload={"step_name": step_name, "operator": operator,
                                      "reason_len": len(reason)})
            conn.commit()
            logger.info("Override: %s step=%s operator=%s", override_id, step_name, operator)
            return override_id
        finally:
            conn.close()

    def count_overrides_7d(self, step_name: str) -> int:
        import datetime
        cutoff = (datetime.datetime.utcnow() -
                  datetime.timedelta(days=7)).isoformat() + "Z"
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM overrides WHERE step_name=? AND created_at>=?",
                (step_name, cutoff),
            ).fetchone()
            return row["n"] if row else 0
        finally:
            conn.close()

    # ── Verification guards ────────────────────────────────────────────────

    def _require_verification(self, row, verification):
        if not verification or not verification.get("verified"):
            raise ValueError(
                f"Step '{row['name']}' cannot transition to 'succeeded' without verified=True"
            )

    def _require_peer_review_if_gated(self, row, verification):
        """Steps typed decision or creative_output require peer_review.incorporated."""
        step_type = json.loads(row["input"] or "{}").get("step_type", "auto")
        if step_type in ("decision", "creative_output"):
            pr = (verification or {}).get("peer_review")
            if not pr:
                raise PeerReviewRequired(
                    f"Step '{row['name']}' (type={step_type}) requires peer_review in verification"
                )
            has_incorporated = bool(pr.get("incorporated"))
            all_skipped_with_rationale = (
                pr.get("skipped")
                and all(len(s.get("rationale", "")) >= 30 for s in pr["skipped"])
            )
            if not has_incorporated and not all_skipped_with_rationale:
                raise PeerReviewRequired(
                    f"Step '{row['name']}': peer_review must have incorporated findings "
                    "or all skips must have rationale >= 30 chars"
                )

    # ── Event emitter ──────────────────────────────────────────────────────

    def _emit_event(self, conn, event_type, job_id=None, step_id=None, payload=None):
        conn.execute(
            "INSERT INTO events (id,job_id,step_id,type,payload,created_at) VALUES (?,?,?,?,?,?)",
            (new_id(), job_id, step_id, event_type,
             json.dumps(payload) if payload else None, now_iso()),
        )


engine = Engine()

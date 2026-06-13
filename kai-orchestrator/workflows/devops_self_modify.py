"""devops_self_modify workflow — M2-1.B 5-step chain.

Inputs (validated by self_modify.propose):
  plane_ticket_id, gate, principle, retirement, diff, target_root

Steps:
  1. log_proposal     — append proposal record to vault (M2-1.A behavior)
  2. verify_semantic  — LiteLLM check: ritual ↔ diff coherence. Reject → stop.
  3. apply_diff       — `patch -p1` inside target_root
  4. commit           — git commit with ritual in message
  5. update_plane     — append self-modify trailer to Plane ticket

Verifier reject = workflow ends failed_permanent, no apply, no commit.
target_root must be in the allowlist (see capabilities/self_modify.py).
"""
import json
import logging

from workflow_base import Workflow
from models import StepDef, CapabilityResult
from db import get_conn
from capabilities import get_capability

logger = logging.getLogger(__name__)


class DevopsSelfModifyWorkflow(Workflow):
    name = "devops.self_modify"
    approval_policy = "auto"
    steps = [
        StepDef("log_proposal",    capability="self_modify.propose",      step_type="auto"),
        StepDef("verify_semantic", capability="self_modify.verify",       step_type="auto"),
        # Post-verify steps must not retry: apply/commit are not idempotent
        # under retry, and an upstream-fail short-circuit should not amplify.
        StepDef("apply_diff",      capability="self_modify.apply",        step_type="auto", max_retries=0),
        StepDef("commit",          capability="self_modify.commit",       step_type="auto", max_retries=0),
        StepDef("update_plane",    capability="self_modify.update_plane", step_type="auto", max_retries=0),
    ]

    # State threaded between steps (per-instance, not persisted to DB beyond
    # what step results already capture).
    def __init__(self, job_id: str):
        super().__init__(job_id)
        self._state: dict = {}

    def _inputs(self) -> dict:
        conn = get_conn()
        row = conn.execute(
            "SELECT inputs FROM jobs WHERE id=?", (self.job_id,)
        ).fetchone()
        conn.close()
        return json.loads(row["inputs"]) if row else {}

    def _prior_step_failed(self, current_name: str) -> dict | None:
        """Return the first prior step (by rowid order) that ended failed_permanent
        or cancelled, else None. Used as a workflow-local safety gate because the
        engine's resume() captures status at loop start and won't otherwise see a
        mid-loop step failure."""
        conn = get_conn()
        rows = conn.execute(
            "SELECT name, status, error FROM steps WHERE job_id=? ORDER BY rowid",
            (self.job_id,),
        ).fetchall()
        conn.close()
        for r in rows:
            if r["name"] == current_name:
                return None
            if r["status"] in ("failed_permanent", "cancelled"):
                return {"name": r["name"], "status": r["status"],
                        "error": r["error"]}
        return None

    def execute_step(self, step_def: StepDef, step: dict) -> CapabilityResult:
        inputs = self._inputs()
        name = step_def.name

        # Workflow-local safety gate: never run a step if any prior step ended
        # failed_permanent. This is the verifier-gates-apply contract.
        prior_fail = self._prior_step_failed(name)
        if prior_fail is not None:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "prior_step_failed",
                       "skipped_because": prior_fail["name"],
                       "skipped_status": prior_fail["status"],
                       "skipped_error": prior_fail.get("error")},
            )

        if name == "log_proposal":
            fn = get_capability("self_modify.propose")
            r = fn(**inputs)
            if r.ok:
                self._state["proposal_id"] = (r.data or {}).get("proposal_id")
            return r

        if name == "verify_semantic":
            fn = get_capability("self_modify.verify")
            return fn(proposal_id=self._state.get("proposal_id"), **inputs)

        if name == "apply_diff":
            fn = get_capability("self_modify.apply")
            r = fn(target_root=inputs.get("target_root"), diff=inputs.get("diff"))
            if r.ok:
                self._state["touched_files"] = (r.data or {}).get("touched_files", [])
                self._state["target_root"] = (r.data or {}).get("target_root")
            return r

        if name == "commit":
            fn = get_capability("self_modify.commit")
            r = fn(
                target_root=self._state.get("target_root") or inputs.get("target_root"),
                plane_ticket_id=inputs.get("plane_ticket_id"),
                gate=inputs.get("gate"),
                principle=inputs.get("principle"),
                retirement=inputs.get("retirement"),
                touched_files=self._state.get("touched_files"),
                workflow_id=self.job_id,
            )
            if r.ok:
                self._state["commit_sha"] = (r.data or {}).get("commit_sha")
                self._state["staged_files"] = (r.data or {}).get("staged_files", [])
            return r

        if name == "update_plane":
            fn = get_capability("self_modify.update_plane")
            # Pull verifier reason from previous step's result if available
            verifier_reason = None
            try:
                conn = get_conn()
                row = conn.execute(
                    "SELECT result FROM steps WHERE job_id=? AND name='verify_semantic'",
                    (self.job_id,),
                ).fetchone()
                conn.close()
                if row and row["result"]:
                    verifier_reason = json.loads(row["result"]).get("reason")
            except Exception:
                pass
            return fn(
                plane_ticket_id=inputs.get("plane_ticket_id"),
                commit_sha=self._state.get("commit_sha"),
                workflow_id=self.job_id,
                target_root=self._state.get("target_root") or inputs.get("target_root"),
                staged_files=self._state.get("staged_files"),
                verifier_reason=verifier_reason,
            )

        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "unknown_step", "name": name},
        )

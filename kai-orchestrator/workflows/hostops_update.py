"""hostops_update workflow — CUR-5: gated WordPress update-apply.

The apply half of the DevOps System Currency Program. The currency readers
(CUR-1..CUR-3) surface a stale WP core/plugin; the CUR-4 custodian files ONE
gated triage item per stale layer. THIS workflow is the authorized path that
actually applies one such update — and it applies ONLY through the same
council-gated host-ops rail (epic d4a2d481) that governs place_secret /
deploy_plugin / publish_post. The scanner never mutates; this never scans.

A live client site can break on a bad update, so — mirroring the drafts-only
publish floor — the update is NEVER autonomous: a real, human-approved,
single-use gate bound to (site, exact component) must resolve first. Mechanics
mirror hostops_deploy exactly:

  1. update_gate — approval_gate (gate_type hostops_update_wp), brief bound to
     op + site + the exact component (hostops_resource); carries no payload.
  2. update_exec — hostops.update_wp, using the store-verified gate_id. max
     retries 0: a resolved hostops gate is single-use, so a retry would fail
     closed after the first attempt consumes it.
  3. complete    — finalize.

The gate/exec split is required: a resolved approval_gate step is already
'succeeded' and never re-runs, so the mutation is a separate step that runs on
resume(), re-deriving the approved gate_id from the persistent store
(engine.find_resolved_hostops_gate). hostops._gate -> engine.consume_hostops_gate
verifies the gate is resolved, bound to op+site+component, and marks it consumed.

Launch: POST /workflows/run {"type": "hostops.update",
                            "inputs": {"site": "<key>", "component": "core"|"<plugin-slug>"}}
Approve: POST /gates/{gate_id}/resolve {"approved": true, ...}
"""
import json
import logging

from workflow_base import Workflow
from models import StepDef, CapabilityResult
from engine import engine
from db import get_conn

log = logging.getLogger(__name__)


class HostopsUpdateWorkflow(Workflow):
    name = "hostops.update"
    approval_policy = "council_gate"
    steps = [
        StepDef("update_gate", step_type="approval_gate"),
        # A resolved hostops gate is single-use — retrying would fail closed.
        StepDef("update_exec", capability="hostops.update_wp", max_retries=0),
        StepDef("complete",    finalize=True),
    ]

    # ── Context aggregation (mirrors hostops_deploy) ───────────────────────────

    def _ctx(self) -> dict:
        conn = get_conn()
        rows = conn.execute(
            "SELECT name, result FROM steps WHERE job_id=? AND status='succeeded'",
            (self.job_id,),
        ).fetchall()
        conn.close()
        ctx: dict = {}
        for row in rows:
            if row["result"]:
                try:
                    ctx.update(json.loads(row["result"]))
                except Exception:
                    pass
        conn = get_conn()
        job = conn.execute("SELECT inputs FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        conn.close()
        if job:
            for k, v in json.loads(job["inputs"]).items():
                ctx.setdefault(k, v)
        return ctx

    # ── Dispatch ───────────────────────────────────────────────────────────────

    def execute_step(self, step_def: StepDef, step: dict) -> CapabilityResult:
        ctx = self._ctx()
        if step_def.step_type == "approval_gate":
            return self._run_gate(step, ctx)
        if step_def.finalize:
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"complete": True, "job_id": self.job_id},
                verification={"verified": True, "evidence": {"finalize": True}},
            )
        if step_def.name == "update_exec":
            return self._step_update(ctx)
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "no_handler", "step": step_def.name},
        )

    @staticmethod
    def _bad_component(component: object) -> bool:
        """This workflow's SOLE purpose is one update — a missing/invalid component
        is a hard error, never a silent green no-op (unlike hostops_deploy, which
        bundles two independently-optional ops)."""
        from capabilities.hostops import _valid_update_component
        return not _valid_update_component(component)

    # ── Approval gate ──────────────────────────────────────────────────────────

    def _run_gate(self, step: dict, ctx: dict) -> CapabilityResult:
        component = ctx.get("component", "")
        if self._bad_component(component):
            return CapabilityResult(ok=False, status="failed_permanent",
                                    error={"type": "input_not_allowed"})

        # The shared org-model decision is evaluated *before* a council gate is
        # created, so any autonomous path never reaches the council. (For WP
        # updates classify() returns approve for client-owned sites; the exec
        # capability additionally fails closed on autonomous.)
        from policy.autonomy import check_policy
        action, reason = check_policy("hostops.update_wp", "workflow", ctx)
        if action == "allow":
            return self._skip(f"autonomous: {reason}")

        from capabilities import get_capability
        gate_fn = get_capability("council.gate")
        site = ctx.get("site", "")

        # Resolve the app identity from the allowlisted site config, never caller
        # input — audit attribution only (SSH uses the fixed master credential).
        from capabilities.hostops import audit_identity, HostOpsTargetError
        try:
            resolved_identity = audit_identity(site)
        except HostOpsTargetError as exc:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "hostops_identity_unavailable", "detail": str(exc)},
            )

        # hostops_operation + site + hostops_resource are the binding that
        # consume_hostops_gate enforces: one approval == one exact component.
        brief = {
            "job_id": self.job_id,
            "workflow": self.name,
            "hostops_operation": "update_wp",
            "site": site,
            "hostops_resource": str(component),
            "component": str(component),
            "audit_identity": resolved_identity,
            "required_decision": f"explicit human approval to update '{component}' on host {site}",
        }
        return gate_fn(
            job_id=self.job_id,
            step_id=step["id"],
            brief=brief,
            gate_type="hostops_update_wp",
        )

    # ── Mutating exec step ─────────────────────────────────────────────────────

    def _step_update(self, ctx: dict) -> CapabilityResult:
        component = ctx.get("component", "")
        if self._bad_component(component):
            return CapabilityResult(ok=False, status="failed_permanent",
                                    error={"type": "input_not_allowed"})

        site = ctx.get("site", "")

        from policy.autonomy import check_policy
        policy_action, _ = check_policy("hostops.update_wp", "workflow", ctx)
        gate_id = engine.find_resolved_hostops_gate(self.job_id, "update_wp", site)
        if policy_action != "allow" and not gate_id:
            return CapabilityResult(
                ok=False, status="failed_permanent", error={"type": "gate_required"},
            )

        from capabilities import get_capability
        fn = get_capability("hostops.update_wp")
        return fn(site=site, component=component, gate_id=gate_id)

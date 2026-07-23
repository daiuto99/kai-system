"""hostops_deploy workflow — KAI-820 HOSTOPS-(c): gated privileged host-ops.

Routes the two *mutating* host-ops through a council gate so no host mutation
executes without a real, human-approved, single-use gate:

  1. place_secret_gate  — approval_gate (gate_type hostops_place_secret)
  2. place_secret_exec  — hostops.place_secret, using the verified gate_id
  3. deploy_plugin_gate — approval_gate (gate_type hostops_deploy_plugin)
  4. deploy_plugin_exec — hostops.deploy_plugin, using the verified gate_id
  5. complete           — finalize

The gate/exec split is required: a resolved approval_gate step is already
'succeeded' and never re-runs, so the mutation must be a *separate* step that
runs after the gate resolves. On resolve the council callback transitions the
gate step to succeeded; resume() then runs the exec step, which re-derives the
approved gate_id from the persistent store (engine.find_resolved_hostops_gate)
and passes it to the capability. hostops._gate() -> engine.consume_hostops_gate
verifies the gate is resolved, correctly bound, and marks it consumed
(single-use / replay refused).

Design §3.3.1 (payload never crosses the persisted gate): place_secret's
workflow input carries only ``secret_name`` — a reference — never the bytes. The
payload is resolved to bytes in-process at exec time via HostOpsSecretResolver.
The gate brief carries op + site + secret_name + audit identity; never key bytes
or the secret payload (L18).

Read-only hostops.status / hostops.verify stay autonomous and are deliberately
not part of this gated workflow.
"""
import json
import logging

from workflow_base import Workflow
from models import StepDef, CapabilityResult
from engine import engine
from db import get_conn

log = logging.getLogger(__name__)


class HostopsDeployWorkflow(Workflow):
    name = "hostops.deploy"
    approval_policy = "council_gate"
    steps = [
        StepDef("place_secret_gate",  step_type="approval_gate"),
        # A resolved hostops gate is single-use. Retrying an execution would
        # necessarily fail closed after the first attempt consumes its gate.
        StepDef("place_secret_exec",  capability="hostops.place_secret",  max_retries=0),
        StepDef("deploy_plugin_gate", step_type="approval_gate"),
        StepDef("deploy_plugin_exec", capability="hostops.deploy_plugin", max_retries=0),
        StepDef("complete",           finalize=True),
    ]

    # ── Context aggregation (mirrors the homepage workflow) ────────────────

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

    # ── Dispatch ───────────────────────────────────────────────────────────

    def execute_step(self, step_def: StepDef, step: dict) -> CapabilityResult:
        name = step_def.name
        ctx = self._ctx()

        if step_def.step_type == "approval_gate":
            return self._run_gate(name, step, ctx)

        if step_def.finalize:
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"complete": True, "job_id": self.job_id},
                verification={"verified": True, "evidence": {"finalize": True}},
            )

        handler = {
            "place_secret_exec":  self._step_place_secret,
            "deploy_plugin_exec": self._step_deploy_plugin,
        }.get(name)
        if handler is None:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "no_handler", "step": name},
            )
        return handler(ctx)

    # ── Skip helper ────────────────────────────────────────────────────────

    @staticmethod
    def _skip(reason: str) -> CapabilityResult:
        """A requested-op-absent step succeeds as an explicit, verified no-op.

        Not a silent cap: the skip reason is recorded in the step verification.
        """
        return CapabilityResult(
            ok=True, status="succeeded",
            data={"skipped": True, "reason": reason},
            verification={"verified": True, "evidence": {"skipped": reason}},
        )

    @staticmethod
    def _op_for(step_name: str) -> str:
        return "place_secret" if step_name.startswith("place_secret") else "deploy_plugin"

    @staticmethod
    def _requested(op: str, ctx: dict) -> bool:
        if op == "place_secret":
            return bool(ctx.get("secret_name"))
        return bool(ctx.get("plugin"))

    # ── Approval gate ──────────────────────────────────────────────────────

    def _run_gate(self, step_name: str, step: dict, ctx: dict) -> CapabilityResult:
        op = self._op_for(step_name)
        if not self._requested(op, ctx):
            return self._skip(f"no {op} requested")

        # The shared org-model decision is evaluated *before* a council gate is
        # created.  Autonomous work therefore never reaches the council/T2 path.
        from policy.autonomy import check_policy
        action, reason = check_policy(f"hostops.{op}", "workflow", ctx)
        if action == "allow":
            return self._skip(f"autonomous: {reason}")

        from capabilities import get_capability
        gate_fn = get_capability("council.gate")
        site = ctx.get("site", "")

        # C-1: resolve the app identity from the allowlisted site configuration,
        # never from caller input. This is audit attribution only: SSH always
        # uses the fixed master-operator credential, never a per-app key.
        from capabilities.hostops import audit_identity, HostOpsTargetError
        try:
            resolved_identity = audit_identity(site)
        except HostOpsTargetError as exc:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "hostops_identity_unavailable", "detail": str(exc)},
            )

        # hostops_operation + site are the binding consume_hostops_gate enforces.
        brief = {
            "job_id": self.job_id,
            "workflow": self.name,
            "hostops_operation": op,
            "site": site,
            "audit_identity": resolved_identity,
            "required_decision": f"explicit human approval to {op} on host {site}",
        }
        if op == "place_secret":
            # A reference only — never the payload bytes (L18 / §3.3.1).
            brief["secret_name"] = ctx.get("secret_name", "")
        else:
            brief["plugin"] = ctx.get("plugin", "")

        return gate_fn(
            job_id=self.job_id,
            step_id=step["id"],
            brief=brief,
            gate_type=f"hostops_{op}",
        )

    # ── Mutating exec steps ────────────────────────────────────────────────

    def _step_place_secret(self, ctx: dict) -> CapabilityResult:
        if not self._requested("place_secret", ctx):
            return self._skip("no place_secret requested")

        site = ctx.get("site", "")
        secret_name = ctx.get("secret_name", "")

        from policy.autonomy import check_policy
        policy_action, _ = check_policy("hostops.place_secret", "workflow", ctx)
        gate_id = engine.find_resolved_hostops_gate(self.job_id, "place_secret", site)
        if policy_action != "allow" and not gate_id:
            return CapabilityResult(
                ok=False, status="failed_permanent", error={"type": "gate_required"},
            )

        # Resolve the payload to bytes only now, post-approval, in-process.
        from hostops_identity import HostOpsSecretResolver, HostOpsIdentityError
        from capabilities.hostops import InMemorySecret
        try:
            material = HostOpsSecretResolver().resolve(site, secret_name)
        except HostOpsIdentityError as exc:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "secret_unavailable", "detail": str(exc)},
            )

        from capabilities import get_capability
        fn = get_capability("hostops.place_secret")
        return fn(site=site, secret_name=secret_name,
                  secret=InMemorySecret(material=material), gate_id=gate_id)

    def _step_deploy_plugin(self, ctx: dict) -> CapabilityResult:
        if not self._requested("deploy_plugin", ctx):
            return self._skip("no deploy_plugin requested")

        site = ctx.get("site", "")
        plugin = ctx.get("plugin", "")

        from policy.autonomy import check_policy
        policy_action, _ = check_policy("hostops.deploy_plugin", "workflow", ctx)
        gate_id = engine.find_resolved_hostops_gate(self.job_id, "deploy_plugin", site)
        if policy_action != "allow" and not gate_id:
            return CapabilityResult(
                ok=False, status="failed_permanent", error={"type": "gate_required"},
            )

        from capabilities import get_capability
        fn = get_capability("hostops.deploy_plugin")
        return fn(site=site, plugin=plugin, gate_id=gate_id)

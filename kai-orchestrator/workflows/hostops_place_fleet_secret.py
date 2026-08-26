"""hostops_place_fleet_secret workflow — AR-2 (KAI-929): gated placement of an
existing named secret onto a fleet host (starting with 71-kai-mini).

Mirrors hostops_deploy's gate/exec split. A resolved approval_gate step is already
'succeeded' and never re-runs, so the placement is a *separate* exec step that runs
after the gate resolves: on resolve the council callback transitions the gate step
to succeeded; resume() runs the exec step, which resolves the secret to bytes
in-process (never persisted / logged — L18), re-derives the approved gate_id from
the persistent store (engine.find_resolved_hostops_gate), and calls
hostops.place_fleet_secret with it.

The gate brief carries op + host + secret_name + audit identity — NEVER the bytes
(§3.3.1). The gate is bound to the exact (host, secret_name) via hostops_resource,
so one approval can never place a different secret or reach a different host. This
placement is NEVER autonomous: policy is requires_approval and the gate step fails
closed if the org-model ever returns 'allow' (defence against drift).
"""
import json
import logging
import re
from pathlib import Path

from workflow_base import Workflow
from models import StepDef, CapabilityResult
from engine import engine
from db import get_conn

log = logging.getLogger(__name__)

# The exec step resolves the named secret from the orchestrator's own mounted
# docker secrets (/run/secrets/<name>), in-process at exec time only. The bytes
# are never written to a step result, log, or gate brief (L18).
_RUN_SECRETS = Path("/run/secrets")
_SAFE_SECRET_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class HostopsPlaceFleetSecretWorkflow(Workflow):
    name = "hostops.place_fleet_secret"
    approval_policy = "council_gate"
    steps = [
        StepDef("place_fleet_secret_gate", step_type="approval_gate"),
        # A resolved hostops gate is single-use; a retry would fail closed after the
        # first attempt consumes its gate.
        StepDef("place_fleet_secret_exec", capability="hostops.place_fleet_secret", max_retries=0),
        StepDef("complete", finalize=True),
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
            return self._run_gate(step_def.name, step, ctx)
        if step_def.finalize:
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"complete": True, "job_id": self.job_id},
                verification={"verified": True, "evidence": {"finalize": True}},
            )
        if step_def.name == "place_fleet_secret_exec":
            return self._step_place_fleet_secret(ctx)
        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "no_handler", "step": step_def.name},
        )

    # ── Approval gate ────────────────────────────────────────────────────────────

    def _run_gate(self, step_name: str, step: dict, ctx: dict) -> CapabilityResult:
        host = ctx.get("host", "")
        secret_name = ctx.get("secret_name", "")
        if not host or not secret_name:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "input_not_allowed", "detail": "host and secret_name are required"},
            )

        # Evaluate the org-model decision before a council gate is created. This
        # capability is requires_approval, so 'allow' must never happen — fail
        # closed if it ever does (defence against policy drift).
        from policy.autonomy import check_policy
        action, reason = check_policy(
            "hostops.place_fleet_secret", "workflow", {"site": host, "secret_name": secret_name})
        if action == "allow":
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "autonomous_fleet_secret_forbidden"},
            )

        # Resolve the fleet identity from the allowlist, never from caller input.
        from capabilities.hostops import _fleet_target, HostOpsTargetError
        try:
            resolved_identity = _fleet_target(host).audit_identity
        except HostOpsTargetError as exc:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "hostops_identity_unavailable", "detail": str(exc)},
            )

        # hostops_operation + site + hostops_resource are the bindings that
        # consume_hostops_gate enforces. secret_name is a reference only — the bytes
        # never enter the persisted brief (L18 / §3.3.1).
        brief = {
            "job_id": self.job_id,
            "workflow": self.name,
            "hostops_operation": "place_fleet_secret",
            "site": host,
            "hostops_resource": secret_name,
            "secret_name": secret_name,
            "audit_identity": resolved_identity,
            "required_decision": f"explicit human approval to place secret '{secret_name}' on fleet host {host}",
        }
        from capabilities import get_capability
        gate_fn = get_capability("council.gate")
        return gate_fn(
            job_id=self.job_id,
            step_id=step["id"],
            brief=brief,
            gate_type="hostops_place_fleet_secret",
        )

    # ── Mutating exec step ───────────────────────────────────────────────────────

    def _step_place_fleet_secret(self, ctx: dict) -> CapabilityResult:
        host = ctx.get("host", "")
        secret_name = ctx.get("secret_name", "")
        if not _SAFE_SECRET_NAME.fullmatch(secret_name or ""):
            return CapabilityResult(
                ok=False, status="failed_permanent", error={"type": "input_not_allowed"})

        gate_id = engine.find_resolved_hostops_gate(self.job_id, "place_fleet_secret", host)
        if not gate_id:
            return CapabilityResult(
                ok=False, status="failed_permanent", error={"type": "gate_required"})

        # Resolve the payload to bytes only now, post-approval, in-process (L18).
        src = _RUN_SECRETS / secret_name
        try:
            material = src.read_bytes()
        except OSError:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "secret_unavailable", "detail": f"source secret '{secret_name}' not mounted"},
            )
        # Docker secret files commonly carry a trailing newline; strip trailing
        # CR/LF so the key lands on the host exactly as the consumer expects.
        material = material.rstrip(b"\r\n")
        if not material:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "secret_unavailable", "detail": "source secret is empty"},
            )

        from capabilities.hostops import InMemorySecret
        from capabilities import get_capability
        fn = get_capability("hostops.place_fleet_secret")
        return fn(host=host, secret_name=secret_name,
                  secret=InMemorySecret(material=material), gate_id=gate_id)

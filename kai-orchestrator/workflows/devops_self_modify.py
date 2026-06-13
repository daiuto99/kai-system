"""devops_self_modify workflow — M2-1.A scaffold.

Single-step workflow that records a self-modify proposal via the
self_modify.propose capability. M2-1.A does not apply the diff,
does not verify semantically, and does not open a Plane gate —
those are M2-1.B and M2-1.C.

Inputs (required, validated by self_modify.propose):
  plane_ticket_id, gate, principle, retirement, diff
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
        StepDef("log_proposal",
                capability="self_modify.propose",
                step_type="auto"),
    ]

    def _inputs(self) -> dict:
        conn = get_conn()
        row = conn.execute(
            "SELECT inputs FROM jobs WHERE id=?", (self.job_id,)
        ).fetchone()
        conn.close()
        return json.loads(row["inputs"]) if row else {}

    def execute_step(self, step_def: StepDef, step: dict) -> CapabilityResult:
        if step_def.name == "log_proposal":
            inputs = self._inputs()
            fn = get_capability("self_modify.propose")
            return fn(**inputs)

        return CapabilityResult(
            ok=False, status="failed_permanent",
            error={"type": "unknown_step", "name": step_def.name},
        )

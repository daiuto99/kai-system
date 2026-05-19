"""hello_world workflow — Sprint 0B validation workflow.

Runs 3 steps, persists to SQLite, survives process kill + restart.
Exit criteria: run, kill mid-step, restart, verify resumes and completes.
"""
import time
from workflow_base import Workflow
from models import StepDef, CapabilityResult

class HelloWorldWorkflow(Workflow):
    name = "hello_world"
    approval_policy = "auto"
    steps = [
        StepDef("step_1_greet",  step_type="auto"),
        StepDef("step_2_wait",   step_type="auto"),
        StepDef("step_3_finish", step_type="auto"),
    ]

    def execute_step(self, step_def, step):
        name = step_def.name
        inputs = self._inputs()

        if name == "step_1_greet":
            msg = f"Hello, {inputs.get('name', 'world')}!"
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"message": msg},
                verification={"verified": True, "evidence": {"message": msg}},
            )

        if name == "step_2_wait":
            time.sleep(0.1)
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"waited_ms": 100},
                verification={"verified": True},
            )

        if name == "step_3_finish":
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"done": True},
                verification={"verified": True, "evidence": {"done": True}},
            )

        return CapabilityResult(ok=False, status="failed_permanent",
                                error={"type": "unknown_step", "name": name})

    def _inputs(self):
        import json
        from db import get_conn
        conn = get_conn()
        row = conn.execute("SELECT inputs FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        conn.close()
        return json.loads(row["inputs"]) if row else {}

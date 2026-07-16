"""BUG-14: a permanently failed sole step must fail its parent job."""
import json
import tempfile
from pathlib import Path
from unittest import mock

import db
from engine import engine
from models import CapabilityResult, StepDef
from workflow_base import Workflow


class F29d1644ShapeWorkflow(Workflow):
    """The single-step permanent-failure shape recorded for job f29d1644."""

    name = "devops.self_modify"
    steps = [StepDef("verify_request", "devops.self_modify", max_retries=0)]

    def execute_step(self, step_def, step):
        return CapabilityResult(
            ok=False,
            status="failed_permanent",
            error={"missing_required_fields": ["retirement"]},
        )


def test_single_failed_permanent_step_rolls_job_up_as_failed_permanent():
    """Live post-step state, not the stale pre-run row, controls job rollup."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            workflow = F29d1644ShapeWorkflow.start({"retirement": ""})
            workflow.resume()

            conn = db.get_conn()
            try:
                job = conn.execute(
                    "SELECT status FROM jobs WHERE id=?", (workflow.job_id,)
                ).fetchone()
                step = conn.execute(
                    "SELECT status, error FROM steps WHERE job_id=?", (workflow.job_id,)
                ).fetchone()
            finally:
                conn.close()

    assert step["status"] == "failed_permanent"
    assert json.loads(step["error"]) == {"missing_required_fields": ["retirement"]}
    assert job["status"] == "failed_permanent"

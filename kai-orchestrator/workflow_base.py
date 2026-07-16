import json, logging, time
from typing import List, Optional
from db import get_conn, new_id, now_iso
from engine import engine
from models import StepDef, CapabilityResult

logger = logging.getLogger(__name__)

class Workflow:
    """Base class for all orchestrator workflows.

    Subclasses declare steps as a list of StepDef. The engine
    persists state — a workflow that is killed mid-run resumes
    from the last verified step on restart.
    """
    name: str = ""
    approval_policy: str = "auto"
    steps: List[StepDef] = []

    def __init__(self, job_id: str):
        self.job_id = job_id

    @classmethod
    def start(cls, inputs: dict) -> "Workflow":
        job_id = engine.create_job(cls.name, inputs, cls.approval_policy)
        instance = cls(job_id)
        conn = get_conn()
        for step_def in cls.steps:
            engine.create_step(job_id, step_def.name, step_def.capability, step_def.step_type)
        engine.transition("job", job_id, "running")
        return instance

    def resume(self):
        """Run all pending steps in order. Stops at awaiting_gate (resumes via callback)."""
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM steps WHERE job_id=? ORDER BY rowid", (self.job_id,)
        ).fetchall()
        conn.close()

        for row in rows:
            if row["status"] == "succeeded":
                continue
            if row["status"] == "awaiting_gate":
                # A council gate is open — stop here. The /gates/{id}/resolve callback
                # will re-trigger resume() when the gate resolves.
                logger.info("Job %s paused at gate step %s", self.job_id, row["name"])
                return
            if row["status"] in ("failed_permanent", "cancelled"):
                engine.transition("job", self.job_id, "failed_permanent",
                                  error=f"Step {row['name']} permanently failed")
                return
            should_continue = self._run_step(dict(row))
            if not should_continue:
                return  # gate opened mid-run, stop

        # _run_step() persists terminal state after this method has taken its
        # initial snapshot. Re-read the authoritative step rows before rolling
        # the job up so a newly permanent failure cannot become job success.
        conn = get_conn()
        try:
            final_rows = conn.execute(
                "SELECT name, status FROM steps WHERE job_id=? ORDER BY rowid",
                (self.job_id,),
            ).fetchall()
        finally:
            conn.close()
        failed = next(
            (row for row in final_rows if row["status"] in ("failed_permanent", "cancelled")),
            None,
        )
        if failed:
            engine.transition("job", self.job_id, "failed_permanent",
                              error=f"Step {failed['name']} permanently failed")
            return
        if any(row["status"] != "succeeded" for row in final_rows):
            logger.warning("Job %s did not reach all-succeeded terminal state", self.job_id)
            return

        engine.transition("job", self.job_id, "succeeded")

    def _run_step(self, step: dict) -> bool:
        """Execute one step. Returns True to continue, False to pause (gate opened)."""
        step_def = next((s for s in self.steps if s.name == step["name"]), None)
        if not step_def:
            raise ValueError(f"No StepDef for step name: {step['name']}")

        engine.transition("step", step["id"], "running")
        started = time.monotonic()

        try:
            result: CapabilityResult = self.execute_step(step_def, step)
            latency_ms = int((time.monotonic() - started) * 1000)

            # Council gate — step pauses until callback arrives
            if result.status == "awaiting_gate":
                engine.transition("step", step["id"], "awaiting_gate",
                                  result=result.data)
                logger.info("Step %s waiting on gate %s",
                            step["name"], (result.data or {}).get("gate_id"))
                return False

            if result.ok:
                # Auto-verify for 'auto' step types when capability doesn't supply verification
                verification = result.verification
                if verification is None and step_def.step_type == "auto":
                    verification = {"verified": True,
                                    "evidence": {"auto_verified": True,
                                                 "capability": step_def.capability}}
                engine.transition("step", step["id"], "succeeded",
                                  verification=verification,
                                  result=result.data)
                self._record_metric(step, step_def, result, latency_ms, verified=True,
                                   provider=result.provider, model=result.model,
                                   cost_usd=result.cost_usd,
                                   cache_read_tokens=result.cache_read_tokens,
                                   cache_creation_tokens=result.cache_creation_tokens)
                return True
            else:
                retry = step.get("retry_count", 0)
                if retry < step_def.max_retries:
                    conn = get_conn()
                    conn.execute("UPDATE steps SET retry_count=retry_count+1 WHERE id=?",
                                 (step["id"],))
                    conn.commit(); conn.close()
                    engine.transition("step", step["id"], "pending")
                    return self._run_step({**step, "retry_count": retry + 1})
                else:
                    engine.transition("step", step["id"], "failed_permanent",
                                      error=json.dumps(result.error))
                    return True  # let resume() see the failed_permanent on next iteration

        except Exception as e:
            logger.exception("Step %s raised", step["name"])
            engine.transition("step", step["id"], "failed_recoverable", error=str(e))
            raise

    def execute_step(self, step_def: StepDef, step: dict) -> CapabilityResult:
        """Override in subclass to implement step logic."""
        raise NotImplementedError(f"execute_step not implemented for {step_def.name}")

    def _record_metric(self, step, step_def, result, latency_ms, verified,
                       provider=None, model=None, cost_usd=0.0,
                       cache_read_tokens=0, cache_creation_tokens=0):
        conn = get_conn()
        conn.execute(
            """INSERT INTO workflow_metrics
               (id,job_id,step_name,capability,transport_used,latency_ms,verified_first_try,
                retry_count,provider,model,cost_usd,cache_read_tokens,cache_creation_tokens,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id(), self.job_id, step["name"], step_def.capability,
             result.transport_used, latency_ms,
             1 if verified and step.get("retry_count", 0) == 0 else 0,
             step.get("retry_count", 0),
             provider, model, cost_usd or 0.0,
             cache_read_tokens or 0, cache_creation_tokens or 0,
             now_iso()),
        )
        conn.commit(); conn.close()

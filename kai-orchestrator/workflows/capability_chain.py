"""capability_chain workflow — run an arbitrary sequence of capabilities as a tracked job.

Inputs:
  title   (str, optional)  — human-readable job name shown in notifications
  chain   (list, required) — ordered list of {"capability": "name", "inputs": {...}}

Example:
  {
    "title": "Morning sync",
    "chain": [
      {"capability": "vault.read",  "inputs": {"path": "00_System/status.md"}},
      {"capability": "notify.post",  "inputs": {"channel": "kai-system", "text": "..."}}
    ]
  }
"""
import json
import logging
from workflow_base import Workflow
from models import StepDef, CapabilityResult
from engine import engine
from db import get_conn

logger = logging.getLogger(__name__)

_SAFE_CAP_NAME = str.maketrans(".", "_", "")


def _step_name(idx: int, cap: str) -> str:
    return f"step_{idx}_{cap.translate(_SAFE_CAP_NAME)}"


class CapabilityChainWorkflow(Workflow):
    name = "capability_chain"
    approval_policy = "auto"
    steps = []  # populated dynamically per-instance

    def __init__(self, job_id: str):
        super().__init__(job_id)
        self.steps = self._build_step_defs()

    def _build_step_defs(self):
        conn = get_conn()
        row = conn.execute("SELECT inputs FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        conn.close()
        if not row:
            return []
        chain = json.loads(row["inputs"]).get("chain", [])
        return [
            StepDef(_step_name(i, item["capability"]),
                    capability=item["capability"],
                    step_type="auto")
            for i, item in enumerate(chain)
        ]

    @classmethod
    def start(cls, inputs: dict) -> "Workflow":
        chain = inputs.get("chain", [])
        if not chain:
            raise ValueError("capability_chain requires at least one step in 'chain'")
        job_id = engine.create_job(cls.name, inputs, cls.approval_policy)
        instance = cls(job_id)
        for step_def in instance.steps:
            engine.create_step(job_id, step_def.name, step_def.capability, step_def.step_type)
        engine.transition("job", job_id, "running")
        return instance

    def execute_step(self, step_def: StepDef, step: dict) -> CapabilityResult:
        conn = get_conn()
        row = conn.execute("SELECT inputs FROM jobs WHERE id=?", (self.job_id,)).fetchone()
        conn.close()
        chain = json.loads(row["inputs"]).get("chain", [])

        # Parse index from step name: "step_2_vault_read" → 2
        try:
            idx = int(step_def.name.split("_")[1])
        except (IndexError, ValueError):
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "bad_step_name", "name": step_def.name}
            )

        if idx >= len(chain):
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "chain_index_out_of_range", "idx": idx}
            )

        item = chain[idx]
        cap_name = item["capability"]
        cap_inputs = item.get("inputs", {})

        try:
            from capabilities import get_capability
            fn = get_capability(cap_name)
            result = fn(**cap_inputs)
            return result
        except KeyError:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "unknown_capability", "capability": cap_name}
            )
        except TypeError as e:
            return CapabilityResult(
                ok=False, status="failed_permanent",
                error={"type": "bad_inputs", "capability": cap_name, "detail": str(e)}
            )
        except Exception as e:
            logger.exception("capability_chain step %s failed", cap_name)
            return CapabilityResult(
                ok=False, status="failed_recoverable",
                error={"type": "capability_error", "capability": cap_name, "detail": str(e)}
            )

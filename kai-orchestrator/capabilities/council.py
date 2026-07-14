"""council.gate capability — opens a council review gate in a workflow step.

The capability POSTs a brief to the council-api, which processes it async
and calls back to /gates/{gate_id}/resolve on the orchestrator.
The workflow step transitions to awaiting_gate and resume() pauses.
"""
import os
from db import new_id
from engine import engine
from models import CapabilityResult
from transports.base import safe_request
from context_service import _worker_auth
from . import capability

_ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_INTERNAL_URL", "http://kai-orchestrator:8003")
_COUNCIL_API_URL  = os.environ.get("COUNCIL_API_URL",           "http://kai-council-api:8002")


@capability("council.gate")
def gate(job_id: str, step_id: str, brief: dict, gate_type: str = "dev",
         creds: dict = None, **_) -> CapabilityResult:
    """Open a council gate. Returns awaiting_gate so the workflow pauses."""
    gate_id      = new_id()
    callback_url = f"{_ORCHESTRATOR_URL}/gates/{gate_id}/resolve"

    # Persist the gate before calling out (so resolve can always find it)
    engine.open_gate(gate_id, job_id, step_id, gate_type, brief, callback_url)

    # Notify council-api — it will process async and POST back to callback_url
    r = safe_request(
        "POST", f"{_COUNCIL_API_URL}/council/gate",
        json={
            "gate_id":      gate_id,
            "gate_type":    gate_type,
            "brief":        brief,
            "callback_url": callback_url,
        },
        timeout=10,
        auth=_worker_auth(),
    )

    if not r.ok:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "council_notify_failed",
                   "status_code": r.status_code,
                   "detail": r.body_preview},
        )

    return CapabilityResult(
        ok=True,
        status="awaiting_gate",
        data={"gate_id": gate_id, "gate_type": gate_type, "callback_url": callback_url},
        verification={"verified": False},
    )

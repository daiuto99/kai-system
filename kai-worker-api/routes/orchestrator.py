"""routes/orchestrator.py — worker-api proxy to kai-orchestrator workflows.

M2-1.A scaffolds the devops self-modify entry point. The route accepts the
pre-artifact ritual (gate §3 line, principle §5 line, retirement) plus the
Plane ticket id and a unified diff, then starts the orchestrator's
devops.self_modify workflow and returns the workflow_id.

M2-1.A does NOT apply the diff — it only logs the proposal. M2-1.B adds the
semantic verifier + apply/commit/Plane chain. M2-1.C adds the KAI-mediated
approval prompt that replaces the binary Mode-Lock YES unlock.
"""
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter()

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL",
                                  "http://kai-orchestrator:8003")
ORCHESTRATOR_TIMEOUT_S = 30


class DevopsSelfModifyRequest(BaseModel):
    plane_ticket_id: str = Field(..., description="Plane issue id this self-modify resolves")
    gate: str = Field(..., description="JARVIS_DEFINITION §3 gate line moved toward")
    principle: str = Field(..., description="LSE_BUILD_PROFILE §5 operating principle invoked")
    retirement: str = Field(..., description="What gets retired or simplified alongside this change")
    diff: str = Field(..., description="Unified diff to apply (NOT applied in M2-1.A)")


@router.post("/orchestrator/devops_self_modify")
def devops_self_modify(req: DevopsSelfModifyRequest):
    """Start the devops.self_modify workflow on the orchestrator.

    Returns 200 with the orchestrator workflow_id (== job_id) on success.
    The workflow logs a structured proposal record to
    /vault/00_System/self_modify_proposals.jsonl. No diff is applied
    in M2-1.A.
    """
    payload = {
        "type": "devops.self_modify",
        "inputs": req.dict(),
    }
    try:
        with httpx.Client(timeout=ORCHESTRATOR_TIMEOUT_S) as client:
            r = client.post(f"{ORCHESTRATOR_URL}/workflows/run", json=payload)
    except httpx.RequestError as e:
        logger.exception("Orchestrator unreachable")
        raise HTTPException(502, f"orchestrator unreachable: {e}")

    if r.status_code != 200:
        logger.error("Orchestrator non-200: %s %s", r.status_code, r.text)
        raise HTTPException(502, f"orchestrator returned {r.status_code}: {r.text[:200]}")

    body = r.json()
    if "error" in body:
        raise HTTPException(400, body["error"])

    workflow_id = body.get("job_id")
    if not workflow_id:
        raise HTTPException(502, f"orchestrator response missing job_id: {body}")

    return {
        "ok": True,
        "workflow_id": workflow_id,
        "workflow_type": "devops.self_modify",
        "stage": "M2-1.A",
        "status": body.get("status", "started"),
    }

"""routes/orchestrator.py — worker-api proxy to kai-orchestrator workflows.

M2-1.B adds `target_root` to the request contract and bumps the stage label.
The route still just proxies to /workflows/run; the workflow does the work.
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

_TARGET_ROOT_ALLOWLIST = {"/kai-system", "/workspace"}


class DevopsSelfModifyRequest(BaseModel):
    plane_ticket_id: str = Field(..., description="Plane issue id this self-modify resolves")
    gate: str = Field(..., description="JARVIS_DEFINITION §3 gate line moved toward")
    principle: str = Field(..., description="LSE_BUILD_PROFILE §5 operating principle invoked")
    retirement: str = Field(..., description="What gets retired or simplified alongside this change")
    diff: str = Field(..., description="Unified diff to apply (paths relative to target_root)")
    target_root: str = Field(
        "/kai-system",
        description="Root the diff applies inside. Must be in allowlist: /kai-system or /workspace.",
    )


@router.post("/orchestrator/devops_self_modify")
def devops_self_modify(req: DevopsSelfModifyRequest):
    """Start the devops.self_modify workflow on the orchestrator.

    Returns 200 with the orchestrator workflow_id on success. The workflow
    runs: log_proposal → verify_semantic → apply_diff → commit → update_plane.
    Verifier reject (or any step fail) stops the chain; nothing after that
    step runs.
    """
    if req.target_root not in _TARGET_ROOT_ALLOWLIST:
        raise HTTPException(
            400,
            f"target_root not allowed: {req.target_root!r}. "
            f"Must be one of {sorted(_TARGET_ROOT_ALLOWLIST)}",
        )

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
        "stage": "M2-1.B",
        "target_root": req.target_root,
        "status": body.get("status", "started"),
    }

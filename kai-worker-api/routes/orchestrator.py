"""routes/orchestrator.py — worker-api proxy to kai-orchestrator workflows.

M2-1.B adds `target_root` to the request contract and bumps the stage label.
The route still just proxies to /workflows/run; the workflow does the work.
"""
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from safe_http import safe_json

logger = logging.getLogger(__name__)
router = APIRouter()

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL",
                                  "http://kai-orchestrator:8003")
ORCHESTRATOR_TIMEOUT_S = 30


def _gate_resolve_secret() -> str:
    """C2 [SEC] 8ae14701 phase-2 — dedicated credential for the orchestrator
    gate-resolve/override boundary (mirrors the council-api phase-1 guard)."""
    for _p in ("/run/secrets/gate_resolve_secret",
               "/home/leo/kai-system/secrets/gate_resolve_secret.txt"):
        try:
            _s = open(_p).read().strip()
        except OSError:
            continue
        if _s:
            return _s
    return ""

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

    body = safe_json(r)
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



# ── WP AR-1 gap4 — launch ergonomics for governed WP workflows ─────────────
# Before this, launching wordpress.build_page_draft meant docker-exec into the
# orchestrator network (port 8003 isn't host-mapped), hand-crafting the run JSON,
# then polling the DB to find the open gate id. These three authed endpoints put
# launch + gate-resolve on the worker-api the dashboard already talks to.
# safe_json is defined outside routes/, so parsing here stays free of the
# bare-.json() the L3 guard forbids.

def _orchestrator_post(path: str, payload: dict) -> dict:
    try:
        with httpx.Client(timeout=ORCHESTRATOR_TIMEOUT_S) as client:
            r = client.post(f"{ORCHESTRATOR_URL}{path}", json=payload,
                            headers={"X-KAI-Gate-Resolve": _gate_resolve_secret()})
    except httpx.RequestError as e:
        logger.exception("Orchestrator unreachable")
        raise HTTPException(502, f"orchestrator unreachable: {e}")
    if r.status_code != 200:
        # Surface upstream CLIENT errors faithfully; only genuine 5xx/gateway
        # failures read as 502 (bad gateway) — a 404/400 must not masquerade as one.
        if 400 <= r.status_code < 500:
            raise HTTPException(r.status_code, f"orchestrator: {r.text[:200]}")
        raise HTTPException(502, f"orchestrator returned {r.status_code}: {r.text[:200]}")
    body = safe_json(r)
    if isinstance(body, dict) and body.get("error"):
        raise HTTPException(400, body["error"])
    return body


def _orchestrator_get(path: str) -> dict:
    try:
        with httpx.Client(timeout=ORCHESTRATOR_TIMEOUT_S) as client:
            r = client.get(f"{ORCHESTRATOR_URL}{path}")
    except httpx.RequestError as e:
        logger.exception("Orchestrator unreachable")
        raise HTTPException(502, f"orchestrator unreachable: {e}")
    if r.status_code != 200:
        # Surface upstream CLIENT errors faithfully; only genuine 5xx/gateway
        # failures read as 502 (bad gateway) — a 404/400 must not masquerade as one.
        if 400 <= r.status_code < 500:
            raise HTTPException(r.status_code, f"orchestrator: {r.text[:200]}")
        raise HTTPException(502, f"orchestrator returned {r.status_code}: {r.text[:200]}")
    return safe_json(r)


class BuildPageDraftRequest(BaseModel):
    site: str = Field(..., description="Brand-seeded site slug (e.g. the71c)")
    page_title: str = Field(..., description="Draft page title")
    page_content: str | None = Field(
        None, description="Authored page HTML. Omit to let create_page use its stub; "
                          "the creative gate still reviews the property's approved brief (gap2).")
    property: str | None = Field(
        None, description="Brand governance slug override (KAI-39) when it differs from the site key")
    brief_path: str | None = Field(
        None, description="Explicit brief file; otherwise the property's approved BUILD_PROFILE "
                          "is auto-loaded for the creative gate (gap2)")
    probe: bool = Field(
        False, description="MR1 governed-pipeline probe marker — council-api auto-approves "
                           "probe-flagged drafts-only gates")


@router.post("/orchestrator/wordpress/build_page_draft")
def build_page_draft(req: BuildPageDraftRequest):
    """Launch the drafts-only governed WP page build. Never publishes (the
    workflow has no publish/homepage steps). Returns the job id; poll
    GET /orchestrator/jobs/{id} for the pending gate, then resolve it."""
    inputs = {k: v for k, v in req.dict().items() if v is not None}
    body = _orchestrator_post("/workflows/run",
                              {"type": "wordpress.build_page_draft", "inputs": inputs})
    job_id = body.get("job_id")
    if not job_id:
        raise HTTPException(502, f"orchestrator response missing job_id: {body}")
    return {
        "ok": True,
        "workflow_id": job_id,
        "workflow_type": "wordpress.build_page_draft",
        "status": body.get("status", "started"),
        "next": f"GET /orchestrator/jobs/{job_id} to read the pending gate, then "
                f"POST /orchestrator/gates/<gate_id>/resolve",
    }


@router.get("/orchestrator/jobs/{job_id}")
def get_job_status(job_id: str):
    """Proxy the orchestrator job record and surface the OPEN gate id (if any)
    so callers never have to shell into the container to find it."""
    import json as _json
    body = _orchestrator_get(f"/jobs/{job_id}")
    err = body.get("error")
    if err:
        # orchestrator reports a missing job as {"error": "not found"} on a 200;
        # only that maps to 404 — any other error is a genuine upstream fault.
        raise HTTPException(404 if str(err).strip().lower() == "not found" else 502,
                            f"orchestrator job error: {err}")
    pending_gate = None
    for step in body.get("steps", []):
        if not isinstance(step, dict) or step.get("status") != "awaiting_gate":
            continue
        result = step.get("result")
        if isinstance(result, str):
            try:
                result = _json.loads(result)
            except Exception:
                result = {}
        # a decoded result that is not an object (list/scalar/None) has no gate id
        gate_id = result.get("gate_id") if isinstance(result, dict) else None
        if gate_id:
            pending_gate = {"gate_id": gate_id, "step": step.get("name")}
            break
    return {"ok": True, "job": body.get("job"), "steps": body.get("steps", []),
            "pending_gate": pending_gate}


class GateResolveRequest(BaseModel):
    approved: bool = Field(..., description="Approve (true) or reject (false)")
    advisor: str = Field("leo", description="Who resolved the gate")
    notes: str = Field("", description="Sign-off / rejection reason")


@router.post("/orchestrator/gates/{gate_id}/resolve")
def resolve_orchestrator_gate(gate_id: str, req: GateResolveRequest):
    """Resolve an open approval gate. A 400 means the gate is unknown or already
    resolved (the orchestrator reports that as an error, not a silent no-op)."""
    body = _orchestrator_post(f"/gates/{gate_id}/resolve", req.dict())
    return {"ok": True, "gate_id": gate_id, "job_id": body.get("job_id")}

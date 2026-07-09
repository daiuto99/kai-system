import json
import logging
from datetime import date as _wd
from fastapi import APIRouter, HTTPException, Body
from routes._destructive_audit import DestructiveRequest, audit_before
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

WORKFLOWS_FILE = VAULT_PATH / "00_System" / "workflows.json"
N8N_REGISTRY_FILE = VAULT_PATH / "00_System" / "n8n_workflows.json"
SPECIALISTS_FILE = VAULT_PATH / "00_System" / "specialists.json"


@router.get("/workflows")
def get_workflows():
    if WORKFLOWS_FILE.exists():
        return {"workflows": json.loads(WORKFLOWS_FILE.read_text())}
    return {"workflows": []}


class WorkflowModel(BaseModel):
    id: str
    label: str
    prompt: str
    send: bool = True
    description: str = ""


@router.post("/workflows")
def upsert_workflow(w: WorkflowModel):
    workflows = json.loads(WORKFLOWS_FILE.read_text()) if WORKFLOWS_FILE.exists() else []
    idx = next((i for i, x in enumerate(workflows) if x["id"] == w.id), None)
    entry = w.dict()
    entry["updated"] = _wd.today().isoformat()
    if idx is not None:
        workflows[idx] = entry
    else:
        entry["created"] = _wd.today().isoformat()
        workflows.append(entry)
    WORKFLOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKFLOWS_FILE.write_text(json.dumps(workflows, indent=2))
    return {"ok": True, "workflow": entry}


@router.delete("/workflows/{workflow_id}")
def delete_workflow_endpoint(workflow_id: str, body: DestructiveRequest = Body(...)):
    audit_before("/workflows/{workflow_id}", {"workflow_id": workflow_id}, body.operator, body.reason)
    if not WORKFLOWS_FILE.exists():
        return {"ok": True}
    workflows = [w for w in json.loads(WORKFLOWS_FILE.read_text()) if w["id"] != workflow_id]
    WORKFLOWS_FILE.write_text(json.dumps(workflows, indent=2))
    return {"ok": True}


@router.get("/n8n/workflows")
def list_n8n_workflows():
    if not N8N_REGISTRY_FILE.exists():
        return {"workflows": {}}
    return {"workflows": json.loads(N8N_REGISTRY_FILE.read_text())}


@router.post("/n8n/workflows")
def register_n8n_workflow(body: dict):
    registry = json.loads(N8N_REGISTRY_FILE.read_text()) if N8N_REGISTRY_FILE.exists() else {}
    name = body.get("name")
    if not name:
        raise HTTPException(400, "name required")
    registry[name] = {
        "webhook_url": body.get("webhook_url", ""),
        "description": body.get("description", ""),
    }
    N8N_REGISTRY_FILE.write_text(json.dumps(registry, indent=2))
    return {"ok": True, "name": name}


@router.get("/specialists")
def list_specialists():
    if not SPECIALISTS_FILE.exists():
        return {"specialists": []}
    return {"specialists": json.loads(SPECIALISTS_FILE.read_text())}

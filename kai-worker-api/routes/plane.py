import logging
import urllib.request as ur
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)
router = APIRouter()

PLANE_BASE = "http://172.17.0.1:8090/api/v1/workspaces/sonicink"
KAI_PROJECT_ID = "78c49227-82d4-477d-a920-66b08cb91c56"

def _plane_token():
    p = Path("/home/leo/kai-system/secrets/plane_api_token.txt")
    return p.read_text().strip().split("\n")[0] if p.exists() else ""

def _req(path):
    token = _plane_token()
    if not token:
        raise HTTPException(status_code=500, detail="Plane API token not configured")
    r = ur.Request(f"{PLANE_BASE}/{path}", headers={"X-API-Key": token})
    with ur.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())

@router.get("/plane/issues")
def get_plane_issues():
    try:
        projects = _req("projects/")
        results = projects.get("results", projects) if isinstance(projects, dict) else projects
        out = []
        for p in results:
            state_map_raw = _req(f"projects/{p['id']}/states/")
            states = state_map_raw.get("results", state_map_raw) if isinstance(state_map_raw, dict) else state_map_raw
            state_map = {s["id"]: s for s in states}
            issues_raw = _req(f"projects/{p['id']}/issues/?per_page=100")
            issues = issues_raw.get("results", issues_raw) if isinstance(issues_raw, dict) else issues_raw
            open_issues = []
            for i in issues:
                s = state_map.get(i.get("state", ""), {})
                if s.get("group") not in ("completed", "cancelled"):
                    open_issues.append({
                        "id": i["id"],
                        "name": i["name"],
                        "state": s.get("name", "?"),
                        "state_group": s.get("group", "?"),
                        "priority": i.get("priority", "none"),
                        "created_at": i.get("created_at", ""),
                    })
            if open_issues:
                out.append({
                    "id": p["id"],
                    "name": p["name"],
                    "identifier": p["identifier"],
                    "issues": open_issues,
                })
        return {"projects": out}
    except Exception as e:
        logger.error(f"Plane issues error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class NewIssue(BaseModel):
    name: str
    description: str = ""
    priority: str = "medium"
    project_id: Optional[str] = None

@router.post("/plane/issues")
def create_plane_issue(body: NewIssue):
    try:
        token = _plane_token()
        if not token:
            raise HTTPException(status_code=500, detail="Plane API token not configured")
        pid = body.project_id or KAI_PROJECT_ID
        state_map_raw = _req(f"projects/{pid}/states/")
        states = state_map_raw.get("results", state_map_raw) if isinstance(state_map_raw, dict) else state_map_raw
        backlog_id = next((s["id"] for s in states if s.get("name","").lower() == "backlog"), states[0]["id"])
        payload = json.dumps({
            "name": body.name,
            "state": backlog_id,
            "priority": body.priority,
            "description_html": f"<p>{body.description}</p>" if body.description else "",
        }).encode()
        url = f"{PLANE_BASE}/projects/{pid}/issues/"
        req = ur.Request(url, data=payload, headers={"X-API-Key": token, "Content-Type": "application/json"}, method="POST")
        with ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        return {"id": result.get("id"), "name": result.get("name")}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Plane create issue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

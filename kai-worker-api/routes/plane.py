import logging
import urllib.request as ur
import json
import html
import re
from fastapi import APIRouter, HTTPException, Query
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


def _description_text(issue: dict) -> str:
    stripped = issue.get("description_stripped", "")
    if stripped:
        return stripped
    raw_html = issue.get("description_html", "")
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw_html))).strip()

PARKED_LABEL = "parked-post-gate"

def _req_paged(path):
    """Fetch ALL results for a Plane list endpoint by following the cursor.

    BUG-22 fix: a single per_page=100 call silently truncated projects with
    >100 issues (KAI holds 700+), so this route reported a capped slice as the
    full board. Loops until Plane signals no more pages.
    """
    out, cursor, guard = [], None, 0
    sep = "&" if "?" in path else "?"
    while True:
        d = _req(path + sep + "per_page=100" + (f"&cursor={cursor}" if cursor else ""))
        if isinstance(d, dict) and "results" in d:
            out += d["results"]
            if d.get("next_page_results") and d.get("next_cursor"):
                cursor = d["next_cursor"]; guard += 1
                if guard > 50:
                    logger.warning(f"_req_paged({path}) hit 50-page guard — possible truncation")
                    break
                continue
            break
        out += d if isinstance(d, list) else []
        break
    return out

def _parked_label_ids(pid):
    raw = _req(f"projects/{pid}/labels/?per_page=100")
    labels = raw.get("results", raw) if isinstance(raw, dict) else raw
    return {l["id"] for l in labels if l.get("name") == PARKED_LABEL}

@router.get("/plane/issues")
def get_plane_issues(
    include_done: bool = Query(False, description="Include completed/cancelled issues"),
    project_id: Optional[str] = Query(None, description="Filter to specific project UUID"),
    include_parked: bool = Query(False, description="Include issues labeled parked-post-gate (default: exclude — the working board)"),
):
    """Return Plane issues across all projects.

    NOTE: Plane's server-side state filter param is broken (silently ignored).
    State filtering is done client-side using the states list for each project.
    By default, only open issues (not completed/cancelled) are returned.
    Set include_done=true to include done/cancelled issues as well.

    Recovery Plan (2026-07-11) step 3: issues labeled `parked-post-gate` are open
    but OUT of the working view. They are excluded by default so this route (and
    the NEXT-UP derivation that reads it) shows the ~12 ON-PATH working board,
    not the raw 200+. Set include_parked=true to see them. `parked_excluded`
    reports how many were hidden per project so the shrink is honest, not silent.
    """
    try:
        projects_raw = _req("projects/")
        all_projects = projects_raw.get("results", projects_raw) if isinstance(projects_raw, dict) else projects_raw
        if project_id:
            all_projects = [p for p in all_projects if p["id"] == project_id]
        out = []
        for p in all_projects:
            # States — per_page=50 ensures we get all states (Plane projects rarely exceed 10)
            state_map_raw = _req(f"projects/{p['id']}/states/?per_page=50")
            states = state_map_raw.get("results", state_map_raw) if isinstance(state_map_raw, dict) else state_map_raw
            state_map = {s["id"]: s for s in states}
            parked_label_ids = set() if include_parked else _parked_label_ids(p["id"])
            # Issues — server-side state filter is broken; fetch ALL (paginated) and filter client-side
            issues = _req_paged(f"projects/{p['id']}/issues/")
            matched = []
            parked_excluded = 0
            for i in issues:
                s = state_map.get(i.get("state", ""), {})
                is_closed = s.get("group") in ("completed", "cancelled")
                if is_closed and not include_done:
                    continue
                if parked_label_ids:
                    ilabels = {(l if isinstance(l, str) else l.get("id")) for l in (i.get("labels") or [])}
                    if parked_label_ids & ilabels:
                        parked_excluded += 1
                        continue
                matched.append({
                    "id": i["id"],
                    "name": i["name"],
                    "state": s.get("name", "?"),
                    "state_group": s.get("group", "?"),
                    "priority": i.get("priority", "none"),
                    "created_at": i.get("created_at", ""),
                })
            if matched or parked_excluded:
                out.append({
                    "id": p["id"],
                    "name": p["name"],
                    "identifier": p["identifier"],
                    "issues": matched,
                    "parked_excluded": parked_excluded,
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



@router.get("/plane/issues/{issue_id}")
def get_plane_issue(issue_id: str, project_id: Optional[str] = Query(None)):
    """Get a single Plane issue with state resolved to name (not raw UUID)."""
    try:
        pid = project_id or KAI_PROJECT_ID
        state_map_raw = _req(f"projects/{pid}/states/?per_page=50")
        states = state_map_raw.get("results", state_map_raw) if isinstance(state_map_raw, dict) else state_map_raw
        state_map = {s["id"]: s for s in states}
        i = _req(f"projects/{pid}/issues/{issue_id}/")
        s = state_map.get(i.get("state", ""), {})
        return {
            "id": i["id"],
            "name": i["name"],
            "state": s.get("name", "?"),
            "state_group": s.get("group", "?"),
            "state_id": i.get("state", ""),
            "priority": i.get("priority", "none"),
            "sequence_id": i.get("sequence_id"),
            "description": _description_text(i),
            "description_html": i.get("description_html", ""),
            "created_at": i.get("created_at", ""),
            "updated_at": i.get("updated_at", ""),
        }
    except Exception as e:
        logger.error(f"Plane get issue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class IssueUpdate(BaseModel):
    state: Optional[str] = None
    state_group: Optional[str] = None
    priority: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[str] = None


@router.patch("/plane/issues/{issue_id}")
def patch_plane_issue(issue_id: str, body: IssueUpdate):
    """Update an existing Plane issue.

    Accepts:
      - state: state name (case-insensitive, e.g. "Done", "Cancelled", "In Progress")
      - state_group: state group (e.g. "completed", "cancelled") — picks the first
        matching state in that group if state name isn't specified.
      - priority: urgent | high | medium | low | none
      - name, description: passed through

    Returns updated issue summary.
    """
    try:
        token = _plane_token()
        if not token:
            raise HTTPException(status_code=500, detail="Plane API token not configured")
        pid = body.project_id or KAI_PROJECT_ID

        payload = {}
        if body.name is not None:
            payload["name"] = body.name
        if body.priority is not None:
            payload["priority"] = body.priority
        if body.description is not None:
            payload["description_html"] = f"<p>{body.description}</p>" if body.description else ""

        if body.state is not None or body.state_group is not None:
            state_map_raw = _req(f"projects/{pid}/states/")
            states = state_map_raw.get("results", state_map_raw) if isinstance(state_map_raw, dict) else state_map_raw
            state_id = None
            if body.state is not None:
                want = body.state.lower()
                state_id = next((s["id"] for s in states if s.get("name", "").lower() == want), None)
            if state_id is None and body.state_group is not None:
                want = body.state_group.lower()
                state_id = next((s["id"] for s in states if s.get("group", "").lower() == want), None)
            if state_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"state '{body.state}' / group '{body.state_group}' not found in project",
                )
            payload["state"] = state_id

        if not payload:
            raise HTTPException(status_code=400, detail="no updatable fields provided")

        # Ensure we have the state map to resolve UUIDs in the response
        if not (body.state is not None or body.state_group is not None):
            state_map_raw = _req(f"projects/{pid}/states/?per_page=50")
            states = state_map_raw.get("results", state_map_raw) if isinstance(state_map_raw, dict) else state_map_raw
        state_uuid_to_name = {s["id"]: s["name"] for s in states}

        url = f"{PLANE_BASE}/projects/{pid}/issues/{issue_id}/"
        req = ur.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"X-API-Key": token, "Content-Type": "application/json"},
            method="PATCH",
        )
        with ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        raw_state_uuid = result.get("state", "")
        return {
            "id": result.get("id"),
            "name": result.get("name"),
            "state": state_uuid_to_name.get(raw_state_uuid, raw_state_uuid),
            "state_id": raw_state_uuid,
            "priority": result.get("priority"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Plane patch issue error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



class BulkIssueUpdate(BaseModel):
    ids: list[str]
    state: Optional[str] = None
    state_group: Optional[str] = None
    priority: Optional[str] = None
    project_id: Optional[str] = None


@router.post("/plane/issues/bulk-update")
def bulk_update_plane_issues(body: BulkIssueUpdate):
    """Apply the same state/priority change to a list of issues.

    Returns per-id result: {id, ok, error?}. Continues on per-issue failure.
    Useful for sprint closeout (close N sub-tasks + parent in one call).
    """
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids list is empty")
    if body.state is None and body.state_group is None and body.priority is None:
        raise HTTPException(status_code=400, detail="no updatable fields provided")

    try:
        token = _plane_token()
        if not token:
            raise HTTPException(status_code=500, detail="Plane API token not configured")
        pid = body.project_id or KAI_PROJECT_ID

        # Resolve state once.
        state_id = None
        if body.state is not None or body.state_group is not None:
            state_map_raw = _req(f"projects/{pid}/states/")
            states = state_map_raw.get("results", state_map_raw) if isinstance(state_map_raw, dict) else state_map_raw
            if body.state is not None:
                want = body.state.lower()
                state_id = next((s["id"] for s in states if s.get("name", "").lower() == want), None)
            if state_id is None and body.state_group is not None:
                want = body.state_group.lower()
                state_id = next((s["id"] for s in states if s.get("group", "").lower() == want), None)
            if state_id is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"state '{body.state}' / group '{body.state_group}' not found in project",
                )

        payload_base = {}
        if state_id is not None:
            payload_base["state"] = state_id
        if body.priority is not None:
            payload_base["priority"] = body.priority

        results = []
        for issue_id in body.ids:
            try:
                url = f"{PLANE_BASE}/projects/{pid}/issues/{issue_id}/"
                req = ur.Request(
                    url,
                    data=json.dumps(payload_base).encode(),
                    headers={"X-API-Key": token, "Content-Type": "application/json"},
                    method="PATCH",
                )
                with ur.urlopen(req, timeout=10) as resp:
                    res = json.loads(resp.read())
                results.append({"id": issue_id, "ok": True, "name": res.get("name", "")[:80]})
            except Exception as e:
                results.append({"id": issue_id, "ok": False, "error": f"{type(e).__name__}: {e}"})

        ok_count = sum(1 for r in results if r["ok"])
        return {"ok_count": ok_count, "total": len(body.ids), "results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Plane bulk-update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

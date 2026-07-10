import json
import logging
import re
from datetime import datetime as _cdt
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body
from routes._destructive_audit import DestructiveRequest, audit_before
from pydantic import BaseModel
from config import VAULT_PATH, safe_path
from safe_http import safe_json  # noqa: F401

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

logger = logging.getLogger(__name__)
router = APIRouter()

PROJECTS_FILE = VAULT_PATH / "00_System" / "projects.json"
PROJECTS_DIR  = VAULT_PATH / "20_Projects"
TEMPLATES_PATH = VAULT_PATH / "00_System" / "templates"
N8N_REGISTRY_FILE = VAULT_PATH / "00_System" / "n8n_workflows.json"


def _parse_status_md(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    try:
        if _yaml:
            return _yaml.safe_load(m.group(1)) or {}
        return {}
    except Exception as e:
        logger.exception("parse status md: %s", e)
        return {}


def _render_template(content: str, variables: dict) -> str:
    for key, value in variables.items():
        content = content.replace("{{" + key + "}}", str(value))
    return content


def _n8n_draft_email(to: str, subject: str, body: str) -> dict:
    if not N8N_REGISTRY_FILE.exists():
        return {"error": "n8n registry not found"}
    try:
        registry = json.loads(N8N_REGISTRY_FILE.read_text())
    except Exception:
        return {"error": "n8n registry unreadable"}
    entry = registry.get("gmail-draft")
    if not entry:
        return {"error": "gmail-draft workflow not registered"}
    webhook_url = entry["webhook_url"] if isinstance(entry, dict) else entry
    try:
        import httpx as _n8nhx
        r = _n8nhx.post(webhook_url, json={"to": to, "subject": subject, "body": body}, timeout=30)
        return {"ok": r.status_code == 200, "status": r.status_code}
    except Exception as e:
        return {"error": str(e)}

@router.get("/projects")
def get_projects_v2():
    base = []
    if PROJECTS_FILE.exists():
        base = json.loads(PROJECTS_FILE.read_text())

    result = []
    for project in base:
        if not project.get("active", True):
            continue
        pid = project["id"]
        status_data = {}
        for folder_name in [pid, pid.capitalize(), pid.upper()]:
            status_path = PROJECTS_DIR / folder_name / "STATUS.md"
            if status_path.exists():
                status_data = _parse_status_md(status_path)
                break

        entry = {
            "id":            project["id"],
            "name":          project["name"],
            "description":   project.get("description", ""),
            "advisor":       project.get("advisor", "kai"),
            "url":           project.get("url", ""),
            "status":        status_data.get("status",       project.get("status", "green")),
            "version":       status_data.get("version",      None),
            "milestone":     status_data.get("milestone",    project.get("next", "")),
            "milestone_pct": status_data.get("milestone_pct", None),
            "updated":       str(status_data.get("updated", "")),
            "next":          status_data.get("next",         project.get("next", "")),
            "type":          status_data.get("type",         project.get("type", "active")),
            "pinned":        project.get("pinned", False),
        }
        result.append(entry)

    # Auto-scan vault/20_Projects/ideas/ for projects not in projects.json
    ideas_dir = PROJECTS_DIR / "ideas"
    existing_ids = {r["id"] for r in result}
    if ideas_dir.exists():
        for folder in sorted(ideas_dir.iterdir()):
            if not folder.is_dir():
                continue
            pid = folder.name.lower().replace(" ", "-").replace("_", "-")
            if pid in existing_ids:
                continue
            status_data = {}
            status_path = folder / "STATUS.md"
            if status_path.exists():
                status_data = _parse_status_md(status_path)
            result.append({
                "id":            pid,
                "name":          status_data.get("name", folder.name),
                "description":   status_data.get("description", ""),
                "advisor":       status_data.get("advisor", "kai"),
                "url":           status_data.get("url", ""),
                "status":        status_data.get("status", "yellow"),
                "version":       status_data.get("version", None),
                "milestone":     status_data.get("milestone", ""),
                "milestone_pct": status_data.get("milestone_pct", None),
                "updated":       str(status_data.get("updated", "")),
                "next":          status_data.get("next", ""),
                "type":          status_data.get("type", "idea"),
                "pinned":        status_data.get("pinned", False),
            })

    return {"projects": result}


class ProjectPatch(BaseModel):
    pinned: bool = None
    status: str = None
    next: str = None
    milestone: str = None
    milestone_pct: int = None


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch):
    if not PROJECTS_FILE.exists():
        raise HTTPException(404, "projects file not found")
    projects = json.loads(PROJECTS_FILE.read_text())
    for p in projects:
        if p["id"] == project_id:
            if body.pinned is not None: p["pinned"] = body.pinned  # noqa: E701
            if body.status:             p["status"]  = body.status  # noqa: E701
            if body.next:               p["next"]    = body.next  # noqa: E701
            if body.milestone:          p["milestone"] = body.milestone  # noqa: E701
            if body.milestone_pct is not None: p["milestone_pct"] = body.milestone_pct  # noqa: E701
            PROJECTS_FILE.write_text(json.dumps(projects, indent=2))
            return {"ok": True, "project": p}
    raise HTTPException(404, "project not found")


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, body: DestructiveRequest = Body(...)):
    if not PROJECTS_FILE.exists():
        raise HTTPException(404, "projects file not found")
    projects = json.loads(PROJECTS_FILE.read_text())
    remaining = [p for p in projects if p["id"] != project_id]
    if len(remaining) == len(projects):
        raise HTTPException(404, "project not found")
    audit_before("/projects/{project_id}", {"project_id": project_id}, body.operator, body.reason)
    PROJECTS_FILE.write_text(json.dumps(remaining, indent=2))
    return {"ok": True}


class ProjectCreate(BaseModel):
    id: str
    name: str
    status: str = "green"
    next: str = ""
    description: str = ""
    url: str = ""
    advisor: str = "kai"
    active: bool = True


@router.post("/projects")
def create_project(body: ProjectCreate):
    projects = json.loads(PROJECTS_FILE.read_text()) if PROJECTS_FILE.exists() else []
    if any(p["id"] == body.id for p in projects):
        raise HTTPException(400, f"project '{body.id}' already exists")
    p = body.dict()
    p.setdefault("pinned", False)
    projects.append(p)
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2))
    return {"ok": True, "project": p}


@router.get("/templates")
def list_templates():
    if not TEMPLATES_PATH.exists():
        return {"versions": []}
    versions = []
    for vdir in sorted(TEMPLATES_PATH.iterdir()):
        if vdir.is_dir():
            manifest_file = vdir / "manifest.json"
            manifest = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
            versions.append({
                "version": vdir.name,
                "description": manifest.get("description", ""),
                "files": [f.name for f in vdir.iterdir() if f.suffix == ".md"],
                "manifest": manifest,
            })
    return {"versions": versions, "latest": versions[-1]["version"] if versions else None}


@router.get("/templates/{version}/{filename}")
def get_template(version: str, filename: str):
    if ".." in version or ".." in filename:
        raise HTTPException(400, "Invalid path")
    tpl_file = TEMPLATES_PATH / version / filename
    if not tpl_file.exists():
        raise HTTPException(404, f"Template {version}/{filename} not found")
    return {"version": version, "filename": filename, "content": tpl_file.read_text()}


@router.post("/templates/{version}")
def create_template_version(version: str, body: dict):
    vdir = TEMPLATES_PATH / version
    vdir.mkdir(parents=True, exist_ok=True)
    filename = body.get("filename")
    content = body.get("content", "")
    if not filename:
        raise HTTPException(400, "filename required")
    (vdir / filename).write_text(content)
    return {"ok": True, "version": version, "filename": filename}


class ProjectSetupRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    advisor: str = "kai"
    status: str = "yellow"
    next: str = ""
    template_version: str = "v1"
    project_type: str = "active"  # "active" | "idea"
    external_invites: list = []
    file_request_message: str = ""
    url: str = ""
    plane_project: str = ""


@router.post("/projects/setup")
def setup_project(req: ProjectSetupRequest):
    results = {"project_id": req.id, "steps": [], "errors": []}
    today = _cdt.now().strftime("%Y-%m-%d")

    is_idea = req.project_type == "idea"

    if not is_idea:
        try:
            proj_file = VAULT_PATH / "00_System" / "projects.json"
            projects = json.loads(proj_file.read_text()) if proj_file.exists() else []
            existing = next((p for p in projects if p["id"] == req.id), None)
            if existing:
                results["steps"].append({"step": "projects.json", "status": "skipped", "note": "Already exists"})
            else:
                projects.append({
                    "id": req.id, "name": req.name, "status": req.status,
                    "next": req.next or "Define scope and goals",
                    "description": req.description, "url": req.url,
                    "advisor": req.advisor, "active": True,
                })
                proj_file.write_text(json.dumps(projects, indent=2))
                results["steps"].append({"step": "projects.json", "status": "done"})
        except Exception as e:
            logger.exception("setup_project projects.json: %s", e)
            results["errors"].append(f"projects.json: {e}")
    else:
        results["steps"].append({"step": "projects.json", "status": "skipped", "note": "Idea projects are auto-discovered — no registry entry needed"})

    template_vars = {
        "PROJECT_NAME": req.name, "PROJECT_ID": req.id, "DATE": today,
        "ADVISOR": req.advisor,
    }
    _id_safe = safe_path(VAULT_PATH / "20_Projects", ("ideas/" + req.id) if is_idea else req.id)
    if _id_safe is None:
        raise HTTPException(400, "Invalid project id")
    proj_dir = _id_safe
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Write canonical STATUS.md (always, overwrite if format is wrong)
    status_md = proj_dir / "STATUS.md"
    if not status_md.exists():
        status_content = f"""---
name: {req.name}
status: {req.status}
type: {"idea" if is_idea else "active"}
version: 0.1.0
milestone: Phase 1 - Kickoff
milestone_pct: 0
updated: {today}
next: {req.next or 'Define scope and goals'}
pinned: false
---
"""
        status_md.write_text(status_content)
        results["steps"].append({"step": "STATUS.md", "status": "done", "path": str(status_md)})

    # Warn if no Plane project code provided
    if not req.plane_project:
        results["steps"].append({"step": "plane_project", "status": "warning", "note": "No plane_project code provided — create a Plane project and update STATUS.md"})

    tpl_dir = TEMPLATES_PATH / req.template_version
    files_created = []
    if tpl_dir.exists():
        for tpl_file in tpl_dir.glob("*.md"):
            dest = proj_dir / tpl_file.name
            if not dest.exists():
                content = _render_template(tpl_file.read_text(), template_vars)
                dest.write_text(content)
                files_created.append(tpl_file.name)
    results["steps"].append({
        "step": "vault_files", "status": "done",
        "path": str(proj_dir), "files": files_created,
    })

    # Gmail draft invitations for external (non-Slack) collaborators
    if req.external_invites:
        gmail_sent = []
        gmail_errors = []
        for invite in req.external_invites:
            email = invite if isinstance(invite, str) else invite.get("email")
            name = "" if isinstance(invite, str) else invite.get("name", "")
            if not email:
                continue
            greeting = f"Hi {name}," if name else "Hi,"
            file_note = f"\n\n{req.file_request_message}" if req.file_request_message else ""
            body_text = (
                f"{greeting}\n\nYou've been invited to collaborate on *{req.name}*."
                f"\n\nLeo will follow up with details on coordination and file sharing.{file_note}"
                f"\n\n— KAI on behalf of Leo"
            )
            draft_result = _n8n_draft_email(
                to=email,
                subject=f"Invitation: {req.name} project",
                body=body_text,
            )
            if draft_result.get("ok"):
                gmail_sent.append(email)
            else:
                gmail_errors.append({"email": email, "error": draft_result.get("error", "unknown")})
        if gmail_sent:
            results["steps"].append({"step": "gmail_drafts", "status": "done", "sent_to": gmail_sent})
        if gmail_errors:
            results["errors"].extend([f"gmail_draft({e['email']}): {e['error']}" for e in gmail_errors])

    results["ok"] = len(results["errors"]) == 0
    return results


@router.post("/projects/{project_id}/teardown")
def teardown_project(project_id: str):
    """Full project removal: projects.json + vault folder archive."""
    if ".." in project_id or not project_id.strip():
        raise HTTPException(400, "Invalid project_id")
    results = {"project_id": project_id, "steps": [], "errors": []}

    # 1. Remove from projects.json
    try:
        proj_file = VAULT_PATH / "00_System" / "projects.json"
        if proj_file.exists():
            projects = json.loads(proj_file.read_text())
            remaining = [p for p in projects if p["id"] != project_id]
            if len(remaining) < len(projects):
                proj_file.write_text(json.dumps(remaining, indent=2))
                results["steps"].append({"step": "projects.json", "status": "done", "note": "Removed"})
            else:
                results["steps"].append({"step": "projects.json", "status": "skipped", "note": "Not found in registry"})
    except Exception as e:
        logger.exception("teardown projects.json: %s", e)
        results["errors"].append(f"projects.json: {e}")

    # 2. Move vault folder to archived/
    import shutil as _shutil
    for candidate_dir in [
        PROJECTS_DIR / project_id,
        PROJECTS_DIR / "ideas" / project_id,
    ]:
        if candidate_dir.exists():
            archived_dir = PROJECTS_DIR / "archived"
            archived_dir.mkdir(parents=True, exist_ok=True)
            dest = archived_dir / project_id
            try:
                _shutil.move(str(candidate_dir), str(dest))
                results["steps"].append({"step": "vault_folder", "status": "done", "note": f"Moved to archived/{project_id}"})
            except Exception as e:
                logger.exception("teardown vault move: %s", e)
                results["errors"].append(f"vault_folder: {e}")
            break
    else:
        results["steps"].append({"step": "vault_folder", "status": "skipped", "note": "Folder not found"})

    results["ok"] = len(results["errors"]) == 0
    return results

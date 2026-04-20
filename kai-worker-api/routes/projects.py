import json
import logging
import re
from datetime import datetime as _cdt
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

try:
    import yaml as _yaml
except ImportError:
    _yaml = None

logger = logging.getLogger(__name__)
router = APIRouter()

PROJECTS_FILE = VAULT_PATH / "00_System" / "projects.json"
PROJECTS_DIR  = VAULT_PATH / "20_Projects"
TEMPLATES_PATH = VAULT_PATH / "00_System" / "templates"


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


def _slack_api(method: str, payload: dict) -> dict:
    import httpx as _slhx
    from pathlib import Path as _slp
    import os
    p = _slp("/run/secrets/slack_bot_token")
    token = p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")
    r = _slhx.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    return r.json()


def _slack_get(method: str, params: dict) -> dict:
    import httpx as _slhx
    from pathlib import Path as _slp
    import os
    p = _slp("/run/secrets/slack_bot_token")
    token = p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")
    r = _slhx.get(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )
    return r.json()


def _contacts_load() -> list:
    contacts_file = VAULT_PATH / "00_System" / "contacts.json"
    if contacts_file.exists():
        return json.loads(contacts_file.read_text())
    return []


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
        }
        result.append(entry)

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
            if body.pinned is not None: p["pinned"] = body.pinned
            if body.status:             p["status"]  = body.status
            if body.next:               p["next"]    = body.next
            if body.milestone:          p["milestone"] = body.milestone
            if body.milestone_pct is not None: p["milestone_pct"] = body.milestone_pct
            PROJECTS_FILE.write_text(json.dumps(projects, indent=2))
            return {"ok": True, "project": p}
    raise HTTPException(404, "project not found")


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
    create_slack_channel: bool = True
    slack_channel_name: str = ""
    invite_contacts: list = []
    url: str = ""


@router.post("/projects/setup")
def setup_project(req: ProjectSetupRequest):
    results = {"project_id": req.id, "steps": [], "errors": []}
    today = _cdt.now().strftime("%Y-%m-%d")

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

    slack_channel = req.slack_channel_name or req.id
    template_vars = {
        "PROJECT_NAME": req.name, "PROJECT_ID": req.id, "DATE": today,
        "ADVISOR": req.advisor, "SLACK_CHANNEL": slack_channel,
    }
    proj_dir = VAULT_PATH / "20_Projects" / req.id
    proj_dir.mkdir(parents=True, exist_ok=True)

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

    channel_id = None
    if req.create_slack_channel:
        try:
            slack_result = _slack_api("conversations.create", {"name": slack_channel, "is_private": False})
            if slack_result.get("ok"):
                channel_id = slack_result["channel"]["id"]
                results["steps"].append({"step": "slack_channel", "status": "done",
                                          "channel": f"#{slack_channel}", "channel_id": channel_id})
                _slack_api("chat.postMessage", {
                    "channel": channel_id,
                    "text": f"*{req.name}* project channel is live.\n*Advisor:* {req.advisor.upper()} | *Description:* {req.description or 'TBD'}\n\nI'll be tracking updates here.",
                    "username": "KAI",
                    "icon_url": "https://kai.sonicink.space/icon-192.png",
                })
            else:
                err = slack_result.get("error", "unknown")
                if err == "name_taken":
                    results["steps"].append({"step": "slack_channel", "status": "skipped",
                                              "note": f"#{slack_channel} already exists"})
                    ch_list = _slack_get("conversations.list", {"types": "public_channel,private_channel", "limit": 200})
                    for ch in ch_list.get("channels", []):
                        if ch["name"] == slack_channel:
                            channel_id = ch["id"]
                            break
                else:
                    results["errors"].append(f"slack_channel: {err}")
        except Exception as e:
            logger.exception("setup_project slack: %s", e)
            results["errors"].append(f"slack_channel: {e}")

    if req.invite_contacts and channel_id:
        contacts = _contacts_load()
        pending_invites = []
        for contact_ref in req.invite_contacts:
            match = None
            for c in contacts:
                if (contact_ref == c["id"] or
                    contact_ref.lower() in [a.lower() for a in c.get("aliases", [])] or
                    contact_ref.lower() in c.get("name", "").lower() or
                    contact_ref == c.get("email")):
                    match = c
                    break
            if match:
                pending_invites.append({"name": match["name"], "email": match.get("email"), "slack_id": match.get("slack_id")})
            else:
                pending_invites.append({"name": contact_ref, "email": contact_ref if "@" in contact_ref else None})

        if pending_invites:
            names = ", ".join(p["name"] for p in pending_invites)
            try:
                import httpx as _t2hx
                _t2hx.post(
                    "http://localhost:8001/t2/queue",
                    json={
                        "action": f"Invite {names} to #{slack_channel}",
                        "detail": f"Project: {req.name} | Channel: #{slack_channel} | People: {names}",
                        "advisor": req.advisor, "slack_channel": "kai",
                    },
                    timeout=5,
                )
                results["steps"].append({
                    "step": "t2_invites", "status": "queued", "people": names,
                    "note": "React on the Slack approval message to send invites",
                })
            except Exception as e:
                logger.exception("setup_project t2_queue: %s", e)
                results["errors"].append(f"t2_queue: {e}")

    results["ok"] = len(results["errors"]) == 0
    return results

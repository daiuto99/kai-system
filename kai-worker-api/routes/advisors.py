import json
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

COUNCIL_DIR  = VAULT_PATH / "60_Council"
SYSTEM_DIR   = VAULT_PATH / "00_System"
SPECIALISTS_FILE = SYSTEM_DIR / "specialists.json"

ADVISOR_META = {
    "kai":      {"name": "KAI",      "role": "Chief of Staff",          "color": "#6366f1", "local": False, "status": "active",      "avatar": "/avatar-kai.png"},
    "beats":    {"name": "Beats",    "role": "Music Director & Mentor", "color": "#f59e0b", "local": False, "status": "active",      "avatar": "/avatar-beats.png"},
    "sky":      {"name": "Sky",      "role": "Studio Operations",       "color": "#06b6d4", "local": False, "status": "active",      "avatar": "/avatar-sky.png"},
    "roads":    {"name": "Roads",    "role": "Gear & Production",       "color": "#f59e0b", "local": False, "status": "active",      "avatar": "/avatar-roads.png"},
    "coach":    {"name": "Coach",    "role": "Performance & Fitness",   "color": "#f97316", "local": False, "status": "active",      "avatar": "/avatar-coach.png"},
    "doc":      {"name": "Doc",      "role": "Health & Longevity",      "color": "#10b981", "local": True,  "status": "active",      "avatar": "/avatar-doc.png"},
    "ember":    {"name": "Ember",    "role": "Emotional & Personal",    "color": "#ec4899", "local": True,  "status": "active",      "avatar": "/avatar-ember.png"},
    "creative": {"name": "Creative", "role": "Creative Director",       "color": "#a855f7", "local": False, "status": "spec_needed", "avatar": "/avatar-creative.png"},
    "dev":      {"name": "Dev",      "role": "Engineering Director",    "color": "#3b82f6", "local": False, "status": "spec_needed", "avatar": "/avatar-dev.png"},
    "devops":   {"name": "DevOps",   "role": "Infrastructure & Ops",    "color": "#64748b", "local": False, "status": "spec_needed", "avatar": "/avatar-devops.png"},
}

COUNCIL_ORDER = ["kai", "beats", "sky", "roads", "coach", "doc", "ember", "creative", "dev", "devops"]


def _safe_path(name: str, filename: str) -> Path:
    safe = (COUNCIL_DIR / name / filename).resolve()
    if not safe.is_relative_to(COUNCIL_DIR.resolve()):
        raise HTTPException(403, "Invalid path")
    return safe


def _load_assets(name: str) -> dict:
    assets_file = COUNCIL_DIR / name / "assets.json"
    if assets_file.exists():
        try:
            return json.loads(assets_file.read_text())
        except Exception:
            pass
    meta = ADVISOR_META.get(name, {})
    return {
        "heygen_id": "",
        "elevenlabs_id": "",
        "sidekick_enabled": False,
        "status": meta.get("status", "active"),
        "default_model": "claude-sonnet-4-6",
        "research_model": "claude-opus-4-7",
    }


def _load_specialists() -> list:
    if SPECIALISTS_FILE.exists():
        try:
            return json.loads(SPECIALISTS_FILE.read_text())
        except Exception:
            pass
    return []


@router.get("/advisors")
def list_advisors():
    advisors = []
    for advisor_id in COUNCIL_ORDER:
        meta = ADVISOR_META.get(advisor_id, {})
        assets = _load_assets(advisor_id)
        persona_file = COUNCIL_DIR / advisor_id / f"{advisor_id.upper()}.md"
        advisors.append({
            "id":              advisor_id,
            "name":            meta.get("name", advisor_id.title()),
            "role":            meta.get("role", ""),
            "color":           meta.get("color", "#6366f1"),
            "local":           meta.get("local", False),
            "status":          assets.get("status", meta.get("status", "active")),
            "sidekick_enabled":assets.get("sidekick_enabled", False),
            "avatar":          meta.get("avatar"),
            "has_persona":     persona_file.exists(),
            "default_model":   assets.get("default_model", "claude-sonnet-4-6"),
        })
    return {"advisors": advisors}


@router.get("/advisors/{name}")
def get_advisor(name: str):
    persona_file = _safe_path(name, f"{name.upper()}.md")
    if not persona_file.exists():
        raise HTTPException(404, f"Advisor {name} not found")
    return {"name": name, "content": persona_file.read_text(encoding="utf-8")}


class AdvisorUpdateRequest(BaseModel):
    content: str


@router.put("/advisors/{name}")
def update_advisor(name: str, req: AdvisorUpdateRequest):
    persona_file = _safe_path(name, f"{name.upper()}.md")
    if not persona_file.exists():
        raise HTTPException(404, f"Advisor {name} not found")
    persona_file.write_text(req.content, encoding="utf-8")
    return {"ok": True, "name": name}


@router.get("/advisors/{name}/assets")
def get_assets(name: str):
    return {"name": name, "assets": _load_assets(name)}


@router.put("/advisors/{name}/assets")
def update_assets(name: str, body: dict):
    allowed = {"heygen_id", "elevenlabs_id", "sidekick_enabled", "status", "default_model", "research_model"}
    assets = _load_assets(name)
    for k, v in body.items():
        if k in allowed:
            assets[k] = v
    assets_file = COUNCIL_DIR / name / "assets.json"
    assets_file.parent.mkdir(parents=True, exist_ok=True)
    assets_file.write_text(json.dumps(assets, indent=2))
    return {"ok": True, "name": name, "assets": assets}


@router.get("/advisors/{name}/team")
def get_team(name: str):
    specialists = _load_specialists()
    team = [s for s in specialists if s.get("director") == name]
    result = []
    for s in team:
        spec_file = VAULT_PATH / s.get("file", "")
        result.append({
            "id":     s["id"],
            "name":   s["name"],
            "domain": s.get("domain", ""),
            "director": s.get("director", ""),
            "has_persona": spec_file.exists() if s.get("file") else False,
        })
    return {"director": name, "team": result}


@router.post("/advisors")
def create_advisor(req: AdvisorUpdateRequest, name: str = ""):
    if not name:
        raise HTTPException(400, "name required")
    advisor_dir = COUNCIL_DIR / name
    advisor_dir.mkdir(exist_ok=True)
    persona_file = _safe_path(name, f"{name.upper()}.md")
    persona_file.write_text(req.content, encoding="utf-8")
    return {"ok": True, "name": name, "created": True}

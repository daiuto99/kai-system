import json
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

COUNCIL_DIR = VAULT_PATH / "60_Council"
ORG_FILE    = VAULT_PATH / "00_System" / "org.json"


def _load_org() -> list:
    """Load org.json — the single source of truth for all members."""
    if not ORG_FILE.exists():
        return []
    return json.loads(ORG_FILE.read_text(encoding="utf-8"))["members"]


def _get_member(member_id: str) -> dict | None:
    return next((m for m in _load_org() if m["id"] == member_id), None)


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
    member = _get_member(name)
    return {
        "heygen_id": "",
        "elevenlabs_id": "",
        "sidekick_enabled": False,
        "status": member.get("status", "active") if member else "active",
        "default_model": member.get("default_model", "claude-sonnet-4-6") if member else "claude-sonnet-4-6",
        "research_model": member.get("director_model", "claude-opus-4-7") if member else "claude-opus-4-7",
    }


@router.get("/org")
def get_org():
    """Full org — all members with tier, reports_to, domain. Primary source for LangGraph routing."""
    members = _load_org()
    return {"version": "1.0.0", "members": members}


@router.get("/org/{tier}")
def get_org_by_tier(tier: str):
    """Members filtered by tier: orchestrator / director / advisor / specialist."""
    members = [m for m in _load_org() if m.get("tier") == tier]
    return {"tier": tier, "members": members}


@router.get("/advisors")
def list_advisors():
    """Council view — orchestrators, directors, and advisors (not specialists)."""
    council_tiers = {"orchestrator", "director", "advisor"}
    advisors = []
    for m in _load_org():
        if m.get("tier") not in council_tiers:
            continue
        assets = _load_assets(m["id"])
        persona_file = VAULT_PATH / m.get("persona_file", "")
        advisors.append({
            "id":               m["id"],
            "name":             m["name"],
            "role":             m.get("role", ""),
            "tier":             m.get("tier"),
            "reports_to":       m.get("reports_to"),
            "domain":           m.get("domain", ""),
            "color":            m.get("color", "#6366f1"),
            "local":            m.get("local", False),
            "status":           assets.get("status", m.get("status", "active")),
            "sidekick_enabled": assets.get("sidekick_enabled", False),
            "avatar":           m.get("avatar"),
            "has_persona":      persona_file.exists() if m.get("persona_file") else False,
            "default_model":    assets.get("default_model", m.get("default_model", "claude-sonnet-4-6")),
        })
    return {"advisors": advisors}


@router.get("/advisors/{name}")
def get_advisor(name: str):
    member = _get_member(name)
    if not member:
        raise HTTPException(404, f"Member {name} not found")
    persona_file = VAULT_PATH / member.get("persona_file", "")
    if not persona_file.exists():
        raise HTTPException(404, f"Persona file not found for {name}")
    return {"name": name, "content": persona_file.read_text(encoding="utf-8")}


class AdvisorUpdateRequest(BaseModel):
    content: str


@router.put("/advisors/{name}")
def update_advisor(name: str, req: AdvisorUpdateRequest):
    member = _get_member(name)
    if not member:
        raise HTTPException(404, f"Member {name} not found")
    persona_file = VAULT_PATH / member.get("persona_file", "")
    if not persona_file.exists():
        raise HTTPException(404, f"Persona file not found for {name}")
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
    """Specialists that report to this director."""
    team = [m for m in _load_org() if m.get("reports_to") == name and m.get("tier") == "specialist"]
    result = []
    for s in team:
        spec_file = VAULT_PATH / s.get("persona_file", "")
        result.append({
            "id":          s["id"],
            "name":        s["name"],
            "role":        s.get("role", ""),
            "domain":      s.get("domain", ""),
            "reports_to":  s.get("reports_to"),
            "default_model": s.get("default_model", "claude-sonnet-4-6"),
            "status":      s.get("status", "active"),
            "has_persona": spec_file.exists() if s.get("persona_file") else False,
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

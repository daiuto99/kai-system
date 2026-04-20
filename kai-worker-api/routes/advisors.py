import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

COUNCIL_DIR = VAULT_PATH / "60_Council"

ADVISOR_DISPLAY = {
    "chief": {"name": "KAI (Chief)", "description": "Primary advisor — executive chief of staff", "model": "claude-sonnet-4-6"},
    "ember": {"name": "Ember", "description": "Creative strategist — brand, content, soul", "model": "claude-sonnet-4-6"},
    "doc": {"name": "Doc", "description": "Health advisor — Oura, sleep, recovery, supplements", "model": "claude-sonnet-4-6"},
    "beats": {"name": "Beats", "description": "Music advisor — studio, gear, projects, artists", "model": "claude-sonnet-4-6"},
    "coach": {"name": "Coach", "description": "Performance and mindset coach", "model": "claude-sonnet-4-6"},
    "biz": {"name": "Biz", "description": "Business strategist — finance, ops, growth", "model": "claude-sonnet-4-6"},
    "sky": {"name": "Sky", "description": "Mindfulness and reflection", "model": "claude-sonnet-4-6"},
    "roads": {"name": "Roads", "description": "Travel and logistics", "model": "claude-sonnet-4-6"},
}


def _safe_advisor_path(name: str, filename: str) -> Path:
    safe = (COUNCIL_DIR / name / filename).resolve()
    if not safe.is_relative_to(COUNCIL_DIR.resolve()):
        raise HTTPException(403, "Invalid path")
    return safe


@router.get("/advisors")
def list_advisors():
    advisors = []
    if not COUNCIL_DIR.exists():
        return {"advisors": []}
    for d in sorted(COUNCIL_DIR.iterdir()):
        if not d.is_dir() or d.name.startswith("_") or d.name.startswith("."):
            continue
        persona_file = d / f"{d.name.upper()}.md"
        if persona_file.exists():
            display = ADVISOR_DISPLAY.get(d.name, {"name": d.name.title(), "description": "", "model": "claude-sonnet-4-6"})
            advisors.append({
                "id": d.name,
                "name": display["name"],
                "description": display["description"],
                "model": display["model"],
                "has_persona": True,
                "persona_file": str(persona_file.relative_to(VAULT_PATH)),
            })
    return {"advisors": advisors}


@router.get("/advisors/{name}")
def get_advisor(name: str):
    persona_file = _safe_advisor_path(name, f"{name.upper()}.md")
    if not persona_file.exists():
        raise HTTPException(404, f"Advisor {name} not found")
    return {"name": name, "content": persona_file.read_text(encoding="utf-8")}


class AdvisorUpdateRequest(BaseModel):
    content: str


@router.put("/advisors/{name}")
def update_advisor(name: str, req: AdvisorUpdateRequest):
    persona_file = _safe_advisor_path(name, f"{name.upper()}.md")
    if not persona_file.exists():
        raise HTTPException(404, f"Advisor {name} not found")
    persona_file.write_text(req.content, encoding="utf-8")
    return {"ok": True, "name": name, "message": f"Persona updated for {name}"}


@router.post("/advisors")
def create_advisor(req: AdvisorUpdateRequest, name: str = ""):
    if not name:
        raise HTTPException(400, "name query parameter required")
    advisor_dir = COUNCIL_DIR / name
    advisor_dir.mkdir(exist_ok=True)
    persona_file = _safe_advisor_path(name, f"{name.upper()}.md")
    persona_file.write_text(req.content, encoding="utf-8")
    return {"ok": True, "name": name, "created": True}

"""Harmony domain storage — reads/writes vault/00_System/harmony.json."""
import json
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/harmony", tags=["harmony"])

VAULT_PATH = Path("/vault")
HARMONY_FILE = VAULT_PATH / "00_System" / "harmony.json"

DOMAIN_META = [
    {"id": "health-fitness",    "name": "Health & Fitness",   "icon": "💪"},
    {"id": "intellectual-life", "name": "Intellectual Life",  "icon": "🧠"},
    {"id": "emotional-life",    "name": "Emotional Life",     "icon": "❤️"},
    {"id": "character",         "name": "Character",          "icon": "⭐"},
    {"id": "spiritual-life",    "name": "Spiritual Life",     "icon": "🕉️"},
    {"id": "love-relationship", "name": "Love Relationship",  "icon": "💑"},
    {"id": "parenting",         "name": "Parenting",          "icon": "👨‍👩‍👧‍👦"},
    {"id": "social-life",       "name": "Social Life",        "icon": "🤝"},
    {"id": "financial-life",    "name": "Financial Life",     "icon": "💰"},
    {"id": "career",            "name": "Career",             "icon": "💼"},
    {"id": "quality-of-life",   "name": "Quality of Life",   "icon": "✨"},
    {"id": "life-vision",       "name": "Life Vision",        "icon": "🎯"},
    {"id": "passion-sex",       "name": "Passion & Vitality", "icon": "🔥"},
]

DEFAULT_ASPECT = lambda: {"statements": [], "status": "green"}


def load_harmony() -> dict:
    if HARMONY_FILE.exists():
        return json.loads(HARMONY_FILE.read_text(encoding="utf-8"))
    # Bootstrap empty domains
    domains = []
    for d in DOMAIN_META:
        domains.append({
            **d,
            "aspects": {
                "premise":  DEFAULT_ASPECT(),
                "vision":   DEFAULT_ASPECT(),
                "purpose":  DEFAULT_ASPECT(),
                "strategy": DEFAULT_ASPECT(),
            },
            "lastUpdated": None,
        })
    data = {"domains": domains}
    _save(data)
    return data


def _save(data: dict):
    HARMONY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HARMONY_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class AspectUpdate(BaseModel):
    status: str


@router.get("")
def get_harmony():
    return load_harmony()


@router.put("/{domain_id}/aspect/{aspect}")
def update_aspect(domain_id: str, aspect: str, body: AspectUpdate):
    if body.status not in ("green", "yellow", "red"):
        raise HTTPException(400, "status must be green, yellow, or red")
    if aspect not in ("premise", "vision", "purpose", "strategy"):
        raise HTTPException(400, "unknown aspect")

    data = load_harmony()
    for domain in data["domains"]:
        if domain["id"] == domain_id:
            domain["aspects"][aspect]["status"] = body.status
            domain["lastUpdated"] = datetime.now(timezone.utc).isoformat()
            _save(data)
            return {"ok": True, "domain_id": domain_id, "aspect": aspect, "status": body.status}

    raise HTTPException(404, f"Domain {domain_id} not found")

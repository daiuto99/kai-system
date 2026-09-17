"""Project Console read-API (s5-console P1, KAI-1455).

Serves the DERIVED Business -> Project -> Deliverable + Idea store to the shipped
Work UI. Thin by design: reads vault/00_System/console_store.json (produced by
scripts/console_store.py in the sonicink repo) straight from the vault mount, so a
data change needs NO container rebuild. Additive to the flat /projects registry
(routes/projects.py) — the new Business and Deliverable layers live only here.

Design: docs/PROJECT_CONSOLE_DESIGN.md §2/§7/§8.
"""
import json
import logging
from fastapi import APIRouter, HTTPException
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

STORE_FILE = VAULT_PATH / "00_System" / "console_store.json"


def _load_store() -> dict:
    if not STORE_FILE.exists():
        raise HTTPException(
            503,
            "console store not built — run `python3 scripts/console_store.py --build` "
            "(writes vault/00_System/console_store.json)",
        )
    try:
        return json.loads(STORE_FILE.read_text())
    except Exception as e:
        logger.exception("console store read: %s", e)
        raise HTTPException(500, f"console store unreadable: {e}")


@router.get("/console/store")
def get_store():
    """The whole object model — businesses, projects, deliverables, ideas, counts."""
    return _load_store()


@router.get("/console/businesses")
def get_businesses():
    store = _load_store()
    return {"businesses": store.get("businesses", []), "count": len(store.get("businesses", []))}


@router.get("/console/projects")
def get_projects(business: str | None = None):
    """All projects, or only those under ?business=<id>."""
    projects = _load_store().get("projects", [])
    if business:
        projects = [p for p in projects if p.get("business_id") == business]
    return {"projects": projects, "count": len(projects)}


@router.get("/console/project/{project_id}")
def get_project(project_id: str):
    for p in _load_store().get("projects", []):
        if p.get("id") == project_id:
            # attach this project's deliverables inline for the open-context view
            delivs = [d for d in _load_store().get("deliverables", [])
                      if d.get("project_id") == project_id]
            return {**p, "deliverables": delivs}
    raise HTTPException(404, f"project '{project_id}' not found")


@router.get("/console/deliverables")
def get_deliverables(project: str | None = None):
    delivs = _load_store().get("deliverables", [])
    if project:
        delivs = [d for d in delivs if d.get("project_id") == project]
    return {"deliverables": delivs, "count": len(delivs)}


@router.get("/console/ideas")
def get_ideas():
    store = _load_store()
    return {"ideas": store.get("ideas", []), "count": len(store.get("ideas", []))}

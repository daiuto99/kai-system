"""Project Console read-API + promote (s5-console P1/P2, KAI-1455/1456).

Serves the DERIVED Business -> Project -> Deliverable + Idea store to the shipped
Work UI. Reads reads are thin: vault/00_System/console_store.json straight from the
vault mount, so a data-only change needs NO container rebuild.

P2 (KAI-1456) adds the write path:
  POST /console/promote  — turn an Idea into a Project (docset + registry + store).
  POST /console/rebuild  — manual re-derive of console_store.json.
The store rebuild uses the co-located runtime builder `console_store.py` (SSOT twin
of ~/sonicink/scripts/console_store.py), so a mutation is immediately reflected.

Design: docs/PROJECT_CONSOLE_DESIGN.md §2/§7/§8.
"""
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

import console_store  # co-located runtime builder (RUNTIME twin, KAI-1456)

logger = logging.getLogger(__name__)
router = APIRouter()

STORE_FILE = VAULT_PATH / "00_System" / "console_store.json"
SEED_FILE = VAULT_PATH / "00_System" / "console_businesses.json"
PROJECTS_JSON = VAULT_PATH / "00_System" / "projects.json"
PROJECTS_DIR = VAULT_PATH / "20_Projects"


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


# ---------------------------------------------------------------------------
# P2 (KAI-1456) — promote an Idea into a Project + manual store rebuild
# ---------------------------------------------------------------------------

class PromoteRequest(BaseModel):
    idea_slug: str
    business_id: str
    name: str | None = None
    project_id: str | None = None


def _resolve_idea_dir(idea: dict) -> Path | None:
    """Best-effort resolve an idea's source dir to a readable path in the container.

    `idea["dir"]` is authored home-relative (e.g. `vault/20_Projects/ideas/X` or a
    `sonicink/ideas/X`). The container only mounts /vault, so map any `vault/...`
    prefix onto VAULT_PATH; a sonicink/... path is unreadable here (return None)."""
    d = idea.get("dir")
    if not d:
        return None
    if d.startswith("vault/"):
        p = VAULT_PATH / d.split("vault/", 1)[1]
        return p if p.is_dir() else None
    # sonicink/... or anything else: not mounted in the container
    p = Path.home() / d
    return p if p.is_dir() else None


def _synthesize_overview(name: str, project_id: str, src_dir: Path | None,
                         carried: list[str], today: str) -> str:
    """Draft Overview.md seeded from the idea's source markdown (title + first
    heading/paragraphs), listing the carried source files."""
    excerpt = ""
    title_line = ""
    if src_dir and src_dir.is_dir():
        md_files = sorted(f for f in src_dir.iterdir()
                          if f.is_file() and f.suffix.lower() == ".md"
                          and f.name != "STATUS.md")
        for md in md_files:
            try:
                text = md.read_text()
            except Exception:
                continue
            lines = [ln.rstrip() for ln in text.splitlines()]
            # strip frontmatter
            if lines and lines[0].strip() == "---":
                try:
                    end = lines.index("---", 1)
                    lines = lines[end + 1:]
                except ValueError:
                    pass
            body = "\n".join(lines).strip()
            if not body:
                continue
            # first heading as a title candidate
            for ln in lines:
                if ln.startswith("#"):
                    title_line = ln.lstrip("# ").strip()
                    break
            # first ~2 non-empty, non-heading paragraphs
            paras: list[str] = []
            buf: list[str] = []
            for ln in lines:
                if ln.startswith("#"):
                    continue
                if ln.strip():
                    buf.append(ln.strip())
                elif buf:
                    paras.append(" ".join(buf))
                    buf = []
                if len(paras) >= 2:
                    break
            if buf and len(paras) < 2:
                paras.append(" ".join(buf))
            if paras:
                excerpt = f"_Synthesized from `{md.name}`:_\n\n" + "\n\n".join(paras[:2])
                break

    carried_md = "\n".join(f"- `research_source/{Path(f).name}`" for f in carried) or "- _(none carried)_"
    heading = title_line or name
    return f"""# {name} — Overview (draft)

> Draft seed synthesized on {today} when this project was promoted from the idea
> `{project_id}` via POST /console/promote. Refine before use.

## Working title / thesis

{heading}

## Synthesized from the idea

{excerpt or "_(no source markdown found to synthesize — start from the carried files below)_"}

## Carried source files

{carried_md}
"""


@router.post("/console/promote")
def promote_idea(req: PromoteRequest):
    """Promote an Idea into a first-class Project: scaffold the doc-set, register it
    in projects.json, reassign it in the seed (append to the business, drop from
    ideas[]), then rebuild the derived store so the change is live at once."""
    warnings: list[str] = []
    today = datetime.now().strftime("%Y-%m-%d")

    store = _load_store()

    # (a) find the idea
    idea = next((i for i in store.get("ideas", []) if i.get("slug") == req.idea_slug), None)
    if idea is None:
        raise HTTPException(404, f"idea '{req.idea_slug}' not found")

    # (b) validate the target business
    seed = json.loads(SEED_FILE.read_text())
    biz = next((b for b in seed.get("businesses", []) if b.get("id") == req.business_id), None)
    if biz is None:
        raise HTTPException(404, f"business '{req.business_id}' not found")

    # (c) derive project id + name
    project_id = (req.project_id or req.idea_slug).strip()
    if not project_id or "/" in project_id or project_id.startswith("."):
        raise HTTPException(400, f"invalid project_id '{project_id}'")
    name = req.name or idea.get("name") or project_id.replace("-", " ").title()

    # (d) scaffold the doc-set — refuse to clobber an existing project dir
    proj_dir = PROJECTS_DIR / project_id
    if proj_dir.exists():
        raise HTTPException(409, f"project dir already exists: 20_Projects/{project_id} — not clobbering")

    src_dir = _resolve_idea_dir(idea)
    if idea.get("dir") and src_dir is None:
        warnings.append(f"idea source dir '{idea.get('dir')}' not readable in the container — "
                        "no source files carried; overview left as a stub")

    # copy carried source files first (so overview can reference them)
    carried: list[str] = []
    research_src = proj_dir / "research_source"
    try:
        proj_dir.mkdir(parents=True, exist_ok=False)
        if src_dir and src_dir.is_dir():
            research_src.mkdir(parents=True, exist_ok=True)
            for f in sorted(src_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    shutil.copy2(f, research_src / f.name)
                    carried.append(f.name)

        # STATUS.md — mirror routes/projects.py setup_project format
        status_md = f"""---
name: {name}
status: yellow
type: active
version: 0.1.0
milestone: Phase 1 - Kickoff
milestone_pct: 0
updated: {today}
next: Define scope and goals
pinned: false
plane_project: null
---
"""
        (proj_dir / "STATUS.md").write_text(status_md)

        # lowercase filenames so console_store._DOCSET_GLOBS (case-sensitive on
        # Linux: overview*.md / ethos*.md / research*.md) discovers them.
        (proj_dir / "overview.md").write_text(
            _synthesize_overview(name, project_id, src_dir, carried, today)
        )

        research_links = "\n".join(
            f"- [`{c}`](research_source/{c})" for c in carried
        ) or "- _(no source files were carried from the idea)_"
        (proj_dir / "ethos_and_goal.md").write_text(
            f"""# {name} — Ethos & Goal (stub)

_Promoted from idea `{project_id}` on {today}. Fill in._

## Why this exists

_TODO._

## The goal

_TODO._
"""
        )
        (proj_dir / "research.md").write_text(
            f"""# {name} — Research (stub)

_Promoted from idea `{project_id}` on {today}. Carried idea files live under
`research_source/` and are linked below._

## Carried idea sources

{research_links}

## Open questions

_TODO._
"""
        )
    except HTTPException:
        raise
    except Exception as e:
        # best-effort rollback of a partial scaffold
        logger.exception("promote scaffold failed: %s", e)
        try:
            if proj_dir.exists():
                shutil.rmtree(proj_dir)
        except Exception:
            pass
        raise HTTPException(500, f"scaffold failed: {e}")

    # (e) register in projects.json
    try:
        registry = json.loads(PROJECTS_JSON.read_text()) if PROJECTS_JSON.exists() else []
        if not isinstance(registry, list):
            registry = []
        if not any(p.get("id") == project_id for p in registry):
            registry.append({
                "id": project_id,
                "name": name,
                "status": "yellow",
                "description": idea.get("note") or f"Promoted from idea {project_id}.",
                "advisor": "kai",
                "active": True,
            })
            PROJECTS_JSON.write_text(json.dumps(registry, indent=2) + "\n")
    except Exception as e:
        logger.exception("promote projects.json: %s", e)
        warnings.append(f"projects.json update failed: {e}")

    # (f) reassign in the seed — append to the business, drop from ideas[]
    try:
        biz.setdefault("projects", [])
        if project_id not in biz["projects"]:
            biz["projects"].append(project_id)
        seed["ideas"] = [i for i in seed.get("ideas", []) if i.get("slug") != req.idea_slug]
        SEED_FILE.write_text(json.dumps(seed, indent=2) + "\n")
    except Exception as e:
        logger.exception("promote seed reassign: %s", e)
        raise HTTPException(500, f"seed reassign failed: {e}")

    # (g) rebuild the derived store
    try:
        new_store = console_store.build_store(write=True)
    except Exception as e:
        logger.exception("promote store rebuild: %s", e)
        raise HTTPException(500, f"store rebuild failed after promote: {e}")

    # (h) Plane project creation is out of v1 scope
    warnings.append("Plane project NOT created — no worker-api endpoint for creating "
                    "a Plane PROJECT. Create it in Plane and link it in STATUS.md "
                    "(plane_project) as a design follow-on.")

    # (i) return the new project from the freshly-built store
    project = next((p for p in new_store.get("projects", []) if p.get("id") == project_id), None)
    if project is None:
        warnings.append("promoted project not visible in rebuilt store — check derivation")
    return {"ok": True, "project": project, "warnings": warnings}


@router.post("/console/rebuild")
def rebuild_store():
    """Manually re-derive console_store.json from the seed + registry + vault dirs."""
    try:
        store = console_store.build_store(write=True)
    except Exception as e:
        logger.exception("console rebuild: %s", e)
        raise HTTPException(500, f"rebuild failed: {e}")
    return {"ok": True, "counts": store.get("counts", {})}

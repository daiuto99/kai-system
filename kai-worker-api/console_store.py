#!/usr/bin/env python3
"""Project Console object model + store — RUNTIME copy (s5-console P1/P2, KAI-1455/1456).

⚠ SSOT NOTE — this is the RUNTIME copy, co-located with the read-API inside the
kai-worker-api container so the container can REBUILD the store after a mutation
(POST /console/promote, /console/rebuild — KAI-1456). Its dev/CLI twin lives at
`~/sonicink/scripts/console_store.py` and MUST stay behavior-identical; edit both
together. The only intentional differences are path roots + a container-resilient
ideas scan:
  * VAULT resolves to VAULT_PATH (=/vault, the container mount), not ~/vault.
  * The container has NO access to ~/sonicink, so the legacy sonicink/ideas/<slug>
    scan is guarded (skipped if absent) and loose Ideas are additionally scanned
    from vault/20_Projects/ideas/<slug>/ (dirs WITH a STATUS.md), so the derived
    Idea set converges with the dev twin regardless of which machine builds it.

Design: docs/PROJECT_CONSOLE_DESIGN.md (object model §2, data direction §8).

Layers:
  Business    authored in vault/00_System/console_businesses.json (the seed).
  Project     derived: projects.json registry x vault/20_Projects/<id>/.
  Deliverable authored in the seed.
  Idea        authored seed ideas + loose captures (sonicink/ideas/ if present +
              vault/20_Projects/ideas/<slug>/ dirs that carry a STATUS.md).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    # normal in-container import
    from config import VAULT_PATH
except Exception:  # pragma: no cover - CLI/dev fallback
    VAULT_PATH = Path.home() / "vault"

HOME = Path.home()
VAULT = VAULT_PATH
# Legacy loose-capture dir on the dev box; absent inside the container.
SONICINK_IDEAS = HOME / "sonicink" / "ideas"

SEED = VAULT / "00_System" / "console_businesses.json"
PROJECTS_JSON = VAULT / "00_System" / "projects.json"
PROJECTS_DIR = VAULT / "20_Projects"
VAULT_IDEAS_DIR = PROJECTS_DIR / "ideas"
STORE = VAULT / "00_System" / "console_store.json"

STORE_VERSION = "1.0.0"

_NOT_PROJECTS = {"archived", "ideas", "idontneedthis", "wordpress"}

_DOCSET_GLOBS = {
    "overview": ["overview*.md", "OVERVIEW*.md", "README.md"],
    "ethos_goal": ["ethos*.md", "*ethos*.md", "*goal*.md", "GOAL*.md"],
    "research": ["research*.md", "RESEARCH*.md", "*research*.md"],
    "status": ["STATUS.md"],
}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _parse_frontmatter(md: Path) -> dict[str, str]:
    try:
        text = md.read_text()
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _rel(path: Path) -> str:
    """Home-relative path string. In the container HOME is not the vault root, so
    fall back to a VAULT-relative `vault/...` string to match the dev twin's shape."""
    try:
        return str(path.relative_to(HOME))
    except ValueError:
        try:
            return "vault/" + str(path.relative_to(VAULT))
        except ValueError:
            return str(path)


def _find_docset(pdir: Path) -> dict[str, str | None]:
    docset: dict[str, str | None] = {}
    for slot, globs in _DOCSET_GLOBS.items():
        hit: str | None = None
        for g in globs:
            matches = sorted(pdir.glob(g))
            if matches:
                hit = _rel(matches[0])
                break
        docset[slot] = hit
    return docset


def _build_projects(seed: dict, registry: list[dict]) -> list[dict]:
    reg_by_id = {p.get("id"): p for p in registry if p.get("id")}
    biz_of: dict[str, str] = {}
    for b in seed.get("businesses", []):
        for pid in b.get("projects", []):
            biz_of[pid] = b["id"]
    idea_slugs = {i.get("slug", "").lower() for i in seed.get("ideas", [])}

    dir_by_lower: dict[str, str] = {}
    if PROJECTS_DIR.is_dir():
        for d in sorted(PROJECTS_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                dir_by_lower.setdefault(d.name.lower(), d.name)

    ids: list[str] = []
    seen: set[str] = set()
    for pid in list(biz_of.keys()) + list(reg_by_id.keys()):
        if pid and pid.lower() not in seen and pid.lower() not in idea_slugs:
            seen.add(pid.lower())
            ids.append(pid)
    for lower, actual in dir_by_lower.items():
        if lower in seen or lower in _NOT_PROJECTS or lower in idea_slugs:
            continue
        if (PROJECTS_DIR / actual / "STATUS.md").exists():
            seen.add(lower)
            ids.append(actual)

    projects: list[dict] = []
    for pid in ids:
        reg = reg_by_id.get(pid, {})
        folder = dir_by_lower.get(pid.lower(), pid)
        pdir = PROJECTS_DIR / folder
        fm = _parse_frontmatter(pdir / "STATUS.md") if pdir.is_dir() else {}
        sources = []
        if reg:
            sources.append("projects.json")
        if fm:
            sources.append(f"vault/20_Projects/{pid}/STATUS.md")
        business_id = biz_of.get(pid)
        # plane_project — the per-project Plane board id (KAI-1462), set by promote
        # into STATUS.md frontmatter. The frontmatter parser yields strings, so an
        # authored `null`/empty coerces back to None.
        _pp = fm.get("plane_project")
        plane_project = _pp if _pp and _pp not in ("null", "none", "") else None
        projects.append({
            "id": pid,
            "business_id": business_id,
            "name": reg.get("name") or pid.replace("-", " ").title(),
            "status": fm.get("status") or reg.get("status") or "unknown",
            "milestone": fm.get("milestone"),
            "milestone_pct": fm.get("milestone_pct"),
            "next": fm.get("next") or reg.get("next"),
            "description": reg.get("description"),
            "type": reg.get("type"),
            "advisor": reg.get("advisor"),
            "docs_dir": _rel(pdir) if pdir.is_dir() else None,
            "docset": _find_docset(pdir) if pdir.is_dir() else {},
            "brand": {
                "scope": "project",
                "inherits": business_id,
                "style_ref": _rel(pdir / "style.md") if (pdir / "style.md").exists() else None,
            },
            "plane_project": plane_project,
            "unassigned": business_id is None,
            "sources": sources,
        })
    return projects


def _idea_from_dir(d: Path, fm: dict[str, str]) -> dict:
    files = sorted(
        _rel(f) for f in d.iterdir()
        if f.is_file() and f.name != "STATUS.md" and not f.name.startswith(".")
    )
    return {
        "slug": d.name,
        "name": fm.get("name") or d.name,
        "type": fm.get("type") or "open-ideation",
        "status": fm.get("status") or "active",
        "dir": _rel(d),
        "files": files,
    }


def _build_ideas(seed: dict) -> list[dict]:
    """Ideas = authored seed ideas merged with loose captures. Loose captures come
    from sonicink/ideas/<slug>/ (dev box only, guarded) AND vault/20_Projects/ideas/
    <slug>/ dirs that carry a STATUS.md (works inside the container). Dedup by slug;
    authored entries win, then whichever loose source is seen first."""
    ideas: list[dict] = []
    seen: set[str] = set()

    for a in seed.get("ideas", []):
        slug = a.get("slug")
        if not slug:
            continue
        src = HOME / a["source_dir"] if a.get("source_dir") else None
        if src and not src.is_dir():
            # container: source_dir is authored home-relative (vault/...) but HOME
            # is not the vault root — retry against VAULT.
            alt = VAULT / a["source_dir"].split("vault/", 1)[-1] if a.get("source_dir", "").startswith("vault/") else None
            if alt and alt.is_dir():
                src = alt
        fm = _parse_frontmatter(src / "STATUS.md") if src and src.is_dir() else {}
        files = []
        if src and src.is_dir():
            files = sorted(
                _rel(f) for f in src.iterdir()
                if f.is_file() and f.name != "STATUS.md" and not f.name.startswith(".")
            )
        ideas.append({
            "slug": slug,
            "name": a.get("name") or slug.replace("-", " ").title(),
            "type": a.get("type") or fm.get("type") or "open-ideation",
            "status": a.get("status") or fm.get("status") or "active",
            "dir": a.get("source_dir") or None,
            "files": files,
            "note": a.get("note"),
            "authored": True,
        })
        seen.add(slug.lower())

    # Legacy dev-box loose captures (skipped in-container where the dir is absent).
    if SONICINK_IDEAS.is_dir():
        for d in sorted(SONICINK_IDEAS.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name.lower() in seen:
                continue
            ideas.append(_idea_from_dir(d, _parse_frontmatter(d / "STATUS.md")))
            seen.add(d.name.lower())

    # Vault-resident loose captures: only dirs carrying a STATUS.md count as Ideas
    # (filters scratch dirs like dj-feed / reclamation that have no status).
    if VAULT_IDEAS_DIR.is_dir():
        for d in sorted(VAULT_IDEAS_DIR.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name.lower() in seen:
                continue
            if not (d / "STATUS.md").exists():
                continue
            ideas.append(_idea_from_dir(d, _parse_frontmatter(d / "STATUS.md")))
            seen.add(d.name.lower())

    return ideas


def build_store(write: bool = True) -> dict:
    seed = _read_json(SEED, {"businesses": [], "deliverables": []})
    registry = _read_json(PROJECTS_JSON, [])
    if not isinstance(registry, list):
        registry = []

    projects = _build_projects(seed, registry)
    deliverables = seed.get("deliverables", [])
    ideas = _build_ideas(seed)

    store = {
        "_note": "DERIVED store — do not hand-edit. Regenerate: python3 scripts/console_store.py --build (dev) or POST /console/rebuild (runtime). Authored Business layer lives in console_businesses.json; Projects derive from projects.json + vault/20_Projects/<id>/.",
        "version": STORE_VERSION,
        "seed_version": seed.get("version"),
        "businesses": seed.get("businesses", []),
        "projects": projects,
        "deliverables": deliverables,
        "ideas": ideas,
        "counts": {
            "businesses": len(seed.get("businesses", [])),
            "projects": len(projects),
            "deliverables": len(deliverables),
            "ideas": len(ideas),
            "unassigned_projects": sum(1 for p in projects if p["unassigned"]),
        },
    }
    if write:
        STORE.write_text(json.dumps(store, indent=2) + "\n")
    return store


# ---- read-API data layer ----

def load_store() -> dict:
    store = _read_json(STORE, None)
    if store is None:
        store = build_store(write=False)
    return store


def get_businesses() -> list[dict]:
    return load_store().get("businesses", [])


def get_projects(business_id: str | None = None) -> list[dict]:
    projects = load_store().get("projects", [])
    if business_id:
        return [p for p in projects if p.get("business_id") == business_id]
    return projects


def get_project(project_id: str) -> dict | None:
    for p in load_store().get("projects", []):
        if p.get("id") == project_id:
            return p
    return None


def get_ideas() -> list[dict]:
    return load_store().get("ideas", [])


def _cmd_show(store: dict) -> None:
    c = store["counts"]
    print(f"Console store v{store['version']} — {c['businesses']} businesses, "
          f"{c['projects']} projects, {c['deliverables']} deliverables, {c['ideas']} ideas")


def main() -> int:
    ap = argparse.ArgumentParser(description="Project Console object model + store (runtime copy)")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    if not (args.build or args.show):
        ap.print_help()
        return 1
    store = build_store(write=args.build)
    if args.build:
        print(f"[console_store] wrote store — {store['counts']}")
    if args.show:
        _cmd_show(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())

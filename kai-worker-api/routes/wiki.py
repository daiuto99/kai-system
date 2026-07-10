import logging
from pathlib import Path  # noqa: F401
from fastapi import APIRouter, HTTPException
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/wiki/tree")
def wiki_tree():
    knowledge_dir = VAULT_PATH / "70_Knowledge"
    if not knowledge_dir.exists():
        return {"tree": []}

    def build_tree(path, rel=""):
        items = []
        try:
            for item in sorted(path.iterdir()):
                rel_path = f"{rel}/{item.name}" if rel else item.name
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    children = build_tree(item, rel_path)
                    items.append({"type": "dir", "name": item.name, "path": rel_path, "children": children})
                elif item.suffix == ".md":
                    items.append({"type": "file", "name": item.name, "path": rel_path})
        except PermissionError:
            pass
        return items

    return {"tree": build_tree(knowledge_dir)}


@router.get("/wiki/file")
def wiki_file(path: str):
    knowledge_dir = VAULT_PATH / "70_Knowledge"
    full_path = (knowledge_dir / path).resolve()
    if not full_path.is_relative_to(knowledge_dir.resolve()):
        raise HTTPException(403, "Invalid path")
    if not full_path.exists() or full_path.suffix != ".md":
        raise HTTPException(404, "File not found")
    return {"path": path, "content": full_path.read_text(encoding="utf-8"), "name": full_path.stem}

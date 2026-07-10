import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH, WORKSPACE_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

BINARY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
                     ".pdf", ".zip", ".tar", ".gz", ".mp3", ".mp4", ".woff", ".woff2"}


@router.get("/vault/read")
def read_file(path: str):
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if target.is_dir():
        files = [str(f.relative_to(VAULT_PATH)) for f in target.rglob("*") if f.is_file()]
        return {"path": path, "is_directory": True, "files": sorted(files)}
    if target.suffix.lower() in BINARY_EXTENSIONS:
        return {
            "path": path,
            "binary": True,
            "vault_path": path,
            "note": "Binary file — do not read as text. Pass vault_path directly to wordpress_write_file as source_path."
        }
    return {"path": path, "content": target.read_text(encoding="utf-8")}


class _WriteBody(BaseModel):
    content: str


@router.post("/vault/write")
def write_file(path: str, body: _WriteBody):
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {"status": "written", "path": path}


@router.get("/vault/list")
def list_files(path: str = ""):
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    files = [str(f.relative_to(VAULT_PATH)) for f in target.rglob("*") if f.is_file()]
    return {"path": path, "files": sorted(files)}


@router.get("/workspace/read")
def read_workspace_file(path: str):
    target = WORKSPACE_PATH / path
    if not target.resolve().is_relative_to(WORKSPACE_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found in workspace: {path}")
    if target.suffix.lower() in BINARY_EXTENSIONS:
        return {
            "path": path,
            "binary": True,
            "worker_path": f"/workspace/{path}",
            "note": "Binary file — do not read as text. Pass worker_path directly to wordpress_write_file as source_path."
        }
    return {"path": path, "content": target.read_text(encoding="utf-8")}


@router.get("/workspace/list")
def list_workspace_files(path: str = ""):
    target = WORKSPACE_PATH / path
    if not target.resolve().is_relative_to(WORKSPACE_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found in workspace: {path}")
    files = [str(f.relative_to(WORKSPACE_PATH)) for f in target.rglob("*") if f.is_file()]
    return {"path": path, "files": sorted(files)}

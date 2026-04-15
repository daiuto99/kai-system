from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path
import os

app = FastAPI(title="kai-worker-api", version="0.1.0")

VAULT_PATH = Path("/vault")


@app.get("/health")
def health():
    vault_ok = VAULT_PATH.exists()
    return {
        "status": "ok",
        "service": "kai-worker-api",
        "vault_mounted": vault_ok,
        "vault_path": str(VAULT_PATH),
    }


@app.get("/vault/read")
def read_file(path: str):
    """Read a file from the vault. Path is relative to vault root."""
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return {"path": path, "content": target.read_text(encoding="utf-8")}


@app.post("/vault/write")
def write_file(path: str, content: str):
    """Write a file to the vault. Path is relative to vault root."""
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "written", "path": path}


@app.get("/vault/list")
def list_files(path: str = ""):
    """List files in a vault directory."""
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    files = [str(f.relative_to(VAULT_PATH)) for f in target.rglob("*") if f.is_file()]
    return {"path": path, "files": sorted(files)}


# ── Focus Brief ──────────────────────────────────────────────────────────────

from focus import run_focus_brief
from pydantic import BaseModel as _BaseModel

class FocusRequest(_BaseModel):
    kai_focus_channel_id: str


@app.post("/focus/run")
def trigger_focus_brief(req: FocusRequest):
    """Trigger the morning focus brief. Called by n8n at 8:45am EST."""
    result = run_focus_brief(req.kai_focus_channel_id)
    return result

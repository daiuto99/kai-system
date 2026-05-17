from pathlib import Path
import os

VAULT_PATH = Path("/vault")
WORKSPACE_PATH = Path("/workspace")

def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")

TODOIST_TOKEN = load_secret("todoist_api_key")

def safe_path(base: Path, rel: str) -> Path | None:
    """Resolve rel against base; return None if it would escape base."""
    try:
        p = (base / rel.lstrip("/")).resolve()
        p.relative_to(base.resolve())
        return p
    except ValueError:
        return None

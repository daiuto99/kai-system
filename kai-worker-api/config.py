from pathlib import Path
import os

VAULT_PATH = Path("/vault")

def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")

TODOIST_TOKEN = load_secret("todoist_api_key")

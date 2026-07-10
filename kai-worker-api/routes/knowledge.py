import json
import logging
from fastapi import APIRouter, HTTPException
from config import VAULT_PATH, safe_path

logger = logging.getLogger(__name__)
router = APIRouter()

SESSIONS_DIR  = VAULT_PATH / "60_Council" / "sessions"
DECISIONS_DIR = VAULT_PATH / "60_Council" / "decisions"
TOKEN_USAGE_FILE = VAULT_PATH / "00_System" / "token_usage.json"


@router.get("/knowledge/sessions")
def list_all_sessions():
    if not SESSIONS_DIR.exists():
        return {"sessions": {}}
    result = {}
    for ch_dir in sorted(SESSIONS_DIR.iterdir()):
        if ch_dir.is_dir():
            files = sorted(ch_dir.glob("*.md"), reverse=True)
            result[ch_dir.name] = []
            for f in files:
                first_line = f.read_text(encoding="utf-8").split("\n")[0].replace("# ", "").strip()
                result[ch_dir.name].append({
                    "filename": f.name,
                    "path": f"60_Council/sessions/{ch_dir.name}/{f.name}",
                    "title": first_line,
                    "channel": ch_dir.name,
                })
    return {"sessions": result}


@router.get("/knowledge/session")
def read_session(path: str):
    target = VAULT_PATH / path
    if not target.resolve().is_relative_to(VAULT_PATH):
        raise HTTPException(404, "Session not found")
    if not target.exists():
        raise HTTPException(404, "Session not found")
    return {"path": path, "content": target.read_text(encoding="utf-8")}


@router.get("/knowledge/decisions")
def list_decisions():
    if not DECISIONS_DIR.exists():
        return {"decisions": [], "files": []}
    files = sorted(DECISIONS_DIR.glob("*.md"), reverse=True)
    all_files = []
    for f in files:
        all_files.append({
            "filename": f.name,
            "path": f"60_Council/decisions/{f.name}",
            "month": f.stem,
        })
    return {"files": all_files}


@router.get("/knowledge/decisions/{month}")
def read_decisions_month(month: str):
    if ".." in month:
        raise HTTPException(400, "Invalid month")
    target = safe_path(DECISIONS_DIR, f"{month}.md")
    if target is None:
        raise HTTPException(400, "Invalid path")
    if not target.exists():
        raise HTTPException(404, f"No decisions for {month}")
    return {"month": month, "content": target.read_text(encoding="utf-8")}


@router.get("/insights")
def get_insights():
    insights_file = VAULT_PATH / "60_Council" / "ember" / "insights.md"
    if not insights_file.exists():
        return {"insights": []}

    content = insights_file.read_text(encoding="utf-8")
    insights = []
    current_category = None

    for line in content.splitlines():
        if line.startswith("## "):
            current_category = line[3:].strip().rstrip("s")
        elif line.startswith("- [") and current_category:
            import re
            m = re.match(r"- \[(\d{4}-\d{2}-\d{2})\] (.+)", line)
            if m:
                insights.append({
                    "category": current_category,
                    "date":     m.group(1),
                    "content":  m.group(2).strip(),
                })

    insights.reverse()
    return {"insights": insights, "count": len(insights)}


@router.get("/token-usage")
def get_token_usage():
    if not TOKEN_USAGE_FILE.exists():
        return {"days": [], "total": {"input": 0, "output": 0, "cost_usd": 0.0, "calls": 0}}
    data = json.loads(TOKEN_USAGE_FILE.read_text())
    return data

import subprocess
import sys
import json
import re
import os
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter
from config import VAULT_PATH

router = APIRouter()

MANIFEST_PATH = VAULT_PATH / "00_System" / "session_close_log.json"


@router.get("/session/brief")
def session_brief():
    """S7-2: Compact session brief (~200 tokens) for catch-up.
    Replaces full reads of StateOfTheUnion + Sprint_History + vault session files.
    """
    brief = {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "version": None,
        "last_session": None,
        "sprint": None,
        "sprint_status": None,
        "open_items": [],
        "recent_decisions": [],
    }

    # 1. StateOfTheUnion.md
    sotu = Path("/workspace/StateOfTheUnion.md")
    if sotu.exists():
        lines = sotu.read_text().splitlines()
        in_open = in_next = False
        for line in lines:
            if not brief["version"]:
                m = re.search(r"Last updated:.*?v([\d.]+)", line)
                if m:
                    brief["version"] = m.group(1)
            if not brief["last_session"]:
                m = re.search(r"Last session:\*\*\s*(.+)", line)
                if m:
                    brief["last_session"] = m.group(1).strip()[:100]
            if "**Open:**" in line:
                in_open, in_next = True, False
            elif "**What's next:**" in line:
                in_open, in_next = False, True
            elif line.startswith("---") or line.startswith("##"):
                in_open = in_next = False
            elif in_open and line.startswith("- "):
                brief["open_items"].append(line[2:].strip()[:80])
            elif in_next and line.startswith("- ") and not brief["sprint"]:
                brief["sprint"] = line[2:].strip()[:80]
                brief["sprint_status"] = "planned"

    # 2. Sprint_History.md — top entry
    sh = Path("/workspace/Sprint_History.md")
    if sh.exists():
        for line in sh.read_text().splitlines()[:10]:
            m = re.match(r"## Session (\S+).*?-- (.+?) -- (Complete|In Progress)", line)
            if not m:
                m = re.match(r"## Session (\S+).*?— (.+?) — (Complete|In Progress)", line)
            if m:
                brief["last_session"] = brief["last_session"] or f"{m.group(1)} — {m.group(2)}"
                brief["sprint"] = brief["sprint"] or m.group(2)
                brief["sprint_status"] = m.group(3).lower().replace(" ", "_")
                break

    # 3. Latest vault session file — recent decisions
    session_dir = VAULT_PATH / "60_Council" / "sessions" / "kai"
    if session_dir.exists():
        files = sorted(session_dir.glob("*.md"), key=os.path.getmtime, reverse=True)
        if files:
            try:
                lines = files[0].read_text().splitlines()
                brief["last_session"] = brief["last_session"] or files[0].stem
                in_decisions = False
                for line in lines:
                    if line.startswith("## Decisions"):
                        in_decisions = True
                    elif line.startswith("## ") and in_decisions:
                        break
                    elif in_decisions and line.startswith("- "):
                        brief["recent_decisions"].append(line[2:].strip()[:80])
                        if len(brief["recent_decisions"]) >= 4:
                            break
            except Exception:
                pass

    return brief


@router.post("/session/tool-output-log")
def log_tool_output(tool: str, size: int, session_id: str = "unknown"):
    """S7-6: Log large tool outputs for pattern analysis."""
    log_path = VAULT_PATH / "00_System" / "tool_output_patterns.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(log_path.read_text()) if log_path.exists() else {"entries": []}
    existing["entries"].append({
        "tool": tool,
        "size": size,
        "session_id": session_id,
        "at": datetime.utcnow().isoformat() + "Z",
    })
    existing["entries"] = existing["entries"][-500:]
    log_path.write_text(json.dumps(existing, indent=2))
    return {"ok": True}


@router.get("/session/close-status")
def close_status():
    if not MANIFEST_PATH.exists():
        return {"status": "no_manifest", "message": "No close manifest found — session not yet closed via engine"}
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
        steps = manifest.get("steps", [])
        failed = [s for s in steps if s.get("status") == "fail"]
        ok_count = sum(1 for s in steps if s.get("status") == "ok")
        return {
            "status": "ok" if not failed else "partial",
            "date": manifest.get("date"),
            "session_title": manifest.get("session_title"),
            "mode": manifest.get("mode"),
            "overall": manifest.get("overall"),
            "steps_ok": ok_count,
            "steps_total": len(steps),
            "failed": [
                {"name": s["name"], "label": s["label"], "detail": s["detail"]}
                for s in failed
            ],
            "steps": [
                {"name": s["name"], "label": s["label"], "status": s["status"], "detail": s["detail"]}
                for s in steps
            ],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/session/close")
def trigger_close(background: bool = False):
    """Trigger manual_close.py. Returns stdout/stderr and exit code."""
    script = Path("/home/leo/sonicink/scripts/manual_close.py")
    python = sys.executable

    if not script.exists():
        return {"ok": False, "error": "manual_close.py not found", "path": str(script)}

    if background:
        subprocess.Popen(
            [python, str(script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"ok": True, "mode": "background", "message": "manual_close.py launched in background"}

    try:
        result = subprocess.run(
            [python, str(script)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[-4000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": "manual_close.py exceeded 300s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

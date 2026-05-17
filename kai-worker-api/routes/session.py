import subprocess
import sys
import json
from pathlib import Path
from fastapi import APIRouter
from config import VAULT_PATH

router = APIRouter()

MANIFEST_PATH = VAULT_PATH / "00_System" / "session_close_log.json"


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
    """Trigger manual_close.py. Returns stdout/stderr and exit code.
    background=true fires it detached (returns immediately).
    """
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

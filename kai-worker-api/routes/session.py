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

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from config import VAULT_PATH
import httpx
from watchdog import _worker_auth

logger = logging.getLogger(__name__)
router = APIRouter()

INBOX_DIR     = VAULT_PATH / "50_Inbox"
PROCESSED_DIR = INBOX_DIR / "processed"
PENDING_DIR   = INBOX_DIR / "pending"
FAILED_DIR    = INBOX_DIR / "failed"
COUNCIL_API   = "http://kai-council-api:8002"
WORKER_API    = "http://kai-worker-api:8001"

VALID_ROUTES  = {"doc", "sky", "roads", "beats", "creative", "dev", "project", "kai", "parking-lot"}
VALID_ACTIONS = {"ingest", "create_project", "update_project", "create_task", "evaluate", "capture"}


def _notify(channel: str, message: str):
    # Routes to Telegram via the shared tg_alert chokepoint (AR-5 sole surface).
    # `channel` is accepted for call-site compatibility but ignored. Fail-soft.
    from tg_alert import tg_alert
    tg_alert(message)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (meta dict, body text). Handles --- delimited frontmatter."""
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta, body_lines, in_fm = {}, [], True
    for line in lines[1:]:
        if in_fm and line.strip() == "---":
            in_fm = False
            continue
        if in_fm:
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        else:
            body_lines.append(line)
    return meta, "\n".join(body_lines).strip()


def _process_file(path: Path):
    """Core intake processor — called by watcher and manual trigger."""
    filename = path.name
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error("inbox: cannot read %s: %s", filename, e)
        path.rename(FAILED_DIR / filename)
        return

    meta, body = _parse_frontmatter(text)
    route  = meta.get("route", "").lower().strip()
    action = meta.get("action", "evaluate").lower().strip()
    context = meta.get("context", "")
    project_id = meta.get("project_id", "")

    # Clarification gate — ambiguous or missing route
    if route not in VALID_ROUTES:
        msg = (
            f":inbox_tray: *Inbox intake needs routing* — `{filename}`\n"
            f"No valid `route:` field found (got: `{route or 'none'}`).\n"
            f"Valid routes: {', '.join(sorted(VALID_ROUTES))}\n"
            f"Reply with the correct route and I'll reprocess it, or edit the file at `~/vault/50_Inbox/pending/{filename}`."
        )
        _notify("#devops", msg)
        path.rename(PENDING_DIR / filename)
        logger.info("inbox: %s → pending (no route)", filename)
        return

    if action not in VALID_ACTIONS:
        action = "evaluate"

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Build dispatch prompt for council
    prompt_parts = [f"[INBOX INTAKE — {filename}]"]
    if context:
        prompt_parts.append(f"Context from sender: {context}")
    if project_id:
        prompt_parts.append(f"Project ID: {project_id}")
    prompt_parts.append(f"Action requested: {action}")
    prompt_parts.append(f"\n---\n{body}")
    prompt = "\n".join(prompt_parts)

    # Route to council advisor
    advisor = route if route in {"doc", "sky", "roads", "beats", "creative", "dev", "kai"} else "kai"

    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"{COUNCIL_API}/council/message", json={
                "channel": advisor,
                "message": prompt,
                "user_id": "inbox-watcher",
                "history": [],
            }, auth=_worker_auth())
        result = r.json() if r.status_code == 200 else {"reply": f"Council error {r.status_code}"}
    except Exception as e:
        logger.exception("inbox: council call failed: %s", e)
        path.rename(FAILED_DIR / filename)
        _notify("#devops", f":x: Inbox intake failed for `{filename}`: {e}")
        return

    reply = result.get("reply", "")

    # Save the processed result to vault
    out_dir = VAULT_PATH / "60_Council" / advisor / "knowledge"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"inbox-{timestamp}-{filename}"
    out_path.write_text(
        f"---\nsource: inbox\nroute: {route}\naction: {action}\nfile: {filename}\nprocessed: {timestamp}\n---\n\n"
        f"## Original Content\n\n{body}\n\n## KAI Response\n\n{reply}\n"
    )

    # Move file to processed
    path.rename(PROCESSED_DIR / filename)

    # Notify via Telegram
    preview = reply[:400] + ("..." if len(reply) > 400 else "")
    _notify("#devops",
        f":white_check_mark: *Inbox processed* — `{filename}`\n"
        f"Route: `{route}` | Action: `{action}`\n\n{preview}"
    )
    logger.info("inbox: %s → processed (route=%s action=%s)", filename, route, action)


def _scan_inbox():
    """Scan inbox for unprocessed .md files and process each."""
    for d in (INBOX_DIR, PROCESSED_DIR, PENDING_DIR, FAILED_DIR):
        d.mkdir(parents=True, exist_ok=True)
    files = sorted(INBOX_DIR.glob("*.md"))
    if not files:
        return {"processed": 0, "files": []}
    results = []
    for f in files:
        _process_file(f)
        results.append(f.name)
    return {"processed": len(results), "files": results}


@router.post("/inbox/scan")
def scan_inbox(background_tasks: BackgroundTasks):
    """Trigger an inbox scan — called by scheduler every 60s."""
    background_tasks.add_task(_scan_inbox)
    return {"status": "scanning"}


@router.get("/inbox/scan")
def scan_inbox_get():
    """Manual trigger for testing."""
    return _scan_inbox()


@router.get("/inbox/pending")
def list_pending():
    """List files waiting for routing clarification."""
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for f in sorted(PENDING_DIR.glob("*.md")):
        meta, _ = _parse_frontmatter(f.read_text(encoding="utf-8"))
        files.append({"filename": f.name, "meta": meta})
    return {"pending": files, "count": len(files)}


class ReprocessRequest(BaseModel):
    filename: str
    route: str
    action: str = "evaluate"


@router.post("/inbox/reprocess")
def reprocess_pending(req: ReprocessRequest):
    """Move a pending file back to inbox with corrected route and reprocess."""
    src = PENDING_DIR / req.filename
    if not src.exists():
        return {"error": f"{req.filename} not found in pending"}
    text = src.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    meta["route"] = req.route
    meta["action"] = req.action
    new_fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n\n" + body
    dest = INBOX_DIR / req.filename
    dest.write_text(new_fm)
    src.unlink()
    result = _scan_inbox()
    return {"status": "reprocessed", **result}

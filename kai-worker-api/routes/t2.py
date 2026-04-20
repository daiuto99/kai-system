import json
import logging
import uuid
from datetime import datetime as _dt
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

T2_QUEUE_FILE = VAULT_PATH / "00_System" / "t2_queue.json"


def _t2_load() -> list:
    if T2_QUEUE_FILE.exists():
        return json.loads(T2_QUEUE_FILE.read_text())
    return []


def _t2_save(queue: list):
    T2_QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def _slack_token() -> str:
    import os
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


class T2ActionRequest(BaseModel):
    action: str
    detail: str = ""
    advisor: str = "kai"
    slack_channel: str = "kai"


@router.get("/t2/queue")
def get_t2_queue():
    return {"queue": _t2_load()}


@router.post("/t2/queue")
def create_t2_action(req: T2ActionRequest):
    queue = _t2_load()
    action_id = str(uuid.uuid4())[:8]
    entry = {
        "id": action_id,
        "action": req.action,
        "detail": req.detail,
        "advisor": req.advisor,
        "status": "pending",
        "created_at": _dt.now().isoformat(),
        "slack_ts": None,
        "slack_channel_id": None,
    }

    slack_token = _slack_token()
    if slack_token:
        try:
            import httpx as _t2hx
            msg_text = (
                f"*T2 Action Request* — `{action_id}`\n"
                f"*Advisor:* {req.advisor.upper()}\n"
                f"*Action:* {req.action}\n"
                f"{('*Detail:* ' + req.detail) if req.detail else ''}\n\n"
                f"React with to approve, to reject."
            )
            r = _t2hx.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={"channel": req.slack_channel, "text": msg_text},
                timeout=10,
            )
            d = r.json()
            if d.get("ok"):
                entry["slack_ts"] = d.get("ts")
                entry["slack_channel_id"] = d.get("channel")
        except Exception as e:
            logger.exception("T2 Slack post error: %s", e)

    queue.append(entry)
    _t2_save(queue)
    return {"ok": True, "id": action_id, "entry": entry}


@router.post("/t2/approve/{action_id}")
def approve_t2_action(action_id: str):
    queue = _t2_load()
    for entry in queue:
        if entry["id"] == action_id:
            entry["status"] = "approved"
            entry["approved_at"] = _dt.now().isoformat()
            _t2_save(queue)
            logger.info("T2 action %s approved: %s", action_id, entry['action'])
            return {"ok": True, "entry": entry}
    raise HTTPException(404, f"T2 action {action_id} not found")


@router.post("/t2/reject/{action_id}")
def reject_t2_action(action_id: str):
    queue = _t2_load()
    for entry in queue:
        if entry["id"] == action_id:
            entry["status"] = "rejected"
            entry["rejected_at"] = _dt.now().isoformat()
            _t2_save(queue)
            return {"ok": True, "entry": entry}
    raise HTTPException(404, f"T2 action {action_id} not found")

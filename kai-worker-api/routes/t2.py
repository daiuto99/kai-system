import json
import logging
import os
import uuid
from datetime import datetime as _dt
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

T2_QUEUE_FILE = VAULT_PATH / "00_System" / "t2_queue.json"
LEO_USER_ID = os.environ.get("LEO_USER_ID", "U0AG93XJ927")


def _t2_load() -> list:
    if T2_QUEUE_FILE.exists():
        return json.loads(T2_QUEUE_FILE.read_text())
    return []


def _t2_save(queue: list):
    T2_QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _resolve_leo_dm_channel() -> str | None:
    """Open (or fetch) the KAI↔Leo DM channel. Returns channel_id or None."""
    token = _slack_token()
    if not token:
        return None
    try:
        import httpx as _hx
        r = _hx.post(
            "https://slack.com/api/conversations.open",
            headers={"Authorization": f"Bearer {token}"},
            json={"users": LEO_USER_ID},
            timeout=10,
        )
        d = r.json()
        if d.get("ok"):
            return d["channel"]["id"]
        logger.warning("conversations.open failed: %s", d.get("error"))
    except Exception as e:
        logger.exception("conversations.open error: %s", e)
    return None


def _post_slack_thread(entry: dict, approved: bool):
    slack_token = _slack_token()
    if not slack_token or not entry.get("slack_channel_id") or not entry.get("slack_ts"):
        return
    try:
        import httpx as _hx
        status = "Approved — executing now" if approved else "Rejected"
        _hx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {slack_token}"},
            json={
                "channel": entry["slack_channel_id"],
                "thread_ts": entry["slack_ts"],
                "text": status,
            },
            timeout=10,
        )
    except Exception as e:
        logger.exception("T2 thread post error: %s", e)


class T2ActionRequest(BaseModel):
    action: str
    detail: str = ""
    advisor: str = "kai"
    slack_channel: str = ""  # ignored — all T2 prompts go to KAI↔Leo DM


class T2RespondRequest(BaseModel):
    action_id: str
    approved: bool
    user_id: str = ""


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
    dm_channel = _resolve_leo_dm_channel() if slack_token else None
    if slack_token and dm_channel:
        try:
            import httpx as _t2hx
            msg_text = (
                f"*T2 Action Request* — `{action_id}`\n"
                f"*Advisor:* {req.advisor.upper()}\n"
                f"*Action:* {req.action}\n"
                f"{('*Detail:* ' + req.detail) if req.detail else ''}\n\n"
                f"React with ✅ to approve, ❌ to reject."
            )
            r = _t2hx.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {slack_token}"},
                json={
                    "channel": dm_channel,
                    "text": msg_text,
                    "username": "KAI",
                    "icon_url": "https://kai.sonicink.space/avatar-kai.png",
                },
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


@router.post("/t2/respond")
def respond_t2_action(req: T2RespondRequest):
    queue = _t2_load()
    for entry in queue:
        if entry["id"] != req.action_id:
            continue
        if entry["status"] != "pending":
            return {"ok": False, "error": f"Action already {entry['status']}", "entry": entry}
        entry["status"] = "approved" if req.approved else "rejected"
        entry["responded_by"] = req.user_id
        entry["responded_at"] = _dt.now().isoformat()
        _t2_save(queue)
        logger.info("T2 respond %s -> %s: %s", req.action_id, entry["status"], entry["action"])
        _post_slack_thread(entry, req.approved)
        return {"ok": True, "entry": entry}
    raise HTTPException(404, f"T2 action {req.action_id} not found")


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

import logging
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
import httpx as _slhx

logger = logging.getLogger(__name__)
router = APIRouter()


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_api(method: str, payload: dict) -> dict:
    token = _slack_token()
    r = _slhx.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    return r.json()


def _slack_get(method: str, params: dict) -> dict:
    token = _slack_token()
    r = _slhx.get(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )
    return r.json()


@router.post("/slack/channels")
def create_slack_channel(body: dict):
    name = body.get("name", "").lower().replace(" ", "-").replace("_", "-").strip("-")
    if not name:
        raise HTTPException(400, "name required")
    is_private = body.get("private", False)
    result = _slack_api("conversations.create", {"name": name, "is_private": is_private})
    if not result.get("ok"):
        error = result.get("error", "unknown")
        if error == "name_taken":
            return {"ok": False, "error": "Channel already exists", "name": name}
        raise HTTPException(400, f"Slack error: {error}")
    channel = result["channel"]
    return {"ok": True, "channel_id": channel["id"], "name": channel["name"]}


@router.post("/slack/channels/{channel_name}/invite")
def invite_to_slack_channel(channel_name: str, body: dict):
    emails = body.get("emails", [])
    user_ids = list(body.get("user_ids", []))

    not_found = []
    for email in emails:
        res = _slack_get("users.lookupByEmail", {"email": email})
        if res.get("ok"):
            user_ids.append(res["user"]["id"])
        else:
            not_found.append(email)

    if not user_ids:
        raise HTTPException(400, "No valid users found")

    channel_id = None
    res = _slack_get("conversations.list", {"types": "public_channel,private_channel", "limit": 200})
    for ch in res.get("channels", []):
        if ch["name"] == channel_name.lstrip("#"):
            channel_id = ch["id"]
            break

    if not channel_id:
        raise HTTPException(404, f"Channel #{channel_name} not found")

    result = _slack_api("conversations.invite", {"channel": channel_id, "users": ",".join(user_ids)})
    return {
        "ok": result.get("ok"),
        "invited": user_ids,
        "not_found_emails": not_found,
        "error": result.get("error") if not result.get("ok") else None,
    }


@router.get("/slack/users/lookup")
def slack_lookup_user(email: str = None, name: str = None):
    if email:
        res = _slack_get("users.lookupByEmail", {"email": email})
        if res.get("ok"):
            u = res["user"]
            return {"found": True, "user_id": u["id"], "name": u["real_name"], "email": email}
        return {"found": False, "error": res.get("error")}
    elif name:
        res = _slack_get("users.list", {"limit": 200})
        name_lower = name.lower()
        for member in res.get("members", []):
            if name_lower in member.get("real_name", "").lower() or \
               name_lower in member.get("name", "").lower():
                return {"found": True, "user_id": member["id"], "name": member["real_name"]}
        return {"found": False, "name": name}
    raise HTTPException(400, "email or name required")

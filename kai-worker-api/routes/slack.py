import json
import logging
import os
from datetime import datetime as _sdt
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
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


# ── Project registry ────────────────────────────────────────────────────────
_VAULT = Path(os.environ.get("VAULT_PATH", "/vault"))
_KAI_PROJECTS_FILE = _VAULT / "00_System" / "kai_projects.json"
_COUNCIL_API = "http://kai-council-api:8002"


def _load_project_registry() -> dict:
    if _KAI_PROJECTS_FILE.exists():
        try:
            return json.loads(_KAI_PROJECTS_FILE.read_text())
        except Exception:
            pass
    return {}


def _download_slack_file(file_id: str, token: str) -> tuple[dict, bytes | None]:
    """Returns (file_meta, content_bytes). content is None on failure."""
    r = _slhx.get(
        "https://slack.com/api/files.info",
        headers={"Authorization": f"Bearer {token}"},
        params={"file": file_id},
        timeout=15,
    )
    meta = r.json()
    if not meta.get("ok"):
        return meta.get("file", {}), None
    file_info = meta["file"]
    dl_url = file_info.get("url_private_download") or file_info.get("url_private")
    if not dl_url:
        return file_info, None
    dl = _slhx.get(dl_url, headers={"Authorization": f"Bearer {token}"}, timeout=60, follow_redirects=True)
    if dl.status_code == 200:
        return file_info, dl.content
    return file_info, None


def _ingest_file_background(vault_path: str, advisor: str, channel_id: str, filename: str):
    try:
        r = _slhx.post(
            f"{_COUNCIL_API}/council/ingest",
            json={"path": vault_path, "advisor": advisor},
            timeout=120,
        )
        result = r.json() if r.status_code == 200 else {"ok": False, "error": r.text[:200]}
        summary = result.get("summary", "done") if result.get("ok") else f"ingest error: {result.get('error', 'unknown')}"

        # Post confirmation to channel
        token = _slack_token()
        _slhx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "channel": channel_id,
                "text": f":white_check_mark: *{filename}* indexed — {summary}",
                "username": "KAI",
                "icon_url": "https://kai.sonicink.space/icon-192.png",
            },
            timeout=10,
        )
    except Exception as e:
        logger.exception("ingest_file_background failed for %s: %s", vault_path, e)


@router.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Slack Events API receiver — handles file_shared events in KAI-managed channels."""
    body = await request.json()

    # URL verification handshake
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    event = body.get("event", {})
    event_type = event.get("type")

    # Only handle file_shared events
    if event_type != "file_shared":
        return {"ok": True}

    channel_id = event.get("channel_id")
    file_id = event.get("file_id")

    if not channel_id or not file_id:
        return {"ok": True}

    # Gate: only process files in KAI-managed project channels
    registry = _load_project_registry()
    project = registry.get(channel_id)
    if not project:
        return {"ok": True}

    token = _slack_token()
    file_info, content = _download_slack_file(file_id, token)
    if content is None:
        logger.warning("slack_events: could not download file %s", file_id)
        return {"ok": True}

    filename = file_info.get("name", f"file_{file_id}")
    project_name = project.get("project_id", "unknown")
    dest_dir = _VAULT / "20_Projects" / project_name / "files"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    dest_path.write_bytes(content)

    logger.info("slack_events: saved %s to %s", filename, dest_path)

    # Ingest in background so we return quickly to Slack
    background_tasks.add_task(
        _ingest_file_background,
        vault_path=str(dest_path),
        advisor="kai",
        channel_id=channel_id,
        filename=filename,
    )

    return {"ok": True}


@router.get("/slack/projects/registry")
def get_project_registry():
    """Returns all KAI-managed Slack channels."""
    return {"registry": _load_project_registry()}


@router.post("/slack/files/ingest")
async def ingest_slack_file(body: dict, background_tasks: BackgroundTasks):
    """Called by kai-slack-bot when a file_shared event fires in a managed channel."""
    file_id = body.get("file_id")
    channel_id = body.get("channel_id")
    if not file_id or not channel_id:
        raise HTTPException(400, "file_id and channel_id required")

    registry = _load_project_registry()
    project = registry.get(channel_id)
    if not project:
        raise HTTPException(404, "channel not in project registry")

    token = _slack_token()
    file_info, file_bytes = _download_slack_file(file_id, token)
    if file_bytes is None:
        raise HTTPException(502, f"could not download Slack file {file_id}")

    filename = file_info.get("name", f"file_{file_id}")
    project_id = project.get("project_id", "unknown")
    dest_dir = _VAULT / "20_Projects" / project_id / "files"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    dest_path.write_bytes(file_bytes)

    logger.info("slack file saved: %s → %s", filename, dest_path)

    background_tasks.add_task(
        _ingest_file_background,
        vault_path=str(dest_path),
        advisor="kai",
        channel_id=channel_id,
        filename=filename,
    )

    return {"ok": True, "filename": filename, "path": str(dest_path), "project": project.get("project_name")}

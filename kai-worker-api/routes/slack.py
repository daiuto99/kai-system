import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime as _sdt
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
import httpx as _slhx
from safe_http import safe_json
from watchdog import _worker_auth

logger = logging.getLogger(__name__)
router = APIRouter()


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")



def _slack_signing_secret() -> str:
    p = Path("/run/secrets/slack_signing_secret")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_SIGNING_SECRET", "")


def _verify_slack_sig(raw_body: bytes, ts: str, sig: str) -> bool:
    secret = _slack_signing_secret()
    if not secret:
        # Fail closed — misconfigured instance must not accept unverified traffic (L5)
        return False
    try:
        if abs(time.time() - float(ts)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    base = f"v0:{ts}:{raw_body.decode('utf-8', errors='replace')}".encode()
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _slack_api(method: str, payload: dict) -> dict:
    token = _slack_token()
    r = _slhx.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=15,
    )
    return safe_json(r)


def _slack_get(method: str, params: dict) -> dict:
    token = _slack_token()
    r = _slhx.get(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )
    return safe_json(r)


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


_VAULT = Path(os.environ.get("VAULT_PATH", "/vault"))


def _handle_clarification_thread_reply(thread_ts: str, channel_id: str, text: str) -> bool:
    """Sprint A: if a thread reply lands in a thread that has a pending
    clarification, hand it off to handlers.handle_freetext_reply and post the
    response in-thread. Returns True if the thread had a pending row (so the
    caller can skip the check-in path)."""
    try:
        import clarification_store as _cs
        import sprint_a_handlers as _sah
    except ImportError:
        return False
    entry = _cs.find_by_thread_ts(thread_ts)
    if not entry:
        return False
    result = _sah.handle_freetext_reply(entry["id"], text)
    reply = result.get("reply_text") or "_(no reply text)_"
    try:
        _slhx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {_slack_token()}"},
            json={"channel": channel_id, "thread_ts": thread_ts,
                  "text": reply, "username": "KAI",
                  "icon_url": "https://kai.sonicink.space/icon-192.png"},
            timeout=15,
        )
    except Exception as e:
        logger.warning("slack clarification reply failed: %s", e)
    return True


def _handle_checkin_reply(thread_ts: str, channel_id: str, text: str):
    """Called in background when a Slack message arrives — check if it's a check-in reply."""
    import json as _json
    pending_file = _VAULT / "00_System" / "checkin_pending.json"
    if not pending_file.exists():
        return
    try:
        pending = _json.loads(pending_file.read_text())
    except Exception:
        return
    from datetime import datetime as _dt
    today = _dt.utcnow().strftime("%Y-%m-%d")
    matched_type = None
    for checkin_type, meta in pending.items():
        ts_match = meta.get("ts") == thread_ts and meta.get("channel_id") == channel_id
        date_match = meta.get("channel_id") == channel_id and meta.get("date") == today
        if ts_match or date_match:
            matched_type = checkin_type
            break
    if not matched_type:
        return
    logger.info("slack_events: check-in reply detected for %s", matched_type)
    try:
        r = _slhx.post(
            "http://localhost:8001/checkin/slack-reply",
            json={"checkin_type": matched_type, "text": text,
                  "thread_ts": thread_ts, "channel_id": channel_id},
            timeout=15,
            auth=_worker_auth(),
        )
        logger.info("slack_events: checkin-reply result: %s", safe_json(r))
    except Exception as e:
        logger.exception("slack_events: checkin-reply failed: %s", e)


@router.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    """Slack Events API receiver — handles message thread replies (clarifications + check-ins)."""
    raw = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_sig(raw, ts, sig):
        raise HTTPException(403, "Invalid Slack signature")
    import json as _json
    body = _json.loads(raw)

    # URL verification handshake
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    event = body.get("event", {})
    event_type = event.get("type")

    if event_type == "message":
        if event.get("bot_id") or event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
            return {"ok": True}
        thread_ts = event.get("thread_ts")
        channel_id = event.get("channel")
        text = event.get("text", "").strip()
        if thread_ts and channel_id and text:
            def _dispatch_reply(_ts=thread_ts, _ch=channel_id, _txt=text):
                if not _handle_clarification_thread_reply(_ts, _ch, _txt):
                    _handle_checkin_reply(_ts, _ch, _txt)
            background_tasks.add_task(_dispatch_reply)

    return {"ok": True}

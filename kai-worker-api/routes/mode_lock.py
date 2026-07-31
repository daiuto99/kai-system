"""routes/mode_lock.py — KAI-mediated approval prompt (M2-1.C).

Replaces the binary YES unlock with a contextual, Leo-in-the-loop approval flow:

  1. Gate hook on Mac denies a write tool.
  2. Hook POSTs /mode_lock/request_approval with {tool, target, reason}.
  3. This worker route persists the request, posts an interactive Telegram
     message to Leo with 3 inline buttons (Allow once / Deny / Allow session
     1h), returns request_id.
  4. Hook checks GET /mode_lock/approval_status/{request_id}?consume=1 (the
     remote path is async — the in-session `YES` fast path remains the default;
     the Telegram tap is for when Leo is away, with a generous window).
  5. Leo taps a button → the tap arrives as a Telegram callback_query on the
     kai-scheduler poll loop, which forwards it to
     /mode_lock/telegram_action_internal → entry updated to approved_once /
     approved_session / denied.
  6. Hook reads approved → exits 0 (tool retries transparently from Claude's
     side). approved_once is consumed on first poll-read; approved_session
     stays valid until expires_at.

AR-5.x (KAI-999): the remote approval channel moved from Slack (retired, AR-5)
to Telegram. The Slack callback + helpers are kept dormant for the eventual
HTTP cutover but are no longer used to post requests. See memory
feedback_mode_lock_approval_telegram.

Storage: JSON file at /vault/00_System/mode_lock_approvals.json with fcntl
flock for concurrency safety. Pattern matches clarification_store.py.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import socket
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx as _mlhx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from safe_http import safe_json
# KAI-1004: the approval-card send + its token redaction moved into the notify()
# gateway; the raw-body redact() call here is retired. _mlhx/safe_json remain in use
# for editMessageText (in-place card edit on decision).

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Config ───────────────────────────────────────────────────────────────────

STORE_PATH = Path(os.environ.get(
    "MODE_LOCK_STORE_PATH",
    "/vault/00_System/mode_lock_approvals.json",
))
# Generous default window: the remote (Telegram) path is async and Leo may be
# away/busy, so a pending request stays tappable for an hour rather than the
# old ~5-min Slack window (KAI-999).
DEFAULT_REQUEST_TTL_S = int(os.environ.get("MODE_LOCK_REQUEST_TTL_S", "3600"))
DEFAULT_SESSION_TTL_S = int(os.environ.get("MODE_LOCK_SESSION_TTL_S", "3600"))
SLACK_CHANNEL = os.environ.get("MODE_LOCK_SLACK_CHANNEL", "#devops")

TELEGRAM_API = "https://api.telegram.org"


# ── Telegram helpers (remote approval channel — AR-5.x/KAI-999) ──────────────

def _telegram_token() -> str:
    p = Path("/run/secrets/telegram_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _telegram_chat_id() -> int | None:
    """Destination chat for the approval prompt: explicit override, else the
    first entry of the Telegram allowlist (Leo's chat). None → cannot post."""
    override = os.environ.get("MODE_LOCK_TELEGRAM_CHAT_ID")
    if override:
        try:
            return int(override.strip())
        except ValueError:
            pass
    p = Path("/run/secrets/telegram_allowed_chat_ids")
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                return int(line)
            except ValueError:
                continue
    return None


# ── Slack helpers (reused pattern from routes/slack.py) ──────────────────────

def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _slack_signing_secret() -> str:
    p = Path("/run/secrets/slack_signing_secret")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_SIGNING_SECRET", "")


def _verify_slack_sig(raw_body: bytes, ts: str, sig: str) -> bool:
    secret = _slack_signing_secret()
    if not secret:
        logger.warning("mode_lock: SLACK_SIGNING_SECRET unset — skipping verification")
        return True
    base = f"v0:{ts}:{raw_body.decode('utf-8', errors='replace')}".encode()
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _slack_api(method: str, payload: dict) -> dict:
    token = _slack_token()
    if not token:
        return {"ok": False, "error": "no_slack_token"}
    try:
        r = _mlhx.post(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10,
        )
        return safe_json(r)
    except Exception as e:
        logger.exception("mode_lock: slack %s failed: %s", method, e)
        return {"ok": False, "error": str(e)}


# ── Storage (atomic write-rename + fcntl flock) ──────────────────────────────

class StoreError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _empty_store() -> dict:
    return {"version": 1, "requests": {}, "sessions": {}}


def _load_locked(path: Path):
    """Open store with exclusive flock; return (file_handle, data). Caller MUST
    close the handle to release the lock. Use _store_session() context manager."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")  # a+ creates if missing, allows read+append
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        fh.close()
        raise
    fh.seek(0)
    raw = fh.read()
    if not raw.strip():
        data = _empty_store()
    else:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            fh.close()
            raise StoreError(f"corrupt store at {path}: {e}") from e
    return fh, data


def _write_locked(fh, path: Path, data: dict) -> None:
    """Atomic write-rename while holding the lock on fh.

    We rename in a tmp file then mv over the original. The lock on the
    original handle is released after rename (the inode replacement is the
    canonical pattern for this)."""
    tmp = tempfile.NamedTemporaryFile(
        "w", delete=False,
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
    )
    try:
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
        raise
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


class _store_session:
    """Context manager: with _store_session() as (data, save): ..."""
    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self.fh = None
        self.data = None
        self.dirty = False
    def __enter__(self):
        self.fh, self.data = _load_locked(self.path)
        def save():
            self.dirty = True
        return self.data, save
    def __exit__(self, exc_type, exc, tb):
        if exc_type is None and self.dirty:
            _write_locked(self.fh, self.path, self.data)
        else:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.fh.close()


# ── Expiry / lookups ──────────────────────────────────────────────────────────

def _expire_in_place(data: dict) -> None:
    """Mutates data: flips pending → expired past expires_at, removes expired
    sessions. Called inside the locked section."""
    now = _now()
    for rid, entry in data.get("requests", {}).items():
        if entry.get("status") == "pending":
            exp = _parse_iso(entry.get("expires_at"))
            if exp and now >= exp:
                entry["status"] = "expired"
                entry["decision_at"] = _iso(now)
                entry["decision_kind"] = "expired"
    keep = {}
    for rid, sess in data.get("sessions", {}).items():
        exp = _parse_iso(sess.get("expires_at"))
        if exp and now < exp:
            keep[rid] = sess
    data["sessions"] = keep


def _active_session_for(data: dict, requester: str) -> dict | None:
    now = _now()
    for sess in data.get("sessions", {}).values():
        if sess.get("requester") != requester:
            continue
        exp = _parse_iso(sess.get("expires_at"))
        if exp and now < exp:
            return sess
    return None


# ── Slack message rendering ───────────────────────────────────────────────────

def _build_blocks(request_id: str, tool: str, target: str, reason: str,
                  requester: str) -> list:
    target_display = target or "(none)"
    if len(target_display) > 200:
        target_display = target_display[:197] + "..."
    reason_display = reason or "(no reason provided)"
    if len(reason_display) > 600:
        reason_display = reason_display[:597] + "..."
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔐 KAI Mode Lock — Approval"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Tool:*\n`{tool}`"},
                {"type": "mrkdwn", "text": f"*Requester:*\n`{requester}`"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Target:*\n`{target_display}`"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reason:*\n{reason_display}"},
        },
        {
            "type": "actions",
            "block_id": f"mode_lock:{request_id}",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "Allow once"},
                    "action_id": "mode_lock_allow_once",
                    "value": request_id,
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "action_id": "mode_lock_deny",
                    "value": request_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Allow session (1h)"},
                    "action_id": "mode_lock_allow_session",
                    "value": request_id,
                },
            ],
        },
    ]


def _post_slack_request(request_id: str, tool: str, target: str, reason: str,
                        requester: str) -> dict:
    payload = {
        "channel": SLACK_CHANNEL,
        "text": f"KAI Mode Lock approval requested: {tool} on {target or '?'}",
        "blocks": _build_blocks(request_id, tool, target, reason, requester),
    }
    return _slack_api("chat.postMessage", payload)


def _update_slack_message(channel: str, ts: str, header_text: str,
                          summary: str) -> None:
    if not channel or not ts:
        return
    _slack_api("chat.update", {
        "channel": channel,
        "ts": ts,
        "text": summary,
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": summary},
            },
        ],
    })


# ── Telegram message rendering (remote approval channel) ─────────────────────

def _telegram_text(tool: str, target: str, reason: str, requester: str) -> str:
    target_display = target or "(none)"
    if len(target_display) > 300:
        target_display = target_display[:297] + "..."
    reason_display = reason or "(no reason provided)"
    if len(reason_display) > 600:
        reason_display = reason_display[:597] + "..."
    return (
        "🔐 *KAI Mode Lock — Approval*\n"
        f"*Tool:* `{tool}`\n"
        f"*Requester:* `{requester}`\n"
        f"*Target:* `{target_display}`\n"
        f"*Reason:* {reason_display}"
    )


def _post_telegram_request(request_id: str, tool: str, target: str, reason: str,
                           requester: str) -> dict:
    """Post the approval prompt with an inline keyboard. callback_data is
    `modelock:{once,deny,session}:{request_id}` — handled by the kai-scheduler
    Telegram poll loop, which forwards to /mode_lock/telegram_action_internal."""
    token = _telegram_token()
    chat_id = _telegram_chat_id()
    if not token or chat_id is None:
        return {"ok": False, "error": "no_telegram_token_or_chat"}
    keyboard = [
        [
            {"text": "✅ Allow once", "callback_data": f"modelock:once:{request_id}"},
            {"text": "❌ Deny", "callback_data": f"modelock:deny:{request_id}"},
        ],
        [
            {"text": "🔓 Allow session (1h)", "callback_data": f"modelock:session:{request_id}"},
        ],
    ]
    # KAI-1004: the unlock approval card is a decision only Leo can give — it routes
    # to Telegram via the single gateway transport (reason="mode_lock"), which owns
    # the raw send, the Rule-A log and the reality gate. send_message() returns the
    # message_id so the card can still be edited in place on decision (KAI-999).
    from notify_gateway import send_message
    res = send_message(
        chat_id,
        _telegram_text(tool, target, reason, requester),
        reason="mode_lock",
        parse_mode="Markdown",
        reply_markup={"inline_keyboard": keyboard},
    )
    if not res.get("delivered"):
        logger.error("mode_lock: telegram approval card not delivered")
        return {"ok": False, "error": "telegram_rejected"}
    return {"ok": True, "chat_id": chat_id, "message_id": res.get("message_id")}


def _update_telegram_message(chat_id, message_id, text: str) -> None:
    """Edit the original prompt in place to reflect the decision (drops the
    inline keyboard by omitting reply_markup)."""
    if chat_id is None or not message_id:
        return
    token = _telegram_token()
    if not token:
        return
    try:
        _mlhx.post(
            f"{TELEGRAM_API}/bot{token}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as e:
        logger.error("mode_lock: telegram edit failed: %s", type(e).__name__)


# ── Routes ────────────────────────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    tool: str = Field(..., description="Tool name Claude attempted (e.g. Edit)")
    target: str = Field("", description="Target file path or command (if any)")
    reason: str = Field("", description="Reason Claude provided for the write")
    requester: str = Field(
        default_factory=lambda: socket.gethostname(),
        description="Identifier for the requesting machine/session",
    )
    ttl_s: int | None = Field(None, description="Override request TTL")


class ApprovalResponse(BaseModel):
    request_id: str
    status: str
    decision_kind: str | None = None
    expires_at: str | None = None
    session_unlock_active: bool = False


@router.post("/mode_lock/request_approval", response_model=ApprovalResponse)
def request_approval(req: ApprovalRequest) -> ApprovalResponse:
    """Create an approval request. If an active session-unlock exists for this
    requester, returns approved_session immediately (no Slack noise)."""
    if not req.tool:
        raise HTTPException(400, "tool is required")

    now = _now()
    ttl_s = req.ttl_s or DEFAULT_REQUEST_TTL_S
    expires_at = _iso(now + timedelta(seconds=ttl_s))
    request_id = uuid.uuid4().hex[:12]

    with _store_session() as (data, save):
        _expire_in_place(data)
        save()

        sess = _active_session_for(data, req.requester)
        if sess:
            entry = {
                "id": request_id,
                "created_at": _iso(now),
                "expires_at": expires_at,
                "tool": req.tool,
                "target": req.target,
                "reason": req.reason,
                "requester": req.requester,
                "status": "approved_session",
                "decision_at": _iso(now),
                "decision_kind": "session_unlock_active",
                "session_id": sess.get("id"),
                "slack_channel": None,
                "slack_ts": None,
                "telegram_chat_id": None,
                "telegram_message_id": None,
            }
            data["requests"][request_id] = entry
            save()
            return ApprovalResponse(
                request_id=request_id,
                status="approved_session",
                decision_kind="session_unlock_active",
                expires_at=sess.get("expires_at"),
                session_unlock_active=True,
            )

        entry = {
            "id": request_id,
            "created_at": _iso(now),
            "expires_at": expires_at,
            "tool": req.tool,
            "target": req.target,
            "reason": req.reason,
            "requester": req.requester,
            "status": "pending",
            "decision_at": None,
            "decision_kind": None,
            "slack_channel": None,
            "slack_ts": None,
            "telegram_chat_id": None,
            "telegram_message_id": None,
        }
        data["requests"][request_id] = entry
        save()

    # Telegram post happens OUTSIDE the lock to keep the critical section short
    # (AR-5.x/KAI-999 — Slack retired). The remote path is best-effort: if the
    # post fails, the in-session `YES` fast path is still the way in.
    resp = _post_telegram_request(request_id, req.tool, req.target, req.reason,
                                  req.requester)
    if resp.get("ok"):
        with _store_session() as (data, save):
            entry = data["requests"].get(request_id)
            if entry:
                entry["telegram_chat_id"] = resp.get("chat_id")
                entry["telegram_message_id"] = resp.get("message_id")
                save()
    else:
        logger.warning("mode_lock: telegram post failed for %s: %s",
                       request_id, resp.get("error"))

    return ApprovalResponse(
        request_id=request_id,
        status="pending",
        expires_at=expires_at,
    )


@router.get("/mode_lock/approval_status/{request_id}", response_model=ApprovalResponse)
def approval_status(request_id: str, consume: int = 0) -> ApprovalResponse:
    """Poll status of an approval request. If consume=1 and status is
    approved_once, the entry transitions to consumed on this read (single-use).
    Session approvals are not consumed — they remain valid until session
    expires_at."""
    with _store_session() as (data, save):
        _expire_in_place(data)
        save()
        entry = data["requests"].get(request_id)
        if not entry:
            raise HTTPException(404, f"request_id not found: {request_id}")

        status = entry.get("status")
        decision_kind = entry.get("decision_kind")

        if consume and status == "approved_once":
            entry["status"] = "consumed"
            entry["consumed_at"] = _iso(_now())
            save()

        return ApprovalResponse(
            request_id=request_id,
            status=status,
            decision_kind=decision_kind,
            expires_at=entry.get("expires_at"),
            session_unlock_active=bool(_active_session_for(data, entry.get("requester", ""))),
        )


_ACTION_MAP = {
    "once":    ("approved_once",    "allow_once"),
    "deny":    ("denied",           "deny"),
    "session": ("approved_session", "allow_session"),
}

# Slack action_id → canonical action key (dormant Slack cutover path).
_SLACK_ACTION_MAP = {
    "mode_lock_allow_once":    "once",
    "mode_lock_deny":          "deny",
    "mode_lock_allow_session": "session",
}


def _decision_render(new_status: str, user: str, requester: str) -> tuple[str, str]:
    """(header, summary) text for a decided request — surface-agnostic. Total by
    construction: the live callers only ever pass once/deny/session (guarded by
    the already_decided/not-ok early returns), but an unexpected status must
    degrade to a generic line, never raise a KeyError that surfaces as a 500."""
    summary = {
        "approved_once":    f"✅ *Allowed once* by {user} — tool will retry now.",
        "denied":           f"❌ *Denied* by {user}.",
        "approved_session": f"🔓 *Session unlocked (1h)* by {user} — all writes from `{requester}` auto-approved.",
    }.get(new_status, f"🔐 *{new_status}* by {user}.")
    header = {
        "approved_once":    "🔐 KAI Mode Lock — Approved (once)",
        "denied":           "🔐 KAI Mode Lock — Denied",
        "approved_session": "🔓 KAI Mode Lock — Session unlocked",
    }.get(new_status, "🔐 KAI Mode Lock — Updated")
    return header, summary


def _apply_decision(request_id: str, action: str, user: str) -> dict:
    """Apply a decision to a pending request. Surface-agnostic state mutation
    shared by the Slack callback and the Telegram callback forward. `action`
    is one of once / deny / session. On a fresh decision the result carries the
    entry `snapshot` so the caller can render the surface-specific update."""
    if not request_id:
        return {"ok": False, "error": "no request_id"}
    if action not in _ACTION_MAP:
        return {"ok": False, "error": f"unknown action: {action}"}
    new_status, decision_kind = _ACTION_MAP[action]

    now = _now()
    with _store_session() as (data, save):
        _expire_in_place(data)
        save()
        entry = data["requests"].get(request_id)
        if not entry:
            return {"ok": False, "error": "request not found"}

        if entry.get("status") != "pending":
            return {
                "ok": True,
                "already_decided": True,
                "status": entry.get("status"),
                "snapshot": dict(entry),
            }

        entry["status"] = new_status
        entry["decision_at"] = _iso(now)
        entry["decision_kind"] = decision_kind
        entry["decided_by"] = user

        if new_status == "approved_session":
            session_id = uuid.uuid4().hex[:12]
            session_expires = _iso(now + timedelta(seconds=DEFAULT_SESSION_TTL_S))
            data["sessions"][session_id] = {
                "id": session_id,
                "requester": entry.get("requester"),
                "created_at": _iso(now),
                "expires_at": session_expires,
                "origin_request_id": request_id,
                "decided_by": user,
            }
            entry["session_id"] = session_id
            entry["session_expires_at"] = session_expires
        save()
        snapshot = dict(entry)

    return {"ok": True, "status": new_status, "request_id": request_id,
            "snapshot": snapshot}


def _apply_block_actions(payload: dict) -> dict:
    """Apply a Slack block_actions payload to mode_lock state (dormant — the
    live remote channel is Telegram as of AR-5.x/KAI-999; kept for the eventual
    Slack HTTP cutover)."""
    if payload.get("type") != "block_actions":
        return {"ok": True, "ignored": payload.get("type")}

    actions = payload.get("actions") or []
    if not actions:
        return {"ok": True, "ignored": "no actions"}

    action = actions[0]
    action_id = action.get("action_id", "")
    request_id = action.get("value", "")
    user = (payload.get("user") or {}).get("name", "unknown")
    channel = (payload.get("channel") or {}).get("id")
    message_ts = (payload.get("message") or {}).get("ts")

    if action_id not in _SLACK_ACTION_MAP:
        return {"ok": False, "error": f"unknown action_id: {action_id}"}

    result = _apply_decision(request_id, _SLACK_ACTION_MAP[action_id], user)
    if not result.get("ok") or result.get("already_decided"):
        return result

    snapshot = result["snapshot"]
    header, summary = _decision_render(result["status"], user,
                                       snapshot.get("requester"))
    _update_slack_message(channel or snapshot.get("slack_channel"),
                          message_ts or snapshot.get("slack_ts"),
                          header, summary)
    return {"ok": True, "status": result["status"], "request_id": request_id}


@router.post("/mode_lock/slack_callback")
async def slack_callback(request: Request):
    """Slack interactivity endpoint (HTTP mode). Receives block_actions payload
    as form-urlencoded `payload=<json>`. Signature-verified per Slack convention.
    Unused while app runs in Socket Mode — kept for the eventual HTTP cutover."""
    raw = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_sig(raw, ts, sig):
        raise HTTPException(403, "Invalid Slack signature")

    form = parse_qs(raw.decode("utf-8", errors="replace"))
    payload_raw = (form.get("payload") or [""])[0]
    if not payload_raw:
        raise HTTPException(400, "missing payload")
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "payload not JSON")
    return _apply_block_actions(payload)


@router.post("/mode_lock/slack_action_internal")
async def slack_action_internal(request: Request):
    """Forwarded block_actions payload from kai-slack-bot (Socket Mode).
    No signature check — endpoint not exposed publicly (nginx port-8080 webhook
    block omits it; port-80 has basic auth). Bot reaches via docker network."""
    body = await request.json()
    payload = body.get("payload") if isinstance(body, dict) else None
    if not isinstance(payload, dict):
        raise HTTPException(400, "missing payload object")
    return _apply_block_actions(payload)


class TelegramAction(BaseModel):
    request_id: str = Field(..., description="mode_lock request id")
    action: str = Field(..., description="once | deny | session")
    user: str = Field("leo", description="Telegram username that tapped")


@router.post("/mode_lock/telegram_action_internal")
def telegram_action_internal(body: TelegramAction):
    """Forwarded Telegram callback_query decision from the kai-scheduler poll
    loop (AR-5.x/KAI-999). No signature check — endpoint is docker-network
    internal only (nginx port-8080 webhook block omits it; port-80 has basic
    auth). The scheduler enforces the Telegram chat allowlist before forwarding,
    so a tap that reaches here is already from an approved chat."""
    result = _apply_decision(body.request_id, body.action, body.user)
    if not result.get("ok"):
        return result
    if result.get("already_decided"):
        return {"ok": True, "already_decided": True, "status": result.get("status"),
                "request_id": body.request_id}

    snapshot = result["snapshot"]
    header, summary = _decision_render(result["status"], body.user,
                                       snapshot.get("requester"))
    _update_telegram_message(snapshot.get("telegram_chat_id"),
                             snapshot.get("telegram_message_id"),
                             f"{header}\n{summary}")
    return {"ok": True, "status": result["status"], "request_id": body.request_id}


@router.get("/mode_lock/sessions")
def list_sessions():
    """Inspection endpoint — current active session-unlocks."""
    with _store_session() as (data, save):
        _expire_in_place(data)
        save()
        return {
            "ok": True,
            "sessions": list(data.get("sessions", {}).values()),
        }


@router.post("/mode_lock/sessions/revoke")
def revoke_session(body: dict):
    """Revoke active session-unlocks for a requester (or all if requester
    not specified). Useful for: 'I'm done with this session, lock me down.'"""
    requester = body.get("requester")
    with _store_session() as (data, save):
        before = len(data.get("sessions", {}))
        if requester:
            data["sessions"] = {
                k: v for k, v in data.get("sessions", {}).items()
                if v.get("requester") != requester
            }
        else:
            data["sessions"] = {}
        after = len(data.get("sessions", {}))
        save()
        return {"ok": True, "revoked": before - after, "remaining": after}

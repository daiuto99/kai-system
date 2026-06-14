"""routes/mode_lock.py — KAI-mediated approval prompt (M2-1.C).

Replaces the binary YES unlock with a contextual, Leo-in-the-loop approval flow:

  1. Gate hook on Mac denies a write tool.
  2. Hook POSTs /mode_lock/request_approval with {tool, target, reason}.
  3. This worker route persists the request, posts an interactive Slack
     message to Leo with 3 buttons (Allow once / Deny / Allow session 1h),
     returns request_id.
  4. Hook polls GET /mode_lock/approval_status/{request_id}?consume=1 until
     it sees a terminal status or timeout.
  5. Leo taps a button → Slack POSTs /mode_lock/slack_callback → entry
     updated to approved_once / approved_session / denied.
  6. Hook reads approved → exits 0 (tool retries transparently from Claude's
     side). approved_once is consumed on first poll-read; approved_session
     stays valid until expires_at.

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
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx as _mlhx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from safe_http import safe_json

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Config ───────────────────────────────────────────────────────────────────

STORE_PATH = Path(os.environ.get(
    "MODE_LOCK_STORE_PATH",
    "/vault/00_System/mode_lock_approvals.json",
))
DEFAULT_REQUEST_TTL_S = int(os.environ.get("MODE_LOCK_REQUEST_TTL_S", "300"))
DEFAULT_SESSION_TTL_S = int(os.environ.get("MODE_LOCK_SESSION_TTL_S", "3600"))
SLACK_CHANNEL = os.environ.get("MODE_LOCK_SLACK_CHANNEL", "#devops")


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
        }
        data["requests"][request_id] = entry
        save()

    # Slack post happens OUTSIDE the lock to keep the critical section short.
    resp = _post_slack_request(request_id, req.tool, req.target, req.reason,
                               req.requester)
    if resp.get("ok"):
        with _store_session() as (data, save):
            entry = data["requests"].get(request_id)
            if entry:
                entry["slack_channel"] = resp.get("channel")
                entry["slack_ts"] = resp.get("ts")
                save()
    else:
        logger.warning("mode_lock: slack post failed for %s: %s",
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


@router.post("/mode_lock/slack_callback")
async def slack_callback(request: Request):
    """Slack interactivity endpoint. Receives block_actions payload as
    form-urlencoded `payload=<json>`. Signature-verified per Slack convention."""
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

    if not request_id:
        return {"ok": False, "error": "no request_id"}

    decision_map = {
        "mode_lock_allow_once":    ("approved_once",    "allow_once"),
        "mode_lock_deny":          ("denied",           "deny"),
        "mode_lock_allow_session": ("approved_session", "allow_session"),
    }
    if action_id not in decision_map:
        return {"ok": False, "error": f"unknown action_id: {action_id}"}
    new_status, decision_kind = decision_map[action_id]

    now = _now()
    with _store_session() as (data, save):
        _expire_in_place(data)
        save()
        entry = data["requests"].get(request_id)
        if not entry:
            return {"ok": False, "error": "request not found"}

        if entry.get("status") != "pending":
            # Already decided — idempotent no-op
            return {
                "ok": True,
                "already_decided": True,
                "status": entry.get("status"),
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

    # Update Slack message OUTSIDE lock
    decided_summary = {
        "approved_once":    f"✅ *Allowed once* by {user} — tool will retry now.",
        "denied":           f"❌ *Denied* by {user}.",
        "approved_session": f"🔓 *Session unlocked (1h)* by {user} — all writes from `{snapshot.get('requester')}` auto-approved.",
    }[new_status]
    header = {
        "approved_once":    "🔐 KAI Mode Lock — Approved (once)",
        "denied":           "🔐 KAI Mode Lock — Denied",
        "approved_session": "🔐 KAI Mode Lock — Session unlocked",
    }[new_status]
    _update_slack_message(channel or snapshot.get("slack_channel"),
                          message_ts or snapshot.get("slack_ts"),
                          header, decided_summary)

    return {"ok": True, "status": new_status, "request_id": request_id}


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

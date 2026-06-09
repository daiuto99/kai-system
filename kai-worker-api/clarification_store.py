"""Pending-clarification state store for Sprint A Slice 2b.

When the routing engine surfaces clarifications_needed (e.g. "which blog?"),
we cannot dispatch yet. This module persists the half-built plan keyed by a
UUID so the conversation can resume when Leo replies.

Storage: a single JSON file at vault/00_System/sprint_a_pending.json.
Concurrency: atomic write-rename. Single-writer is the worker; no locking.

Entry shape:
    {
        "id":              "<uuid4>",
        "created_at":      "<iso8601>",
        "updated_at":      "<iso8601>",
        "channel":         "slack" | "telegram" | "web",
        "origin_chat_id":  "<channel-specific chat/user id>",
        "slack_thread_ts": "<ts>" | null,   # only set for slack
        "telegram_msg_id": <int> | null,    # only set for telegram, the bot's prompt msg
        "parsed_intent":   {...},           # output of intent_parser.parse_intent
        "dispatch_plan":   {...},           # output of routing_engine.build_dispatch_plan
        "status":          "pending" | "resolved" | "expired" | "failed",
        "retries":         0,               # bumps on nonsense replies
        "resolution":      {...} | null,    # set when resolved: {choice, replied_at}
        "expired_reason":  str | null
    }

Status transitions: pending → resolved | expired | failed.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("/vault/00_System/sprint_a_pending.json")
DEFAULT_EXPIRY_HOURS = 24
MAX_RETRIES = 1   # retry-once policy from Slice 2b spec


class ClarificationStoreError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ClarificationStoreError(f"corrupt pending store at {path}: {e}") from e
    data.setdefault("version", 1)
    data.setdefault("entries", {})
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pending_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_pending(
    parsed_intent: dict,
    dispatch_plan: dict,
    channel: str,
    origin_chat_id: str,
    captured_content: dict | None = None,
    slack_thread_ts: str | None = None,
    telegram_msg_id: int | None = None,
    store_path: Path = DEFAULT_PATH,
) -> dict:
    """Create a pending row. Returns the full entry (including generated id).

    captured_content carries the original message/url/og data so the dispatch
    step can fire without re-fetching anything. Shape:
        {"original_message": str, "url": str|None,
         "og_title": str, "og_description": str}
    """
    if channel not in {"slack", "telegram", "web"}:
        raise ClarificationStoreError(f"invalid channel: {channel}")
    if not dispatch_plan.get("clarifications_needed"):
        raise ClarificationStoreError("plan has no clarifications_needed — nothing to pend")

    entry = {
        "id":               str(uuid.uuid4()),
        "created_at":       _now(),
        "updated_at":       _now(),
        "channel":          channel,
        "origin_chat_id":   origin_chat_id,
        "slack_thread_ts":  slack_thread_ts,
        "telegram_msg_id":  telegram_msg_id,
        "captured_content": captured_content or {},
        "parsed_intent":    parsed_intent,
        "dispatch_plan":   dispatch_plan,
        "status":          "pending",
        "retries":         0,
        "resolution":      None,
        "expired_reason":  None,
    }

    data = _load(store_path)
    data["entries"][entry["id"]] = entry
    _save(store_path, data)
    return entry


def get_pending(pending_id: str, store_path: Path = DEFAULT_PATH) -> dict | None:
    data = _load(store_path)
    return data["entries"].get(pending_id)


def find_by_thread_ts(thread_ts: str, store_path: Path = DEFAULT_PATH) -> dict | None:
    """Lookup the pending row Leo replied to (Slack correlation)."""
    if not thread_ts:
        return None
    data = _load(store_path)
    for entry in data["entries"].values():
        if entry["status"] == "pending" and entry.get("slack_thread_ts") == thread_ts:
            return entry
    return None


def find_latest_pending_for_chat(
    channel: str, origin_chat_id: str, store_path: Path = DEFAULT_PATH
) -> dict | None:
    """Telegram correlation: most recent pending row for a chat_id."""
    data = _load(store_path)
    candidates = [
        e for e in data["entries"].values()
        if e["status"] == "pending"
        and e["channel"] == channel
        and e["origin_chat_id"] == origin_chat_id
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e["created_at"])


def resolve_pending(
    pending_id: str,
    choice: dict,
    store_path: Path = DEFAULT_PATH,
) -> dict:
    """Mark a pending row resolved with Leo's choice. Returns updated entry."""
    data = _load(store_path)
    entry = data["entries"].get(pending_id)
    if not entry:
        raise ClarificationStoreError(f"pending id not found: {pending_id}")
    if entry["status"] != "pending":
        raise ClarificationStoreError(
            f"cannot resolve entry in status={entry['status']}"
        )
    now = _now()
    entry["status"] = "resolved"
    entry["resolved_at"] = now
    entry["updated_at"] = now
    entry["resolution"] = {"choice": choice, "replied_at": now}
    data["entries"][pending_id] = entry
    _save(store_path, data)
    return entry


def bump_retry(pending_id: str, store_path: Path = DEFAULT_PATH) -> dict:
    """Increment retry counter. Caller checks `retries > MAX_RETRIES` to fall back."""
    data = _load(store_path)
    entry = data["entries"].get(pending_id)
    if not entry:
        raise ClarificationStoreError(f"pending id not found: {pending_id}")
    entry["retries"] = entry.get("retries", 0) + 1
    entry["updated_at"] = _now()
    data["entries"][pending_id] = entry
    _save(store_path, data)
    return entry


def mark_failed(
    pending_id: str, reason: str, store_path: Path = DEFAULT_PATH
) -> dict:
    data = _load(store_path)
    entry = data["entries"].get(pending_id)
    if not entry:
        raise ClarificationStoreError(f"pending id not found: {pending_id}")
    entry["status"] = "failed"
    entry["updated_at"] = _now()
    entry["expired_reason"] = reason
    data["entries"][pending_id] = entry
    _save(store_path, data)
    return entry


def expire_stale(
    expiry_hours: int = DEFAULT_EXPIRY_HOURS,
    store_path: Path = DEFAULT_PATH,
    now: datetime | None = None,
) -> list[dict]:
    """Move pending rows older than expiry_hours to status='expired'.

    Returns the entries that were expired (caller can hand them to auto-park).
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=expiry_hours)
    data = _load(store_path)
    expired: list[dict] = []
    dirty = False
    for entry in data["entries"].values():
        if entry["status"] != "pending":
            continue
        created = datetime.fromisoformat(entry["created_at"])
        if created < cutoff:
            entry["status"] = "expired"
            entry["updated_at"] = _now()
            entry["expired_reason"] = f"no reply within {expiry_hours}h"
            expired.append(entry)
            dirty = True
    if dirty:
        _save(store_path, data)
    return expired


def list_pending(store_path: Path = DEFAULT_PATH) -> list[dict]:
    data = _load(store_path)
    return [e for e in data["entries"].values() if e["status"] == "pending"]

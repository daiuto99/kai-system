"""Clarification surface for Sprint A Slice 2b.

Reads a pending row from clarification_store and posts a channel-appropriate
prompt asking Leo to pick. Slack uses Block Kit buttons; Telegram uses an
inline keyboard. Web (dashboard) is not posted from here — the dashboard reads
pending rows directly.

Idempotent: if the pending row already has surface metadata (slack_thread_ts
populated post-create, or telegram_msg_id), ask() is a no-op and returns the
existing surface descriptor.

ask() returns: {"ok": bool, "channel": str, "skipped": bool, "detail": str}
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import clarification_store as store
# KAI-1004: the raw Telegram send (and its token redaction) moved into the notify()
# gateway; this module no longer imports httpx or redact directly.

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"

# Action id namespace: "sprint_a_clarify:<pending_id>:<field>:<choice>"
ACTION_PREFIX = "sprint_a_clarify"


class ClarificationSurfaceError(Exception):
    pass


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


def _telegram_token() -> str:
    p = Path("/run/secrets/telegram_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("TELEGRAM_BOT_TOKEN", "")


def ask(pending_id: str, store_path: Path = store.DEFAULT_PATH) -> dict:
    """Post the clarification prompt to the original channel. Idempotent."""
    entry = store.get_pending(pending_id, store_path=store_path)
    if not entry:
        raise ClarificationSurfaceError(f"no pending entry for id {pending_id}")
    if entry["status"] != "pending":
        return {"ok": False, "channel": entry["channel"], "skipped": True,
                "detail": f"entry status={entry['status']}, not pending"}

    clarifications = entry["dispatch_plan"].get("clarifications_needed") or []
    if not clarifications:
        return {"ok": False, "channel": entry["channel"], "skipped": True,
                "detail": "no clarifications_needed on plan"}

    # Slice 2b handles one clarification per round. Multi-question support is Slice 3.
    clar = clarifications[0]

    channel = entry["channel"]
    if channel == "slack":
        return _ask_slack(entry, clar, store_path)
    if channel == "telegram":
        return _ask_telegram(entry, clar, store_path)
    if channel == "web":
        return {"ok": True, "channel": "web", "skipped": True,
                "detail": "web surface is the dashboard — no push needed"}
    raise ClarificationSurfaceError(f"unknown channel: {channel}")


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

def _ask_slack(entry: dict, clar: dict, store_path: Path) -> dict:
    # AR-5.3: Slack retired (AR-5) — dormant no-op. Sprint-A clarifications are
    # asked over Telegram; this Slack branch is no longer used.
    return {"ok": True, "channel": "slack", "skipped": True,
            "detail": "retired (AR-5)"}


def _slack_blocks(pending_id: str, clar: dict) -> list[dict]:
    """Block Kit: prompt + one button per option. Default option is highlighted."""
    options = clar.get("options") or []
    default = clar.get("default")
    field = clar.get("field", "choice")

    blocks: list[dict] = [
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"*{clar.get('prompt', 'Pick one:')}*"}}
    ]

    if not options:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_(no options configured — reply in thread to pick)_"},
        })
        return blocks

    elements = []
    for opt in options[:5]:  # Slack actions block caps at 5 buttons per row
        button: dict = {
            "type": "button",
            "text": {"type": "plain_text", "text": str(opt)},
            "value": str(opt),
            "action_id": f"{ACTION_PREFIX}:{pending_id}:{field}:{opt}",
        }
        if default and opt == default:
            button["style"] = "primary"
        elements.append(button)

    blocks.append({"type": "actions", "elements": elements})

    if len(options) > 5:
        extras = ", ".join(str(o) for o in options[5:])
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn",
                          "text": f"_or reply in thread with one of:_ {extras}"}],
        })
    return blocks


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _ask_telegram(entry: dict, clar: dict, store_path: Path) -> dict:
    if entry.get("surface_posted_at"):
        return {"ok": True, "channel": "telegram", "skipped": True,
                "detail": "already posted"}

    token = _telegram_token()
    if not token:
        raise ClarificationSurfaceError("telegram_bot_token unavailable")

    options = clar.get("options") or []
    keyboard = _telegram_keyboard(entry["id"], clar)

    text_lines = [clar.get("prompt", "Pick one:")]
    for i, opt in enumerate(options, 1):
        marker = " ←" if clar.get("default") == opt else ""
        text_lines.append(f"{i}. {opt}{marker}")
    text = "\n".join(text_lines)

    # KAI-1004: a clarification is a decision KAI needs from Leo — it routes to
    # Telegram via the single gateway transport (reason="clarify"), which owns the
    # raw send, the Rule-A log and the reality gate. send_message() returns the
    # message_id so the pending surface can still be tracked/edited.
    from notify_gateway import send_message
    reply_markup = {"inline_keyboard": keyboard} if keyboard else None
    res = send_message(int(entry["origin_chat_id"]), text,
                       reason="clarify", reply_markup=reply_markup)
    if not res.get("delivered"):
        logger.error("telegram clarify post failed (not delivered)")
        return {"ok": False, "channel": "telegram", "skipped": False,
                "detail": "not delivered to Telegram"}

    msg_id = res.get("message_id")
    _mark_surface_posted(entry["id"], {
        "surface_posted_at": store._now(),
        "telegram_msg_id": msg_id,
    }, store_path)
    return {"ok": True, "channel": "telegram", "skipped": False,
            "detail": f"posted msg_id={msg_id}"}


def _telegram_keyboard(pending_id: str, clar: dict) -> list[list[dict]]:
    """One button per option, up to 3 per row. callback_data carries id+field+choice."""
    options = clar.get("options") or []
    field = clar.get("field", "choice")
    rows: list[list[dict]] = []
    row: list[dict] = []
    for opt in options:
        cb = f"{ACTION_PREFIX}:{pending_id}:{field}:{opt}"
        # Telegram callback_data max 64 bytes — truncate option if it would blow the cap.
        if len(cb.encode("utf-8")) > 64:
            cb = cb[:60] + "_..."
        row.append({"text": str(opt), "callback_data": cb})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Internal — mutate the pending row with surface metadata
# ---------------------------------------------------------------------------

def _mark_surface_posted(pending_id: str, fields: dict, store_path: Path) -> None:
    data = store._load(store_path)
    entry = data["entries"].get(pending_id)
    if not entry:
        return
    entry.update(fields)
    entry["updated_at"] = store._now()
    data["entries"][pending_id] = entry
    store._save(store_path, data)


# ---------------------------------------------------------------------------
# Callback-id helpers (used by webhook handlers in Slice 3 / Slice 2b wiring)
# ---------------------------------------------------------------------------

def parse_callback(action_id_or_data: str) -> dict | None:
    """Parse our Slack action_id or Telegram callback_data.

    Returns {"pending_id", "field", "choice"} or None if not ours.
    """
    if not action_id_or_data or not action_id_or_data.startswith(ACTION_PREFIX + ":"):
        return None
    parts = action_id_or_data.split(":", 3)
    if len(parts) != 4:
        return None
    _, pending_id, field, choice = parts
    return {"pending_id": pending_id, "field": field, "choice": choice}

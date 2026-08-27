"""Sprint A Slice 4 — pure resolution handlers.

Three entry points:

- handle_clarification_choice(pending_id, field, value) — used by the inbound clarification handler
  button click and Telegram callback_query. Resumes the plan and dispatches.

- handle_freetext_reply(pending_id, raw_text) — used by inbound thread replies
  and Telegram free-text in a freshness window. validate_choice on raw_text;
  on hit, resume+dispatch; on miss, bump_retry and (if past retry limit)
  fall back to capture.

- expire_and_notify(expiry_hours, notifier=None) — used by the hourly
  scheduler tick. Sweeps stale rows, logs an audit row per expiry, optionally
  calls notifier(summary_text) once if anything expired.

All three return a structured result dict so the calling route layer can
post the right reply to the right channel without re-doing the work.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import clarification_store as store
import routing_engine
import dispatch as dispatcher

logger = logging.getLogger(__name__)


def handle_clarification_choice(
    pending_id: str,
    field: str,
    value: str,
    *,
    store_path: Path | None = None,
    council_client=None,
    worker_client=None,
) -> dict:
    if store_path is None:
        store_path = store.DEFAULT_PATH
    """Button-click / callback_query path. Resumes plan, dispatches.

    Returns: {"ok", "status", "reply_text", "pending_id", "dispatch_result"}.
    status ∈ {"resolved", "already_resolved", "not_found", "dispatch_error"}.
    """
    entry = store.get_pending(pending_id, store_path=store_path)
    if not entry:
        return _err("not_found", f"No pending clarification with id {pending_id}.")
    if entry["status"] != "pending":
        return {"ok": True, "status": "already_resolved",
                "pending_id": pending_id,
                "reply_text": f"_(already {entry['status']})_",
                "dispatch_result": None}

    choice = {"field": field, "value": value}
    plan = routing_engine.resume(entry, choice)
    if not plan.get("ok_to_dispatch"):
        return _err("dispatch_error",
                    "Choice accepted but plan still not dispatchable.",
                    pending_id=pending_id, plan=plan)

    store.resolve_pending(pending_id, choice, store_path=store_path)

    result = dispatcher.dispatch(
        plan,
        entry.get("captured_content") or {},
        entry["parsed_intent"],
        council_client=council_client,
        worker_client=worker_client,
    )
    return {
        "ok": result["ok"],
        "status": "resolved" if result["ok"] else "dispatch_error",
        "pending_id": pending_id,
        "reply_text": _format_dispatch_reply(result, value),
        "dispatch_result": result,
    }


def handle_freetext_reply(
    pending_id: str,
    raw_text: str,
    *,
    store_path: Path | None = None,
    council_client=None,
    worker_client=None,
) -> dict:
    if store_path is None:
        store_path = store.DEFAULT_PATH
    """Thread-reply / free-text path. Validates against the active clar, then
    either resumes + dispatches, retries (max once), or falls back to capture.

    Returns: same shape as handle_clarification_choice plus status ∈
    {"resolved", "retry_requested", "fallback_capture", "not_found",
    "already_resolved", "dispatch_error"}.
    """
    entry = store.get_pending(pending_id, store_path=store_path)
    if not entry:
        return _err("not_found", f"No pending clarification with id {pending_id}.")
    if entry["status"] != "pending":
        return {"ok": True, "status": "already_resolved", "pending_id": pending_id,
                "reply_text": f"_(already {entry['status']})_",
                "dispatch_result": None}

    clarifications = entry["dispatch_plan"].get("clarifications_needed") or []
    if not clarifications:
        return _err("dispatch_error", "Pending entry has no active clarification.",
                    pending_id=pending_id)
    clar = clarifications[0]

    canonical = routing_engine.validate_choice(clar, raw_text)
    if canonical is not None:
        return handle_clarification_choice(
            pending_id, clar.get("field", "choice"), canonical,
            store_path=store_path,
            council_client=council_client, worker_client=worker_client,
        )

    bumped = store.bump_retry(pending_id, store_path=store_path)
    if bumped["retries"] <= store.MAX_RETRIES:
        opts = ", ".join(str(o) for o in clar.get("options", []))
        return {"ok": True, "status": "retry_requested",
                "pending_id": pending_id,
                "reply_text": (
                    f"I didn't catch that. Reply with one of: {opts}"
                    + (f"  (default: {clar.get('default')})" if clar.get("default") else "")
                ),
                "dispatch_result": None}

    # Past retry limit → fall back to capture, mark entry failed (caller invokes
    # legacy parking_lot.capture if/when appropriate; we don't reach into it here
    # so this stays unit-testable).
    store.mark_failed(pending_id, "retry_limit_exceeded", store_path=store_path)
    return {"ok": True, "status": "fallback_capture",
            "pending_id": pending_id,
            "reply_text": "Couldn't resolve that — saved to the parking lot for now.",
            "dispatch_result": {"handler": "capture", "ok": True,
                                "summary": "Fallback capture after retry limit."}}


def expire_and_notify(
    expiry_hours: int = 24,
    *,
    store_path: Path | None = None,
    notifier: Callable[[str], None] | None = None,
) -> dict:
    if store_path is None:
        store_path = store.DEFAULT_PATH
    """Sweep stale pending rows. Returns {"expired_count", "expired_ids",
    "notified": bool}. If notifier is provided and anything expired, it is
    called exactly once with a short summary string.
    """
    expired = store.expire_stale(expiry_hours=expiry_hours, store_path=store_path)
    if not expired:
        return {"expired_count": 0, "expired_ids": [], "notified": False}

    log_dir = Path("/vault") / "60_Council"
    log_path = log_dir / "sprint_a_dispatch_log.jsonl"
    log_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in expired:
        rows.append({
            "ts":       datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "handler":  "expire",
            "ok":       True,
            "error":    None,
            "target":   entry["dispatch_plan"].get("target", {}),
            "action":   entry["parsed_intent"].get("action"),
            "summary":  f"expired pending id={entry['id']} channel={entry['channel']}",
        })
    try:
        with log_path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    except Exception as e:
        logger.warning("expire log write failed: %s", e)

    summary = f"Sprint A: {len(expired)} clarification(s) expired after {expiry_hours}h, auto-parked."
    notified = False
    if notifier is not None:
        try:
            notifier(summary)
            notified = True
        except Exception as e:
            logger.warning("expire notifier failed: %s", e)

    return {"expired_count": len(expired),
            "expired_ids": [e["id"] for e in expired],
            "notified": notified,
            "summary": summary}


def _format_dispatch_reply(result: dict, value: str) -> str:
    if not result.get("ok"):
        return f"Couldn't finish: {result.get('error') or 'unknown error'}."
    summary = result.get("summary") or ""
    return f"Got it ({value}). {summary}"


def _err(status: str, msg: str, **extras) -> dict:
    out = {"ok": False, "status": status, "reply_text": msg,
           "dispatch_result": None}
    out.update(extras)
    return out

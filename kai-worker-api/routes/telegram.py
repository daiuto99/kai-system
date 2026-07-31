import logging
import os
import hmac
import hashlib
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
import httpx as _tghttpx
from watchdog import _worker_auth
from safe_http import safe_json
from redact import redact, redact_obj

logger = logging.getLogger(__name__)
router = APIRouter()

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_SECRET = os.environ.get("TELEGRAM_SECRET", "")
COUNCIL_API = os.environ.get("COUNCIL_API_URL", "http://kai-council-api:8002")


def _tg_token() -> str:
    p = Path("/run/secrets/telegram_bot_token")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _redact(e: object) -> str:
    """L18: httpx error text embeds the request URL, which carries the bot
    token (/bot<TOKEN>/...) — and upstream response bodies may reflect it,
    literal or URL-encoded. Never log or return such text unredacted — and
    never log these exceptions with logger.exception, whose traceback tail
    repeats the unredacted message."""
    return redact(e, _tg_token())


def _tg_send(chat_id: int, text: str):
    # KAI-1004: conversational replies route through the single gateway transport
    # (reason="reply"): always deliverable, logged (Rule A), not classified. The
    # gateway owns the raw send and its L18-safe error handling.
    from notify_gateway import send_telegram
    send_telegram(chat_id, text, reason="reply", parse_mode="Markdown")


class TelegramUpdate(BaseModel):
    update_id: int
    message: dict | None = None
    callback_query: dict | None = None


# ---------------------------------------------------------------------------
# Sprint A — clarification correlation
# ---------------------------------------------------------------------------

SPRINT_A_FRESHNESS_S = 600  # 10-minute window for free-text correlation


def _handle_sprint_a_callback(cbq: dict) -> None:
    try:
        import clarification_surface as _surface
        import sprint_a_handlers as _sah
    except ImportError:
        return
    data = cbq.get("data") or ""
    parsed = _surface.parse_callback(data)
    if not parsed:
        return
    result = _sah.handle_clarification_choice(
        parsed["pending_id"], parsed["field"], parsed["choice"],
    )
    chat_id = cbq.get("message", {}).get("chat", {}).get("id")
    if chat_id:
        _tg_send(chat_id, result.get("reply_text") or "_(no reply text)_")

    # Always answer the callback so Telegram clears the button spinner.
    try:
        _tghttpx.post(
            f"{TELEGRAM_API}/bot{_tg_token()}/answerCallbackQuery",
            json={"callback_query_id": cbq.get("id")},
            timeout=10,
        )
    except Exception as e:
        logger.warning("answerCallbackQuery failed: %s", _redact(e))


def _allowed_gate_chat_ids() -> set[str]:
    """Chat ids permitted to resolve council gates (one per line). Defense in
    depth: only Leo's allowed chat receives the buttons, and only that chat may
    resolve a gate even if a callback is otherwise crafted."""
    p = Path("/run/secrets/telegram_allowed_chat_ids")
    if not p.exists():
        return set()
    return {ln.strip() for ln in p.read_text().splitlines() if ln.strip()}


def _handle_gate_callback(cbq: dict) -> None:
    """AR-5.2: Telegram inline approve/reject for a council gate.

    callback_data is `gate:approve:{gate_id}` or `gate:reject:{gate_id}`.
    Resolves the gate by POSTing to kai-council-api /council/gate/{id}/resolve,
    authenticated with the worker credential (same pattern as /message)."""
    data = cbq.get("data") or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "gate" or parts[1] not in ("approve", "reject"):
        return
    action, gate_id = parts[1], parts[2]
    approved = action == "approve"
    chat_id = cbq.get("message", {}).get("chat", {}).get("id")

    # Allowlist guard — only an approved chat may resolve a gate.
    allowed = _allowed_gate_chat_ids()
    if allowed and str(chat_id) not in allowed:
        logger.warning("Gate callback from non-allowed chat %s — ignored", chat_id)
        _answer_callback(cbq.get("id"))
        return

    try:
        r = _tghttpx.post(
            f"{COUNCIL_API}/council/gate/{gate_id}/resolve",
            json={"approved": approved, "notes": "via Telegram", "resolver": "leo"},
            timeout=30,
            auth=_worker_auth(),
        )
        if r.status_code == 200:
            reply = f"{'✅ Approved' if approved else '🛑 Rejected'} gate `{gate_id}`."
        elif r.status_code == 409:
            reply = f"⚠️ Gate `{gate_id}` was already resolved."
        elif r.status_code == 404:
            reply = f"⚠️ Gate `{gate_id}` not found — it may have expired."
        else:
            reply = f"⚠️ Gate resolve failed — HTTP {r.status_code}."
    except Exception as e:
        logger.error("Gate resolve error: %s", _redact(e))
        reply = "⚠️ Could not reach the council to resolve the gate."

    if chat_id:
        _tg_send(chat_id, reply)
    _answer_callback(cbq.get("id"))


def _answer_callback(callback_query_id: object) -> None:
    """Clear the Telegram button spinner after handling a callback."""
    if not callback_query_id:
        return
    try:
        _tghttpx.post(
            f"{TELEGRAM_API}/bot{_tg_token()}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
            timeout=10,
        )
    except Exception as e:
        logger.warning("answerCallbackQuery failed: %s", _redact(e))


def _try_sprint_a_freetext(chat_id: int, text: str) -> bool:
    """Return True if this message resolved (or retried) a recent pending
    clarification; the caller should NOT also send it to the council router."""
    try:
        import clarification_store as _cs
        import sprint_a_handlers as _sah
    except ImportError:
        return False
    entry = _cs.find_latest_pending_for_chat("telegram", str(chat_id))
    if not entry:
        return False
    from datetime import datetime, timezone
    created = datetime.fromisoformat(entry["created_at"])
    age = (datetime.now(timezone.utc) - created).total_seconds()
    if age > SPRINT_A_FRESHNESS_S:
        return False
    result = _sah.handle_freetext_reply(entry["id"], text)
    _tg_send(chat_id, result.get("reply_text") or "_(no reply text)_")
    return True


def _tg_download_file(file_id: str) -> tuple[bytes, str]:
    """Download a Telegram file. Returns (bytes, mime_type)."""
    token = _tg_token()
    r = _tghttpx.get(f"{TELEGRAM_API}/bot{token}/getFile", params={"file_id": file_id}, timeout=10)
    j = safe_json(r)
    file_path = j.get("result", {}).get("file_path")
    if not file_path:
        raise ValueError(f"getFile returned no file_path (status={r.status_code})")
    data = _tghttpx.get(f"{TELEGRAM_API}/file/bot{token}/{file_path}", timeout=30)
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    mime = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "gif": "image/gif", "webp": "image/webp"}.get(ext, "application/octet-stream")
    return data.content, mime


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default="")
):
    """Receive Telegram update, route to KAI council, reply via Telegram."""
    if not TELEGRAM_SECRET:
        raise HTTPException(503, "Telegram webhook not configured — TELEGRAM_SECRET unset")
    if x_telegram_bot_api_secret_token != TELEGRAM_SECRET:
        raise HTTPException(403, "Invalid token")

    body = await request.json()

    # Inline-keyboard button click. AR-5.2 gate approvals use `gate:*`
    # callback data; everything else is a Sprint-A clarification choice.
    cbq = body.get("callback_query")
    if cbq:
        if (cbq.get("data") or "").startswith("gate:"):
            _handle_gate_callback(cbq)
        else:
            _handle_sprint_a_callback(cbq)
        return {"ok": True}

    msg = body.get("message")
    if not msg:
        return {"ok": True}

    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or msg.get("caption") or "").strip()
    username = msg.get("from", {}).get("username", "unknown")

    if not chat_id:
        return {"ok": True}

    if text == "/start":
        _tg_send(chat_id, "*KAI online.* Send me a message and I'll respond.")
        return {"ok": True}

    # Sprint A: if there's a recent pending clarification for this chat (within
    # 10 minutes), treat free-text as the answer before falling through to the
    # council router.
    if text and _try_sprint_a_freetext(chat_id, text):
        return {"ok": True}

    # Detect file attachments
    attachments = []
    doc = msg.get("document")
    photos = msg.get("photo")

    if doc:
        file_id = doc.get("file_id")
        filename = doc.get("file_name", "file")
        try:
            import base64
            file_bytes, mime = _tg_download_file(file_id)
            if mime in ("application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"):
                attachments.append({
                    "type": "document" if mime == "application/pdf" else "image",
                    "media_type": mime,
                    "data": base64.standard_b64encode(file_bytes).decode(),
                    "filename": filename,
                })
                if not text:
                    text = f"[File attached: {filename}]"
        except Exception as e:
            logger.error("Telegram file download error: %s", _redact(e))
            text = text or f"[File: {filename} — could not download]"

    elif photos:
        largest = max(photos, key=lambda p: p.get("file_size", 0))
        try:
            import base64
            file_bytes, mime = _tg_download_file(largest["file_id"])
            attachments.append({
                "type": "image",
                "media_type": mime or "image/jpeg",
                "data": base64.standard_b64encode(file_bytes).decode(),
                "filename": "photo.jpg",
            })
            if not text:
                text = "[Photo attached]"
        except Exception as e:
            logger.error("Telegram photo download error: %s", _redact(e))
            text = text or "[Photo — could not download]"

    if not text and not attachments:
        return {"ok": True}

    logger.info("Telegram msg from @%s (%s): %s attach=%d", username, chat_id, text[:60], len(attachments))

    try:
        payload = {"channel": "kai", "message": text, "user_id": f"telegram:{username}"}
        if attachments:
            payload["attachments"] = attachments
        r = _tghttpx.post(
            "http://kai-council-api:8002/message",
            json=payload,
            timeout=120,
            auth=_worker_auth(),
        )
        r.raise_for_status()
        reply = safe_json(r).get("reply", "No response.")
    except (_tghttpx.ConnectError, _tghttpx.ConnectTimeout, _tghttpx.NetworkError) as e:
        logger.error("Council API unreachable from Telegram: %s", type(e).__name__)
        raise HTTPException(503, "KAI temporarily unavailable — Telegram will retry")
    except _tghttpx.TimeoutException:
        logger.error("Council API timeout from Telegram webhook after 120s")
        reply = ("⚠️ KAI error — the council did not answer within 120s. "
                 "It may still be working; ask again in a minute.")
    except _tghttpx.HTTPStatusError as e:
        logger.error("Council API HTTP %s from Telegram webhook", e.response.status_code)
        reply = f"⚠️ KAI error — the council API returned HTTP {e.response.status_code}."
    except Exception as e:
        logger.error("Council API error from Telegram: %s", type(e).__name__)
        reply = f"⚠️ KAI error — unexpected {type(e).__name__} while contacting the council."

    _tg_send(chat_id, reply)
    return {"ok": True}


@router.get("/telegram/status")
def telegram_status():
    token = _tg_token()
    if not token:
        return {"configured": False, "error": "No telegram_bot_token secret"}
    try:
        r = _tghttpx.get(f"{TELEGRAM_API}/bot{token}/getMe", timeout=10)
        if r.status_code == 200:
            # L18: successful result payloads can reflect the token-bearing
            # request URL just as error bodies can — sanitize before return.
            bot = safe_json(r).get("result", {})
            return {"configured": True, "bot": redact_obj(bot, _tg_token())}
        # L18: the response body may reflect the token-bearing request URL.
        return {"configured": False, "error": _redact(r.text[:200])}
    except Exception as e:
        logger.error("telegram_status: %s", _redact(e))
        return {"configured": False, "error": _redact(e)}


@router.post("/telegram/register-webhook")
def telegram_register_webhook(body: dict):
    token = _tg_token()
    if not token:
        raise HTTPException(500, "No telegram_bot_token secret")
    webhook_url = body.get("url", "https://kai.sonicink.space/api/telegram/webhook")
    try:
        r = _tghttpx.post(
            f"{TELEGRAM_API}/bot{token}/setWebhook",
            json={"url": webhook_url},
            timeout=15,
        )
    except Exception as e:
        # L18: a transport error here would otherwise propagate the
        # token-bearing setWebhook URL into FastAPI/Uvicorn error handling.
        logger.error("telegram register-webhook: %s", _redact(e))
        raise HTTPException(502, f"setWebhook failed: {_redact(e)}")
    resp = safe_json(r, default={"ok": False, "description": "non-json response"})
    # L18: return a sanitized subset — the body may reflect the request URL,
    # in the success `result` field as much as in the error description.
    return {"ok": bool(resp.get("ok")),
            "result": redact_obj(resp.get("result"), token),
            "description": _redact(resp.get("description", ""))}

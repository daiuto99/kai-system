import logging
import os
import hmac
import hashlib
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
import httpx as _tghttpx
from safe_http import safe_json

logger = logging.getLogger(__name__)
router = APIRouter()

TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_SECRET = os.environ.get("TELEGRAM_SECRET", "")


def _tg_token() -> str:
    p = Path("/run/secrets/telegram_bot_token")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _tg_send(chat_id: int, text: str):
    token = _tg_token()
    if not token:
        return
    try:
        _tghttpx.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as e:
        logger.exception("Telegram send error: %s", e)


class TelegramUpdate(BaseModel):
    update_id: int
    message: dict | None = None
    callback_query: dict | None = None


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
    if TELEGRAM_SECRET and x_telegram_bot_api_secret_token != TELEGRAM_SECRET:
        raise HTTPException(403, "Invalid token")

    body = await request.json()
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
            logger.exception("Telegram file download error: %s", e)
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
            logger.exception("Telegram photo download error: %s", e)
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
        )
        r.raise_for_status()
        data = r.json()
        reply = data.get("reply", "No response.")
    except (_tghttpx.ConnectError, _tghttpx.ConnectTimeout, _tghttpx.NetworkError) as e:
        logger.exception("Council API unreachable from Telegram: %s", e)
        raise HTTPException(503, "KAI temporarily unavailable — Telegram will retry")
    except Exception as e:
        logger.exception("Council API error from Telegram: %s", e)
        reply = "KAI encountered an error. Try again in a moment."

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
            bot = safe_json(r).get("result", {})
            return {"configured": True, "bot": bot}
        return {"configured": False, "error": r.text[:200]}
    except Exception as e:
        logger.exception("telegram_status: %s", e)
        return {"configured": False, "error": str(e)}


@router.post("/telegram/register-webhook")
def telegram_register_webhook(body: dict):
    token = _tg_token()
    if not token:
        raise HTTPException(500, "No telegram_bot_token secret")
    webhook_url = body.get("url", "https://kai.sonicink.space/api/telegram/webhook")
    r = _tghttpx.post(
        f"{TELEGRAM_API}/bot{token}/setWebhook",
        json={"url": webhook_url},
        timeout=15,
    )
    return safe_json(r, default={"ok": False, "error": "non-json response"})

import logging
import os
import hmac
import hashlib
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
import httpx as _tghttpx

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
    text = msg.get("text", "").strip()
    username = msg.get("from", {}).get("username", "unknown")

    if not text or not chat_id:
        return {"ok": True}

    if text == "/start":
        _tg_send(chat_id, "*KAI online.* Send me a message and I'll respond.")
        return {"ok": True}

    logger.info("Telegram msg from @%s (%s): %s", username, chat_id, text[:60])

    try:
        r = _tghttpx.post(
            "http://kai-council-api:8002/message",
            json={"channel": "chief", "message": text, "user_id": f"telegram:{username}"},
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        reply = data.get("reply", "No response.")
    except Exception as e:
        logger.exception("Council API error from Telegram: %s", e)
        reply = "KAI is temporarily unavailable. Try again in a moment."

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
            bot = r.json().get("result", {})
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
    return r.json()

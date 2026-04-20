import os
import logging
import re
from pathlib import Path
import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [kai-slack-bot] %(message)s")
log = logging.getLogger(__name__)

COUNCIL_API = "http://kai-council-api:8002"
WORKER_API  = "http://kai-worker-api:8001"

COUNCIL_CHANNELS = {
    "chief", "beats", "beats-personal",
    "ember", "doc", "coach", "biz",
    "council", "council-daily", "council-weekly", "council-monthly",
    "sky", "roads",
}

PARKING_LOT_CHANNEL = "kai-parking-lot"


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


bot_token = load_secret("slack_bot_token")
app_token = load_secret("slack_app_token")

app = App(token=bot_token)

# Per-thread conversation history
_history: dict[str, list[dict]] = {}

BOT_ID: str = ""


def get_bot_id() -> str:
    global BOT_ID
    if not BOT_ID:
        result = app.client.auth_test()
        BOT_ID = result["user_id"]
    return BOT_ID


def channel_name(channel_id: str) -> str:
    try:
        info = app.client.conversations_info(channel=channel_id)
        return info["channel"]["name"]
    except Exception:
        return ""


def thread_key(channel_id: str, ts: str) -> str:
    return f"{channel_id}:{ts}"


def call_council(channel: str, message: str, user_id: str, ts: str) -> str:
    key = thread_key(channel, ts)
    history = _history.get(key, [])

    try:
        r = httpx.post(
            f"{COUNCIL_API}/council/message",
            json={
                "channel": channel,
                "message": message,
                "user_id": user_id,
                "history": history,
                "thread_ts": ts,
            },
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()

        _history[key] = data.get("history", history)
        return data.get("reply", "(no reply)")
    except Exception as e:
        log.error(f"Council API error: {e}")
        return "⚠️ KAI is temporarily unavailable."


ADVISOR_IDENTITIES = {
    "beats":   {"username": "Beats",   "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "biz":     {"username": "Biz",     "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "creative":{"username": "Creative","icon_url": "https://kai.sonicink.space/icon-192.png"},
    "tech":    {"username": "Tech",    "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "dev":     {"username": "Dev",     "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "learning":{"username": "Learning","icon_url": "https://kai.sonicink.space/icon-192.png"},
    "support": {"username": "Support", "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "coach":   {"username": "Coach",   "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "sky":     {"username": "Sky",     "icon_url": "https://kai.sonicink.space/avatar-sky.png"},
    "roads":   {"username": "Roads",   "icon_url": "https://kai.sonicink.space/avatar-roads.png"},
}


# ── T2 Approval Gate ───────────────────────────────────────────────────────────

def extract_t2_id_from_text(text: str) -> str | None:
    """Extract T2 action ID from a KAI T2 approval request message."""
    # Format: ⚡ *T2 Action Request* — `xxxxxxxx`
    m = re.search(r"`([a-f0-9]{8})`", text)
    return m.group(1) if m else None


@app.event("reaction_added")
def handle_reaction(event, say):
    """Handle ✅ and ❌ reactions for T2 approval gate."""
    reaction = event.get("reaction", "")
    if reaction not in ("white_check_mark", "x"):
        return

    # Get the original message that was reacted to
    item = event.get("item", {})
    if item.get("type") != "message":
        return

    channel_id = item["channel"]
    msg_ts = item["ts"]

    # Fetch the message to extract T2 action ID
    try:
        result = app.client.conversations_history(
            channel=channel_id,
            latest=msg_ts,
            inclusive=True,
            limit=1,
        )
        messages = result.get("messages", [])
        if not messages:
            return
        msg_text = messages[0].get("text", "")
    except Exception as e:
        log.error(f"T2 reaction message fetch error: {e}")
        return

    action_id = extract_t2_id_from_text(msg_text)
    if not action_id:
        return  # Not a T2 message

    log.info(f"T2 reaction '{reaction}' on action {action_id}")

    try:
        if reaction == "white_check_mark":
            r = httpx.post(f"{WORKER_API}/t2/approve/{action_id}", timeout=10)
            data = r.json()
            if data.get("ok"):
                app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=msg_ts,
                    text=f"✅ *T2 action `{action_id}` approved.* KAI will proceed.",
                )
                log.info(f"T2 action {action_id} approved")
        elif reaction == "x":
            r = httpx.post(f"{WORKER_API}/t2/reject/{action_id}", timeout=10)
            data = r.json()
            if data.get("ok"):
                app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=msg_ts,
                    text=f"❌ *T2 action `{action_id}` rejected.*",
                )
    except Exception as e:
        log.error(f"T2 approval API error: {e}")


# ── Message Handler ────────────────────────────────────────────────────────────

@app.event("message")
def handle_message(event, say):
    if event.get("bot_id"):
        return
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return
    if event.get("user") == get_bot_id():
        return

    channel_id = event["channel"]
    ch_name = channel_name(channel_id)

    text = event.get("text", "").strip()
    user_id = event.get("user", "unknown")
    ts = event.get("thread_ts") or event["ts"]

    if not text:
        return

    # Parking Lot
    if ch_name == PARKING_LOT_CHANNEL:
        try:
            r = httpx.post(
                f"{WORKER_API}/parking-lot/capture",
                json={
                    "text": text,
                    "channel_id": channel_id,
                    "thread_ts": ts,
                    "user_id": user_id,
                },
                timeout=30.0,
            )
            data = r.json()
            log.info(f"Parking lot: {data.get('type', '?')}: {data.get('title', '?')[:40]}")
        except Exception as e:
            log.error(f"Parking lot error: {e}")
        return

    if ch_name not in COUNCIL_CHANNELS:
        return

    log.info(f"Message in #{ch_name} from {user_id}: {text[:60]}")
    reply = call_council(ch_name, text, user_id, ts)
    identity = ADVISOR_IDENTITIES.get(ch_name, {})
    if identity:
        app.client.chat_postMessage(
            channel=channel_id,
            text=reply,
            thread_ts=ts,
            username=identity["username"],
            icon_url=identity["icon_url"],
        )
    else:
        say(text=reply, thread_ts=ts)


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "")
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if text:
        event["text"] = text
        handle_message(event, say)


def main():
    log.info("kai-slack-bot starting (Socket Mode) — Sprint 8 with T2 gate")
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()

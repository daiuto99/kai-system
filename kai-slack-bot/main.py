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

# Per-thread conversation history: {thread_key: [messages]}
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
        reply = data["reply"]

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        _history[key] = history[-20:]

        insights = data.get("insights_logged", 0)
        insight_note = f" | {insights} insight(s) logged" if insights else ""
        log.info(f"Council response: {data['advisor']} — {data['input_tokens']}in/{data['output_tokens']}out tokens{insight_note}")
        return reply

    except httpx.HTTPStatusError as e:
        log.error(f"Council API error: {e.response.status_code} {e.response.text}")
        return f"Something went wrong reaching the Council. (HTTP {e.response.status_code})"
    except Exception as e:
        log.error(f"Council API exception: {e}")
        return "I couldn't reach the Council API. Check that kai-council-api is running."


@app.event("message")
def handle_message(event, say):
    # Ignore bot messages and edits
    if event.get("bot_id"):
        return
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return
    if event.get("user") == get_bot_id():
        return

    channel_id = event["channel"]
    ch_name = channel_name(channel_id)

    # Extract common fields up front
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
    say(text=reply, thread_ts=ts)


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "")
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if text:
        event["text"] = text
        handle_message(event, say)


def main():
    log.info("kai-slack-bot starting (Socket Mode)")
    handler = SocketModeHandler(app, app_token)
    handler.start()


if __name__ == "__main__":
    main()

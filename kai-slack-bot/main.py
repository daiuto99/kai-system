import os
import logging
import re
import threading
from pathlib import Path
import httpx
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [kai-slack-bot] %(message)s")
log = logging.getLogger(__name__)

COUNCIL_API = "http://kai-council-api:8002"
WORKER_API  = "http://kai-worker-api:8001"

COUNCIL_CHANNELS = {
    "encore", "kai-focus", "launchbox", "revolt-group",
    "soul-collective", "ice-cream-stand", "test-project",
}

PARKING_LOT_CHANNEL = "kai-parking-lot"
KAI_SYSTEM_CHANNEL  = "kai-system"

ADVISOR_NAMES = {
    "kai", "beats", "ember", "doc", "coach", "biz",
    "creative", "tech", "dev", "sky", "roads", "ops", "learning", "support",
}

ADVISOR_BOTS = ["beats", "creative", "dev", "sky", "roads"]

ADVISOR_IDENTITIES = {
    "kai":      {"username": "KAI",      "icon_url": "https://kai.sonicink.space/avatar-kai.png"},
    "ember":    {"username": "Ember",    "icon_url": "https://kai.sonicink.space/avatar-ember.png"},
    "beats":    {"username": "Beats",    "icon_url": "https://kai.sonicink.space/avatar-beats.png"},
    "doc":      {"username": "Doc",      "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "coach":    {"username": "Coach",    "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "biz":      {"username": "Biz",      "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "creative": {"username": "Creative", "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "tech":     {"username": "Tech",     "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "dev":      {"username": "Dev",      "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "learning": {"username": "Learning", "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "support":  {"username": "Support",  "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "sky":      {"username": "Sky",      "icon_url": "https://kai.sonicink.space/avatar-sky.png"},
    "roads":    {"username": "Roads",    "icon_url": "https://kai.sonicink.space/avatar-roads.png"},
    "ops":      {"username": "Ops",      "icon_url": "https://kai.sonicink.space/icon-192.png"},
}


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


bot_token = load_secret("slack_bot_token")
app_token = load_secret("slack_app_token")

app = App(token=bot_token)

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


def thread_key(channel: str, ts: str) -> str:
    return f"{channel}:{ts}"


def call_council(channel: str, message: str, user_id: str, ts: str) -> str:
    key = thread_key(channel, ts)
    history = _history.get(key, [])
    try:
        r = httpx.post(
            f"{COUNCIL_API}/council/message",
            json={"channel": channel, "message": message, "user_id": user_id, "history": history, "thread_ts": ts},
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        _history[key] = data.get("history", history)
        return data.get("reply", "(no reply)")
    except Exception as e:
        log.error(f"Council API error: {e}")
        return "⚠️ KAI is temporarily unavailable."


def parse_advisor_prefix(text: str) -> tuple[str, str]:
    """Parse /advisor message. Returns (advisor, message). Defaults to kai."""
    if text.startswith("/"):
        parts = text[1:].split(None, 1)
        name = parts[0].lower()
        if name in ADVISOR_NAMES:
            return name, (parts[1] if len(parts) > 1 else "")
    return "kai", text


def post_as_advisor(client, channel_id: str, advisor: str, reply: str, thread_ts: str = None):
    identity = ADVISOR_IDENTITIES.get(advisor, ADVISOR_IDENTITIES["kai"])
    kwargs = dict(channel=channel_id, text=reply, username=identity["username"], icon_url=identity["icon_url"])
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    client.chat_postMessage(**kwargs)


# ── T2 Approval Gate ───────────────────────────────────────────────────────────

def extract_t2_id_from_text(text: str) -> str | None:
    m = re.search(r"`([a-f0-9]{8})`", text)
    return m.group(1) if m else None


@app.event("reaction_added")
def handle_reaction(event, say):
    if event.get("user") == get_bot_id():
        return
    emoji = event.get("reaction", "")
    if emoji not in ("white_check_mark", "x"):
        return
    item = event.get("item", {})
    if item.get("type") != "message":
        return
    channel_id = item.get("channel")
    ts = item.get("ts")
    try:
        result = app.client.conversations_history(channel=channel_id, latest=ts, limit=1, inclusive=True)
        messages = result.get("messages", [])
        if not messages:
            return
        msg_text = messages[0].get("text", "")
    except Exception as e:
        log.error(f"T2 reaction message fetch error: {e}")
        return
    action_id = extract_t2_id_from_text(msg_text)
    if not action_id:
        return
    approved = emoji == "white_check_mark"
    try:
        r = httpx.post(
            f"{WORKER_API}/t2/respond",
            json={"action_id": action_id, "approved": approved, "user_id": event.get("user")},
            timeout=15.0,
        )
        log.info(f"T2 {'approved' if approved else 'rejected'}: {action_id} → {r.status_code}")
    except Exception as e:
        log.error(f"T2 response error: {e}")


# ── KAI Message Handler ─────────────────────────────────────────────────────────

@app.event("message")
def handle_message(event, say):
    if event.get("bot_id"):
        return
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return
    if event.get("user") == get_bot_id():
        return

    channel_id = event["channel"]
    channel_type = event.get("channel_type", "")
    text = event.get("text", "").strip()
    user_id = event.get("user", "unknown")
    ts = event.get("thread_ts") or event["ts"]

    if not text:
        return

    if channel_type == "im":
        advisor, message = parse_advisor_prefix(text)
        if not message:
            post_as_advisor(app.client, channel_id, "kai",
                "Send me a message or use */advisor message* to reach a specific advisor.\n\n"
                "Available: /beats /coach /biz /sky /roads /tech /dev /ops /creative /learning /support")
            return
        log.info(f"DM from {user_id} → {advisor}: {message[:60]}")
        reply = call_council(advisor, message, user_id, ts)
        post_as_advisor(app.client, channel_id, advisor, reply)
        return

    ch_name = channel_name(channel_id)

    if ch_name == PARKING_LOT_CHANNEL:
        try:
            r = httpx.post(
                f"{WORKER_API}/parking-lot/capture",
                json={"text": text, "channel_id": channel_id, "thread_ts": ts, "user_id": user_id},
                timeout=30.0,
            )
            data = r.json()
            log.info(f"Parking lot: {data.get('type', '?')}: {data.get('title', '?')[:40]}")
        except Exception as e:
            log.error(f"Parking lot error: {e}")
        return

    if ch_name == KAI_SYSTEM_CHANNEL:
        return

    if ch_name not in COUNCIL_CHANNELS:
        return

    log.info(f"Message in #{ch_name} from {user_id}: {text[:60]}")
    reply = call_council("kai", text, user_id, ts)
    post_as_advisor(app.client, channel_id, "kai", reply, ts)


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "")
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if text:
        event["text"] = text
        handle_message(event, say)




# ── File Listener — project channel file_shared events ─────────────────────────

@app.event("file_shared")
def handle_file_shared(event, say):
    """Download files shared in KAI-managed project channels and ingest them."""
    channel_id = event.get("channel_id")
    file_id = event.get("file_id")
    if not channel_id or not file_id:
        return

    # Check registry — only process managed project channels
    try:
        r = httpx.get(f"{WORKER_API}/slack/projects/registry", timeout=5)
        registry = r.json().get("registry", {})
    except Exception as e:
        log.error(f"file_shared: registry fetch failed: {e}")
        return

    project = registry.get(channel_id)
    if not project:
        return

    log.info(f"file_shared in managed channel {channel_id}, project={project.get('project_name')}, file={file_id}")

    # Download via worker API (it has the token + vault write access)
    try:
        r = httpx.post(
            f"{WORKER_API}/slack/files/ingest",
            json={"file_id": file_id, "channel_id": channel_id},
            timeout=120,
        )
        data = r.json()
        log.info(f"file ingest result: {data}")
    except Exception as e:
        log.error(f"file_shared: ingest call failed: {e}")

# ── Advisor Bot Factory ─────────────────────────────────────────────────────────

def make_advisor_handler(advisor: str) -> SocketModeHandler | None:
    bot_tok = load_secret(f"slack_bot_token_{advisor}")
    app_tok = load_secret(f"slack_app_token_{advisor}")
    if not bot_tok or not app_tok:
        log.warning(f"Advisor bot {advisor}: missing tokens — skipping")
        return None

    advisor_app = App(token=bot_tok)

    _advisor = advisor
    _client = advisor_app.client

    @advisor_app.event("message")
    def handle_advisor_dm(event, say):
        if event.get("bot_id"):
            return
        if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
            return
        if event.get("channel_type") != "im":
            return
        channel_id = event["channel"]
        text = event.get("text", "").strip()
        user_id = event.get("user", "unknown")
        ts = event.get("thread_ts") or event["ts"]
        if not text:
            return
        log.info(f"DM ({_advisor}) from {user_id}: {text[:60]}")
        reply = call_council(_advisor, text, user_id, ts)
        identity = ADVISOR_IDENTITIES.get(_advisor, ADVISOR_IDENTITIES["kai"])
        _client.chat_postMessage(
            channel=channel_id,
            text=reply,
            username=identity["username"],
            icon_url=identity["icon_url"],
        )

    log.info(f"Advisor bot {advisor}: initialized")
    return SocketModeHandler(advisor_app, app_tok)


def main():
    log.info("kai-slack-bot starting — KAI + 5 advisor bots")

    for advisor in ADVISOR_BOTS:
        handler = make_advisor_handler(advisor)
        if handler:
            t = threading.Thread(target=handler.start, name=f"slack-{advisor}", daemon=True)
            t.start()
            log.info(f"Started advisor bot thread: {advisor}")

    log.info("Starting KAI main bot (main thread)")
    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()

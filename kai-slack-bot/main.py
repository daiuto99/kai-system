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
# Council's agentic loop can legitimately run past 60s (L9 allows 12 iterations);
# the old 60s client timeout turned slow-but-successful replies into "unavailable".
COUNCIL_TIMEOUT_S = 180.0

COUNCIL_CHANNELS = {
    "encore", "launchbox", "revolt-group",
    "soul-collective", "ice-cream-stand",
}

DEVOPS_CHANNEL = "devops"

ADVISOR_NAMES = {
    "kai", "beats", "ember", "doc", "coach",
    "creative", "tech", "dev", "sky", "roads", "ops", "learning", "support",
}

ADVISOR_BOTS = ["sky", "roads"]

# Slack identities — only advisors that post as themselves. All other advisor
# output is relayed via KAI with a "Beats says:" prefix (see post_as_advisor).
ADVISOR_IDENTITIES = {
    "kai":   {"username": "KAI",   "icon_url": "https://kai.sonicink.space/avatar-kai.png"},
    "sky":   {"username": "Sky",   "icon_url": "https://kai.sonicink.space/avatar-sky.png"},
    "roads": {"username": "Roads", "icon_url": "https://kai.sonicink.space/avatar-roads.png"},
}

# Capitalized labels for the "Beats says:" relay prefix
ADVISOR_LABELS = {
    "beats": "Beats", "ember": "Ember", "doc": "Doc", "coach": "Coach",
    "creative": "Creative", "tech": "Tech", "dev": "Dev", "ops": "Ops",
    "learning": "Learning", "support": "Support", "devops": "DevOps",
}


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


def _worker_auth():
    """Basic-auth tuple for internal calls to kai-worker-api (Bug 48f85706).
    The worker authenticates every route; the credential is a mounted docker
    secret. Returns None if unavailable — the call then 401s and is logged."""
    raw = load_secret("kai_worker_auth")
    if raw and ":" in raw:
        u, pw = raw.split(":", 1)
        return (u, pw)
    log.warning("worker_auth: no kai_worker_auth credential mounted — worker calls will 401")
    return None


bot_token = load_secret("slack_bot_token")
app_token = load_secret("slack_app_token")
signing_secret = load_secret("slack_signing_secret")

app = App(token=bot_token, signing_secret=signing_secret)

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


def call_council(channel: str, message: str, user_id: str, ts: str,
                 trigger_source: str = "") -> str:
    """No history field — memory is server-owned (CONTEXT_SPEC §4.1); a
    client-supplied history field is rejected with 400. thread_ts scopes the
    conversation key server-side (§4.2), so the Slack thread stays coherent
    without this bot maintaining its own copy of the transcript."""
    try:
        r = httpx.post(
            f"{COUNCIL_API}/council/message",
            json={"channel": channel, "message": message, "user_id": user_id,
                  "thread_ts": ts, "trigger_source": trigger_source},
            timeout=COUNCIL_TIMEOUT_S,
            auth=_worker_auth(),
        )
        r.raise_for_status()
        data = r.json()
        return data.get("reply", "(no reply)")
    except httpx.TimeoutException:
        log.error("Council API timeout after %ss", COUNCIL_TIMEOUT_S)
        return (f"⚠️ KAI error — the council did not answer within {int(COUNCIL_TIMEOUT_S)}s. "
                "It may still be working; ask again in a minute.")
    except httpx.HTTPStatusError as e:
        log.error("Council API HTTP %s", e.response.status_code)
        return f"⚠️ KAI error — the council API returned HTTP {e.response.status_code}."
    except httpx.TransportError as e:
        log.error("Council API unreachable: %s", type(e).__name__)
        return (f"⚠️ KAI error — the council API is unreachable ({type(e).__name__}); "
                "the service may be restarting.")
    except Exception as e:
        log.error("Council API error: %s", type(e).__name__)
        return f"⚠️ KAI error — unexpected {type(e).__name__} while contacting the council."


def parse_advisor_prefix(text: str) -> tuple[str, str]:
    """Parse /advisor message. Returns (advisor, message). Defaults to kai."""
    if text.startswith("/"):
        parts = text[1:].split(None, 1)
        name = parts[0].lower()
        if name in ADVISOR_NAMES:
            return name, (parts[1] if len(parts) > 1 else "")
    return "kai", text


def post_as_advisor(client, channel_id: str, advisor: str, reply: str, thread_ts: str = None):
    """Called from KAI's main bot. KAI/Sky/Roads post as themselves; every
    other advisor is relayed by KAI with a 'Beats says:' attribution prefix."""
    if advisor in ADVISOR_IDENTITIES:
        identity = ADVISOR_IDENTITIES[advisor]
        text = reply
    else:
        identity = ADVISOR_IDENTITIES["kai"]
        label = ADVISOR_LABELS.get(advisor, advisor.capitalize())
        text = f"{label} says:\n{reply}"
    kwargs = dict(channel=channel_id, text=text, username=identity["username"], icon_url=identity["icon_url"])
    if thread_ts:
        kwargs["thread_ts"] = thread_ts
    client.chat_postMessage(**kwargs)


# ── KAI Mode Lock — block_actions forward (Socket Mode bridge) ────────────────
# App runs in Socket Mode, so Slack delivers button taps via websocket, not the
# Interactivity Request URL. We forward the parsed payload to the worker so the
# existing mode_lock decision logic stays in one place.

def _forward_mode_lock_action(ack, body, logger):
    ack()
    try:
        r = httpx.post(
            f"{WORKER_API}/mode_lock/slack_action_internal",
            json={"payload": body},
            timeout=10,
            auth=_worker_auth(),
        )
        logger.info(f"mode_lock forward: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.exception(f"mode_lock forward error: {e}")

for _aid in ("mode_lock_allow_once", "mode_lock_deny", "mode_lock_allow_session"):
    app.action(_aid)(_forward_mode_lock_action)


# ── T2 Approval Gate ───────────────────────────────────────────────────────────

def extract_t2_id_from_text(text: str) -> str | None:
    m = re.search(r"`([a-f0-9]{8})`", text)
    return m.group(1) if m else None


def _t2_requires_council_execution(response_data: dict) -> bool:
    """Hostops gate resolution advances the workflow itself; generic T2 does not."""
    return not (response_data.get("kind") == "hostops_gate" and response_data.get("executed"))


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
            auth=_worker_auth(),
        )
        log.info(f"T2 {'approved' if approved else 'rejected'}: {action_id} → {r.status_code}")
        if approved and r.status_code == 200:
            response_data = r.json()
            if not _t2_requires_council_execution(response_data):
                # Gate resolution already advances the hostops workflow; the
                # generic T2 execute prompt would run it a second time.
                return
            entry = response_data.get("entry", {})
            action_text = entry.get("action", "")
            detail_text = entry.get("detail", "")
            exec_msg = f"T2 action approved (id: {action_id}): {action_text}"
            if detail_text:
                exec_msg += f" — {detail_text}"
            exec_msg += ". Execute it now using the appropriate tool and confirm completion."
            exec_reply = call_council("kai", exec_msg, event.get("user", "leo"), f"t2-{action_id}",
                                      trigger_source=f"t2:execute:{action_id}")
            try:
                app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=ts,
                    text=exec_reply,
                    username="KAI",
                    icon_url="https://kai.sonicink.space/avatar-kai.png",
                )
            except Exception as post_err:
                log.error(f"T2 exec reply post error: {post_err}")
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
                "Available: /beats /coach /sky /roads /tech /dev /ops /creative /learning /support")
            return
        log.info(f"DM from {user_id} → {advisor}: {message[:60]}")
        reply = call_council(advisor, message, user_id, ts,
                             trigger_source=f"slack:dm:{advisor}")
        post_as_advisor(app.client, channel_id, advisor, reply)
        return

    ch_name = channel_name(channel_id)

    if ch_name == DEVOPS_CHANNEL:
        if text:
            text_lower = text.lower()
            # Intake trigger
            if any(kw in text_lower for kw in ["process examples", "process design", "process resources", "ingest examples", "review examples"]):
                try:
                    httpx.post(
                        f"{WORKER_API}/intake/scan",
                        json={"advisor": "creative", "channel_id": channel_id},
                        timeout=15,
                        auth=_worker_auth(),
                    )
                except Exception as e:
                    log.error(f"intake scan: {e}")
                return
            # Route to active intake if one is running
            try:
                r = httpx.get(f"{WORKER_API}/intake/active/creative", timeout=5, auth=_worker_auth())
                if r.json().get("active"):
                    httpx.post(
                        f"{WORKER_API}/intake/reply/creative",
                        json={"text": text, "channel_id": channel_id},
                        timeout=30,
                        auth=_worker_auth(),
                    )
                    return
            except Exception as e:
                log.error(f"intake active check: {e}")
            # Checkin dispatch
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            try:
                httpx.post(
                    f"{WORKER_API}/slack/events",
                    json={"event": {"type": "message", "thread_ts": thread_ts, "channel": channel_id, "text": text}},
                    timeout=10,
                )
            except Exception as e:
                log.error(f"checkin dispatch: {e}")
        return

    if ch_name not in COUNCIL_CHANNELS:
        return

    log.info(f"Message in #{ch_name} from {user_id}: {text[:60]}")
    reply = call_council("kai", text, user_id, ts,
                         trigger_source=f"slack:channel:{ch_name}")
    post_as_advisor(app.client, channel_id, "kai", reply, ts)


@app.event("app_mention")
def handle_mention(event, say):
    text = event.get("text", "")
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
    if text:
        event["text"] = text
        handle_message(event, say)




# ── Advisor Bot Factory (Sky + Roads only — direct DMs, KAI-aware via dm_log.jsonl) ──

import json as _json
from datetime import datetime as _dt

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/vault"))


def _log_advisor_dm(advisor: str, user_id: str, message: str, reply: str):
    """Append a DM exchange to the advisor's dm_log.jsonl so KAI can read it later."""
    try:
        log_dir = VAULT_PATH / "60_Council" / advisor
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _dt.utcnow().isoformat() + "Z",
            "user_id": user_id,
            "message": message,
            "reply": reply,
        }
        with open(log_dir / "dm_log.jsonl", "a") as f:
            f.write(_json.dumps(entry) + "\n")
    except Exception as e:
        log.error(f"_log_advisor_dm({advisor}): {e}")


def make_advisor_handler(advisor: str) -> SocketModeHandler | None:
    bot_tok = load_secret(f"slack_bot_token_{advisor}")
    app_tok = load_secret(f"slack_app_token_{advisor}")
    if not bot_tok or not app_tok:
        log.warning(f"Advisor bot {advisor}: missing tokens — skipping")
        return None

    advisor_app = App(token=bot_tok, signing_secret=signing_secret)

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
        reply = call_council(_advisor, text, user_id, ts,
                             trigger_source=f"slack:dm:{_advisor}")
        _log_advisor_dm(_advisor, user_id, text, reply)
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
    log.info(f"kai-slack-bot starting — KAI + advisor bots: {ADVISOR_BOTS}")

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

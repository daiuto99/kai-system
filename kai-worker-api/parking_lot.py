"""
Parking Lot — capture anything, classify it, write to vault, suggest routing.
"""
from pathlib import Path
from datetime import datetime
import re
import anthropic
import httpx
import os


CAPTURE_TYPES = ["article", "idea", "product", "recipe", "note", "link", "music", "video"]

CHIEF_SIGNALS = ["strategy", "business", "launch", "product", "startup", "revenue",
                 "market", "brand", "partnership", "invest", "fund", "plan", "roadmap"]
BEATS_SIGNALS = ["music", "song", "track", "guitar", "bass", "piano", "studio",
                 "record", "mix", "sample", "beat", "chord", "artist", "album",
                 "creative", "sound", "instrument", "gear", "plugin"]


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


def classify_capture(text: str) -> dict:
    api_key = load_secret("anthropic_api_key")
    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": f"""Classify this capture and give it a short title.

Capture: {text}

Respond in this exact format (no other text):
TYPE: [one of: article, idea, product, recipe, note, link, music, video]
TITLE: [short descriptive title, max 8 words]
SUMMARY: [one sentence summary]"""
        }],
    )

    raw = response.content[0].text.strip()
    result = {"type": "note", "title": text[:50], "summary": text[:100]}

    for line in raw.splitlines():
        if line.startswith("TYPE:"):
            t = line.split(":", 1)[1].strip().lower()
            if t in CAPTURE_TYPES:
                result["type"] = t
        elif line.startswith("TITLE:"):
            result["title"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUMMARY:"):
            result["summary"] = line.split(":", 1)[1].strip()

    return result


def suggest_routing(text: str, capture_type: str) -> str | None:
    lower = text.lower()
    if capture_type == "idea":
        if any(s in lower for s in BEATS_SIGNALS):
            return "beats"
        if any(s in lower for s in CHIEF_SIGNALS):
            return "chief"
    if capture_type == "music" or any(s in lower for s in BEATS_SIGNALS):
        return "beats"
    return None


def slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:50]


def write_capture_card(text: str, classification: dict, vault_path: Path, user_id: str = "") -> str:
    pl_dir = vault_path / "50_ParkingLot"
    pl_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    slug = slugify(classification["title"])
    filename = f"{date_str}-{slug}.md"
    filepath = pl_dir / filename

    counter = 1
    while filepath.exists():
        filepath = pl_dir / f"{date_str}-{slug}-{counter}.md"
        counter += 1

    card = f"""---
date: {date_str}
time: {time_str}
type: {classification['type']}
source: slack
status: captured
---

# {classification['title']}

{classification['summary']}

## Raw Capture
{text}
"""

    filepath.write_text(card, encoding="utf-8")
    return str(filepath.relative_to(vault_path))


def post_capture_response(slack_token: str, channel: str, thread_ts: str,
                          classification: dict, file_path: str,
                          routing: str | None) -> None:
    type_emoji = {
        "article": "📰", "idea": "💡", "product": "🛍️",
        "recipe": "🍳", "note": "📝", "link": "🔗",
        "music": "🎵", "video": "🎬"
    }.get(classification["type"], "📌")

    msg = f"{type_emoji} *{classification['title']}*\n_{classification['summary']}_\n\nSaved to vault."

    if routing:
        msg += f"\n\n→ This looks like it belongs in *#{routing}*. Want me to route it there?"

    with httpx.Client() as client:
        client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {slack_token}"},
            json={
                "channel": channel,
                "thread_ts": thread_ts,
                "text": msg,
                "mrkdwn": True,
            },
            timeout=15.0,
        )


def capture(text: str, channel_id: str, thread_ts: str,
            user_id: str, vault_path: Path = Path("/vault")) -> dict:
    classification = classify_capture(text)
    file_path = write_capture_card(text, classification, vault_path, user_id)
    routing = suggest_routing(text, classification["type"])

    slack_token = load_secret("slack_bot_token")
    post_capture_response(slack_token, channel_id, thread_ts, classification, file_path, routing)

    return {
        "status": "captured",
        "type": classification["type"],
        "title": classification["title"],
        "file": file_path,
        "routing_suggestion": routing,
    }

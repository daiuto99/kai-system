"""
Parking Lot — capture anything, enrich with OG metadata + summarization, write to vault.
"""
from pathlib import Path
from datetime import datetime
import re
import anthropic
import httpx
import os

from usage_tracker import _track_usage


CAPTURE_TYPES = ["article", "idea", "product", "recipe", "note", "link", "music", "video"]

KAI_SIGNALS = ["strategy", "business", "launch", "product", "startup", "revenue",
                "market", "brand", "partnership", "invest", "fund", "plan", "roadmap"]
BEATS_SIGNALS = ["music", "song", "track", "guitar", "bass", "piano", "studio",
                 "record", "mix", "sample", "beat", "chord", "artist", "album",
                 "creative", "sound", "instrument", "gear", "plugin"]


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


def resolve_url(url: str) -> str:
    """Follow redirects to get the real URL. Unwraps Google share/redirect links."""
    import urllib.parse
    if not url:
        return url
    try:
        # Unwrap google.com/url?q= redirects
        if "google.com/url" in url:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            url = params.get("q", params.get("url", [url]))[0]

        # share.google URLs require GET + redirect follow (HEAD returns 404 or loops)
        if "share.google" in url:
            with httpx.Client(timeout=10, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"}) as client:
                r = client.get(url)
                final = str(r.url)
                # Only accept if it actually resolved to a different domain
                if "share.google" not in final and "accounts.google" not in final:
                    return final
            return url  # couldn't resolve — keep as-is

        # General redirect follow (t.co, bit.ly, etc.)
        with httpx.Client(timeout=10, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.head(url)
            return str(r.url)
    except Exception:
        return url


def fetch_og_metadata(url: str) -> dict:
    """Fetch OG title, description, and image from a URL."""
    if not url:
        return {}
    try:
        with httpx.Client(timeout=10, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 Twitterbot/1.0"}) as client:
            r = client.get(url)
        html = r.text[:80000]

        def og(prop):
            m = re.search(
                rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)["\']',
                html, re.I)
            if not m:
                m = re.search(
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}["\']',
                    html, re.I)
            return m.group(1).strip() if m else ""

        from html import unescape
        og_title = unescape(og("title"))
        og_desc = unescape(og("description"))
        og_image = unescape(og("image"))

        if not og_title:
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
            og_title = unescape(m.group(1).strip()) if m else ""

        if og_image and og_image.startswith("/"):
            import urllib.parse
            p = urllib.parse.urlparse(url)
            og_image = f"{p.scheme}://{p.netloc}{og_image}"

        return {"og_title": og_title, "og_description": og_desc, "og_image": og_image}
    except Exception:
        return {}


def summarize_article(url: str, fallback: str, api_key: str) -> str:
    """Fetch article text and summarize with Claude Haiku."""
    if not url:
        return fallback
    try:
        with httpx.Client(timeout=12, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = client.get(url)
        html = r.text[:60000]
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()[:6000]

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=180,
            messages=[{"role": "user", "content":
                f"Summarize this article in 2-3 sentences for a personal dashboard. "
                f"Be concise and direct. No preamble.\n\n{text}"}],
        )
        _track_usage("parking_lot", resp.usage.input_tokens, resp.usage.output_tokens,
                     provider="anthropic", model="claude-haiku-4-5-20251001")
        return resp.content[0].text.strip()
    except Exception:
        return fallback


def classify_capture(text: str, og_title: str = "", og_description: str = "") -> dict:
    api_key = load_secret("anthropic_api_key")
    client = anthropic.Anthropic(api_key=api_key)

    context = text
    if og_title:
        context += f"\nTitle: {og_title}"
    if og_description:
        context += f"\nDescription: {og_description}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"""Classify this capture and give it a short title.

Capture: {context}

Respond in this exact format (no other text):
TYPE: [one of: article, idea, product, recipe, note, link, music, video]
TITLE: [short descriptive title, max 8 words]
SUMMARY: [one sentence summary]
TAGS: [2-4 comma-separated lowercase tags]"""
        }],
    )
    _track_usage("parking_lot", response.usage.input_tokens, response.usage.output_tokens,
                 provider="anthropic", model="claude-haiku-4-5-20251001")

    raw = response.content[0].text.strip()
    result = {"type": "note", "title": og_title or text[:50], "summary": og_description or text[:100], "tags": []}

    for line in raw.splitlines():
        if line.startswith("TYPE:"):
            t = line.split(":", 1)[1].strip().lower()
            if t in CAPTURE_TYPES:
                result["type"] = t
        elif line.startswith("TITLE:"):
            result["title"] = line.split(":", 1)[1].strip()
        elif line.startswith("SUMMARY:"):
            result["summary"] = line.split(":", 1)[1].strip()
        elif line.startswith("TAGS:"):
            raw_tags = line.split(":", 1)[1].strip()
            result["tags"] = [t.strip().lower() for t in raw_tags.split(",") if t.strip()][:4]

    return result


def suggest_routing(text: str, capture_type: str) -> str | None:
    lower = text.lower()
    if capture_type == "idea":
        if any(s in lower for s in BEATS_SIGNALS):
            return "beats"
        if any(s in lower for s in KAI_SIGNALS):
            return "kai"
    if capture_type == "music" or any(s in lower for s in BEATS_SIGNALS):
        return "beats"
    return None


def slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:50]


def write_capture_card(text: str, classification: dict, og: dict,
                       real_url: str, vault_path: Path, user_id: str = "") -> str:
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

    tags_str = ", ".join(classification.get("tags", []))
    og_image = og.get("og_image", "")
    og_title = og.get("og_title", "")

    card = f"""---
date: {date_str}
time: {time_str}
type: {classification['type']}
source: slack
status: captured
url: {real_url}
image: {og_image}
tags: {tags_str}
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
            json={"channel": channel, "thread_ts": thread_ts, "text": msg, "mrkdwn": True},
            timeout=15.0,
        )


def gather_capture_context(text: str) -> dict:
    """Read-only sibling of enrich_text — resolves URL + OG metadata, no model calls, no vault writes.

    Returned shape matches clarification_store.create_pending's captured_content contract.
    """
    urls = re.findall(r"https?://\S+", text)
    raw_url = urls[0].rstrip(">).,") if urls else ""
    real_url = resolve_url(raw_url) if raw_url else ""
    og = fetch_og_metadata(real_url) if real_url else {}
    return {
        "original_message": text,
        "url":              real_url or None,
        "og_title":         og.get("og_title", ""),
        "og_description":   og.get("og_description", ""),
    }


def enrich_text(text: str, api_key: str) -> tuple[dict, dict, str]:
    """Resolve URL, fetch OG metadata, classify. Returns (classification, og, real_url)."""
    urls = re.findall(r"https?://\S+", text)
    raw_url = urls[0].rstrip(">).,") if urls else ""
    real_url = resolve_url(raw_url) if raw_url else ""
    og = fetch_og_metadata(real_url) if real_url else {}
    classification = classify_capture(text, og.get("og_title", ""), og.get("og_description", ""))

    if classification["type"] == "article" and real_url:
        classification["summary"] = summarize_article(
            real_url, og.get("og_description", classification["summary"]), api_key)

    return classification, og, real_url


def capture(text: str, channel_id: str, thread_ts: str,
            user_id: str, vault_path: Path = Path("/vault")) -> dict:
    api_key = load_secret("anthropic_api_key")
    classification, og, real_url = enrich_text(text, api_key)
    file_path = write_capture_card(text, classification, og, real_url, vault_path, user_id)
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

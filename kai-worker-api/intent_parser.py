"""Intent parser for Sprint A — reads Leo's full parking-lot message and
extracts structured intent before the existing classification pipeline runs.

Designed to sit upstream of parking_lot.capture(). Output drives the routing
engine; when no intent is detected the system falls back to capture-only
behavior (the pre-Sprint-A path).

Output schema:
    {
        "action":       one of ACTIONS
        "destination":  advisor name (when action requires one) or None
        "instructions": the user's specific instructions verbatim (the X/Y/Z)
        "confidence":   "high" | "medium" | "low"
        "raw_reply":    the model's raw response (kept for debugging)
    }
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import anthropic

from usage_tracker import _track_usage

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

ACTIONS = [
    "save_to_recipes",
    "write_blog_post",
    "summarize",
    "share_with_advisor",
    "capture",
]

ADVISORS = ["kai", "beats", "sky", "roads", "coach", "ember", "doc", "creative", "dev"]
PRIVACY_ADVISORS = {"ember", "doc"}

PROMPT = """You are parsing a single Slack message Leo dropped into his #kai-parking-lot channel.
Your job is to extract Leo's INTENT — what does he want done with this — before any capture/classification happens.

INPUT MESSAGE:
{message}

URL TITLE (if any): {og_title}
URL DESCRIPTION (if any): {og_desc}

POSSIBLE ACTIONS:
- save_to_recipes:    Leo wants this saved as a recipe (food, cooking, drink). Destination is the recipe vault.
- write_blog_post:    Leo wants a blog post, draft, copy, article, or any written output. ALWAYS routes to `creative` regardless of topic — copywriting is creative's domain even when the subject is strategy, business, gear, or anything else. Creative will consult other advisors for subject-matter context.
- summarize:          Leo wants a summary, possibly with additional context gathered. Pass to the doc advisor.
- share_with_advisor: Leo named a specific advisor to send this to (Sky, Roads, Beats, Coach, Ember, Doc, Creative, Dev, Kai).
- capture:            FALLBACK. No clear intent — Leo just wants this saved for later. Use when in doubt.

ADVISOR DESTINATIONS (only valid when action is share_with_advisor or summarize):
- kai:      strategy, business, product, brand, planning
- beats:    music production, songwriting, mixing, plugins, beat-making
- sky:      studio gear, hardware, recording equipment, monitors, interfaces
- roads:    guitars, basses, pickups, rigs, amps, pedals, live instrument gear
- coach:    fitness, performance coaching, training programs, recovery
- ember:    personal/relational matters (PRIVACY-scoped)
- doc:      health, medical, study, longevity, nutrition, supplements (PRIVACY-scoped)
- creative: design, copy, visual direction, brand creative
- dev:      software engineering, code, technical implementation

OUTPUT FORMAT (strict JSON, no other text, no markdown fences):
{{"action": "<one of ACTIONS>", "destination": "<advisor name or null>", "instructions": "<Leo's specific instructions verbatim, or empty string>", "confidence": "high|medium|low"}}

RULES:
1. If Leo just dropped a URL with no commentary → action="capture", destination=null, confidence="high".
2. If Leo wrote prose around the URL, parse it. Look for verbs (summarize, write, save, send, share, gather).
3. If Leo named an advisor explicitly ("send to Sky", "for Roads") → action="share_with_advisor", destination=<name>.
4. If Leo asked for a blog post / draft / copy / article / written output → action="write_blog_post", destination="creative" ALWAYS. Topic does not change the destination — copywriting belongs to creative regardless of subject. Even leadership/brand/strategy blog posts go to creative, not kai.
5. "Summarize and gather additional information" → action="summarize", destination="doc" (the doc advisor handles the enrichment as part of its response).
6. The instructions field MUST quote Leo's specific asks verbatim ("highlighting X, Y, and Z") — do not paraphrase.
7. When uncertain, choose action="capture" with confidence="low" rather than guessing.
"""


class IntentParseError(Exception):
    pass


def parse_intent(
    message: str,
    og_title: str = "",
    og_description: str = "",
    api_key: str | None = None,
) -> dict:
    """Run the intent parser. Returns a structured intent dict (see module docstring)."""
    if api_key is None:
        api_key = _load_secret("anthropic_api_key")
    if not api_key:
        raise IntentParseError("anthropic_api_key not available")

    if not message or not message.strip():
        return _capture_fallback("empty message")

    prompt = PROMPT.format(
        message=message[:4000],
        og_title=og_title or "(none)",
        og_desc=og_description or "(none)",
    )

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    _track_usage("intent_parser", resp.usage.input_tokens, resp.usage.output_tokens,
                 provider="anthropic", model=MODEL)
    raw = resp.content[0].text.strip()

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("intent_parser: model returned non-JSON, falling back to capture: %r", raw[:200])
        return _capture_fallback("non_json_response", raw)

    action = parsed.get("action", "capture")
    if action not in ACTIONS:
        logger.warning("intent_parser: invalid action %r, falling back", action)
        return _capture_fallback("invalid_action", raw)

    destination = parsed.get("destination")
    if isinstance(destination, str) and destination.lower() in ADVISORS:
        destination = destination.lower()
    else:
        destination = None

    if action == "share_with_advisor" and destination is None:
        logger.info("intent_parser: share_with_advisor without destination, demoting to capture")
        return _capture_fallback("share_without_destination", raw)

    return {
        "action": action,
        "destination": destination,
        "instructions": (parsed.get("instructions") or "").strip(),
        "confidence": parsed.get("confidence", "low"),
        "raw_reply": raw,
    }


def _capture_fallback(reason: str, raw: str = "") -> dict:
    return {
        "action": "capture",
        "destination": None,
        "instructions": "",
        "confidence": "high" if reason == "empty message" else "low",
        "raw_reply": raw,
        "fallback_reason": reason,
    }


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _load_secret(name: str) -> str:
    import os
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")

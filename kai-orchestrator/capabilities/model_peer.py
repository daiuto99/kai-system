"""Model peer review capabilities — S4-6.

codex.review   → o4-mini via LiteLLM (architecture, performance, security/privacy)
chatgpt.review → gpt-4o via LiteLLM (design concepts, creative direction)
get_review     → reads stored review from vault by topic
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from models import CapabilityResult
from transports.base import safe_request
from . import capability

try:
    from usage_tracker import _track_usage
except ImportError:
    def _track_usage(*args, **kwargs):
        pass

_LITELLM_URL = "http://kai-litellm:4000"
_VAULT_REVIEWS = Path("/vault/60_Council/reviews")

_CODEX_SYSTEM = (
    "You are a senior systems architect performing a peer review for KAI, an autonomous AI "
    "orchestration system. Review the provided content for: architecture soundness and design "
    "patterns, performance implications and optimization opportunities, security and privacy "
    "risks, scalability and maintainability concerns. "
    "Respond with a JSON object (raw JSON, no markdown): "
    '{"findings": [{"issue": "...", "severity": "critical|high|medium|low", "suggestion": "..."}], '
    '"incorporated": ["actionable item 1"], '
    '"skipped": [{"item": "...", "rationale": "at least 30 chars explaining why not applicable"}]}'
)

_CHATGPT_SYSTEM = (
    "You are a creative director performing a peer review for KAI, an autonomous AI system. "
    "Review the provided design ideas, concepts, or direction for: creative coherence and concept "
    "strength, user experience and emotional resonance, brand alignment and differentiation, "
    "clarity and actionability of direction. "
    "Respond with a JSON object (raw JSON, no markdown): "
    '{"findings": [{"observation": "...", "impact": "high|medium|low", "suggestion": "..."}], '
    '"incorporated": ["actionable item 1"], '
    '"skipped": [{"item": "...", "rationale": "at least 30 chars explaining why not applicable"}]}'
)


def _litellm_key() -> str:
    p = Path("/run/wp_secrets/litellm_master_key.txt")
    return p.read_text().strip() if p.exists() else os.environ.get("LITELLM_MASTER_KEY", "")


def _topic_slug(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:60]


def _store_review(slug: str, payload: dict) -> None:
    _VAULT_REVIEWS.mkdir(parents=True, exist_ok=True)
    (_VAULT_REVIEWS / f"{slug}.json").write_text(json.dumps(payload, indent=2))


def _call_litellm(model: str, system: str, content: str) -> tuple[bool, str, int, int]:
    """Returns (ok, text_or_error, input_tokens, output_tokens)."""
    key = _litellm_key()
    r = safe_request(
        "POST", f"{_LITELLM_URL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        },
        timeout=90,
    )
    if not r.ok or not isinstance(r.data, dict):
        return False, f"LiteLLM HTTP {r.status_code}: {r.body_preview or r.error}", 0, 0
    choices = r.data.get("choices", [])
    if not choices:
        return False, "LiteLLM returned no choices", 0, 0
    text = choices[0].get("message", {}).get("content", "")
    usage = r.data.get("usage", {}) or {}
    return True, text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def _peer_review(model: str, system: str, reviewer: str, content: str, topic: str) -> CapabilityResult:
    ok, text, input_tokens, output_tokens = _call_litellm(model, system, content)
    if not ok:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "litellm_error", "detail": text})

    _in_rates = {"o4-mini": 1.10, "gpt-4o": 2.50}
    _out_rates = {"o4-mini": 4.40, "gpt-4o": 10.0}
    _cost_usd = (input_tokens * _in_rates.get(model, 2.50) + output_tokens * _out_rates.get(model, 10.0)) / 1_000_000
    _track_usage(reviewer, input_tokens, output_tokens, "openai", model,
                 trigger_source=f"orchestrator:peer_review:{reviewer}")
    # Strip markdown fences if model wrapped the JSON
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    # Strip trailing commas before ] or } (common LLM JSON quirk)
    clean = re.sub(r",\s*([\]\}])", r"\1", clean)
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "json_parse_error", "raw": text[:500]})

    slug = _topic_slug(topic)
    record = {
        "topic": topic,
        "slug": slug,
        "reviewer": reviewer,
        "model": model,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "content_preview": content[:300],
        "findings": parsed.get("findings", []),
        "incorporated": parsed.get("incorporated", []),
        "skipped": parsed.get("skipped", []),
    }
    _store_review(slug, record)

    return CapabilityResult(
        ok=True, status="succeeded",
        provider="openai", model=model, cost_usd=_cost_usd,
        data={
            "topic": topic,
            "reviewer": reviewer,
            "findings_count": len(record["findings"]),
            "incorporated_count": len(record["incorporated"]),
            "skipped_count": len(record["skipped"]),
            "vault_path": f"vault/60_Council/reviews/{slug}.json",
        },
        verification={
            "verified": True,
            "method": "peer_review_stored",
            "peer_review": {
                "incorporated": record["incorporated"],
                "skipped": record["skipped"],
            },
        },
    )


@capability("model_peer.codex.review")
def codex_review(content: str, topic: str, category: str = "architecture", **_) -> CapabilityResult:
    """Architectural peer review via o4-mini. Categories: architecture, performance, security_privacy."""
    return _peer_review("o4-mini", _CODEX_SYSTEM, "codex", content, topic)


@capability("model_peer.chatgpt.review")
def chatgpt_review(content: str, topic: str, **_) -> CapabilityResult:
    """Creative peer review via gpt-4o. For design ideas, concepts, or mood direction."""
    return _peer_review("gpt-4o", _CHATGPT_SYSTEM, "chatgpt", content, topic)


@capability("model_peer.get_review")
def get_review(topic: str, **_) -> CapabilityResult:
    """Retrieve a stored peer review by topic slug (fuzzy match)."""
    slug = _topic_slug(topic)
    exact = _VAULT_REVIEWS / f"{slug}.json"
    if exact.exists():
        return CapabilityResult(ok=True, status="succeeded",
                                data=json.loads(exact.read_text()))

    if _VAULT_REVIEWS.exists():
        matches = [f for f in _VAULT_REVIEWS.glob("*.json") if slug in f.stem]
        if matches:
            # Most recently modified match
            best = max(matches, key=lambda f: f.stat().st_mtime)
            return CapabilityResult(ok=True, status="succeeded",
                                    data=json.loads(best.read_text()))

    return CapabilityResult(ok=False, status="failed_recoverable",
                            error={"type": "not_found", "topic": topic, "slug": slug})

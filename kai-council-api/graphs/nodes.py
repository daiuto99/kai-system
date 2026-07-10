import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from council_config import VAULT_PATH, _track_usage
from complexity import _classify_complexity, _get_advisor_config
from persona import load_persona
from providers import _call_ollama, _call_litellm
from router import _run_agentic_loop, KAI_TOOLS, DIRECTOR_TOOLS
from graphs.state import KAIState

logger = logging.getLogger(__name__)

ORG_FILE       = VAULT_PATH / "00_System" / "org.json"
ORG_MODEL_FILE = VAULT_PATH / "00_System" / "org_model.json"
PRIVACY_ADVISORS = {"ember", "doc"}


def _load_org() -> list:
    return json.loads(ORG_FILE.read_text(encoding="utf-8"))["members"]


def _load_org_model() -> dict:
    if ORG_MODEL_FILE.exists():
        return json.loads(ORG_MODEL_FILE.read_text(encoding="utf-8"))
    return {}


def channel_router(state: KAIState) -> KAIState:
    channel = state["channel"].lstrip("#")
    org = _load_org()

    member = next((m for m in org if m.get("channel") == channel), None)
    if not member:
        member = next((m for m in org if m["id"] == channel), None)
    if not member:
        member = next((m for m in org if m["id"] == "kai"), None)
        reason = f"Unknown channel '{channel}', fallback to KAI"
    else:
        reason = f"Channel '{channel}' → {member['id']} ({member.get('tier', 'unknown')})"

    ts = datetime.now(timezone.utc).isoformat()
    return {
        **state,
        "target_advisor": member["id"],
        "routing_reason": reason,
        "audit_log": state.get("audit_log", []) + [{
            "ts": ts,
            "node": "channel_router",
            "action": "route",
            "channel": channel,
            "target": member["id"],
            "reason": reason,
        }],
    }


def advisor_node(state: KAIState) -> KAIState:
    advisor = state["target_advisor"]
    channel = state["channel"].lstrip("#")

    system_prompt = load_persona(advisor, channel)

    messages = list(state.get("history", [])[-10:])
    messages.append({"role": "user", "content": state["message"]})

    complexity = _classify_complexity(state["message"])
    force_privacy = advisor in PRIVACY_ADVISORS or state.get("privacy_mode", False)

    if advisor == "kai":
        model_map = {"deep": "claude-opus-4-6", "simple": "claude-haiku-4-5-20251001"}
        adv_cfg = {"provider": "anthropic", "model": model_map.get(complexity, "claude-sonnet-4-6")}
    else:
        adv_cfg = _get_advisor_config(advisor)
        if adv_cfg.get("provider") == "anthropic":
            if complexity == "deep":
                adv_cfg = dict(adv_cfg, model="claude-opus-4-6")
            elif complexity == "simple":
                adv_cfg = dict(adv_cfg, model="claude-haiku-4-5-20251001")

    if force_privacy:
        adv_cfg = {"provider": "ollama", "model": "qwen2.5:3b"}

    provider = adv_cfg.get("provider", "anthropic")
    model = adv_cfg.get("model", "claude-sonnet-4-6")
    actual_provider = provider
    actual_model = model

    reply = ""
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0

    if provider == "anthropic":
        org = _load_org()
        member = next((m for m in org if m["id"] == advisor), None)
        tier = member["tier"] if member else "advisor"
        if tier == "orchestrator":
            tools = KAI_TOOLS
        elif tier == "director":
            tools = DIRECTOR_TOOLS
        else:
            tools = []
        reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _run_agentic_loop(
            messages, tools, model, system_prompt, advisor
        )

    elif provider == "ollama":
        try:
            reply, input_tokens, output_tokens = _call_ollama(model, system_prompt, messages)
        except Exception as e:
            logger.exception("ollama error in advisor_node: %s", e)
            if force_privacy:
                reply = "Privacy mode is active — local model unavailable. Please try again shortly."
            else:
                fallback = adv_cfg.get("fallback_model", "claude-sonnet-4-6")
                actual_provider = "anthropic"
                actual_model = fallback
                reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _run_agentic_loop(
                    messages, [], fallback,
                    system_prompt + f"\n\n[Note: Ollama unavailable ({e}), using cloud fallback]",
                    advisor,
                )

    else:
        try:
            reply, input_tokens, output_tokens = _call_litellm(model, system_prompt, messages)
        except Exception as e:
            logger.exception("litellm error in advisor_node: %s", e)
            fallback = adv_cfg.get("fallback_model", "claude-sonnet-4-6")
            actual_provider = "anthropic"
            actual_model = fallback
            reply, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens = _run_agentic_loop(
                messages, [], fallback, system_prompt, advisor
            )

    _track_usage(advisor, input_tokens, output_tokens, actual_provider, actual_model,
                 trigger_source=f"graph:advisor_node:{advisor}",
                 cache_read_tokens=cache_read_tokens,
                 cache_creation_tokens=cache_creation_tokens)

    ts = datetime.now(timezone.utc).isoformat()
    return {
        **state,
        "advisor_reply": reply,
        "final_reply": reply,
        "model_used": actual_model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "audit_log": state.get("audit_log", []) + [{
            "ts": ts,
            "node": "advisor_node",
            "action": "response",
            "advisor": advisor,
            "model": actual_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }],
    }

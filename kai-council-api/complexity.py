import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_CONFIG_FILE = Path("/vault/00_System/model_config.json")

TOOL_SIGNALS = [
    "look up", "find ", "search", "contact", "phone", "number", "email",
    "calendar", "schedule", "what's", "who is", "who's", "send ",
    "draft", "create ", "remind", "project", "task",
]


def _load_model_config() -> dict:
    if MODEL_CONFIG_FILE.exists():
        try:
            return json.loads(MODEL_CONFIG_FILE.read_text())
        except Exception as e:
            logger.exception("load_model_config: %s", e)
    return {}


def _get_advisor_config(advisor: str) -> dict:
    config = _load_model_config()
    return config.get("advisors", {}).get(advisor, {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
    })


def _classify_complexity(message: str) -> str:
    """Classify message complexity: simple | standard | deep."""
    msg = message.lower().strip()
    word_count = len(msg.split())

    # Unambiguous deep signals — phrases that only appear in genuinely heavy requests
    DEEP_SIGNALS = [
        "major decision", "life decision", "deep analysis", "strategy session",
        "really important", "most important", "change my life",
        "weigh the options", "pros and cons", "comprehensive", "thorough analysis",
    ]
    # Conditional deep — only deep when the message has real substance behind it
    DEEP_CONDITIONAL = ["should i", "help me decide", "what would you recommend"]

    SIMPLE_SIGNALS = [
        "add task", "add to", "parking lot",
        "capture this", "make a note", "note that",
        "list my", "what are my", "show me my",
    ]

    if any(s in msg for s in DEEP_SIGNALS):
        return "deep"

    # Simple signals checked before conditional deep — "should i add this to parking lot" = simple
    if any(s in msg for s in SIMPLE_SIGNALS):
        return "simple"

    if any(s in msg for s in DEEP_CONDITIONAL):
        if word_count > 7:
            return "deep"
        return "standard"  # fragment like "help me decide" — not simple, not deep yet

    if any(s in msg for s in TOOL_SIGNALS):
        return "standard"

    if word_count <= 8:
        return "simple"

    return "standard"

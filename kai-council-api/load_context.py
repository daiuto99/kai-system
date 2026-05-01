import logging
from pathlib import Path
from council_config import COUNCIL_PATH

logger = logging.getLogger(__name__)

# Channels that share history (chief→kai rename transition)
CHANNEL_ALIASES = {
    "kai": ["kai", "chief"],
    "chief": ["chief", "kai"],
}


def load_session_memory(channel: str, n: int = 2) -> str:
    """Return the last n session summary files for a channel as a formatted block."""
    channels_to_check = CHANNEL_ALIASES.get(channel, [channel])

    all_files = []
    for ch in channels_to_check:
        sessions_dir = COUNCIL_PATH / "sessions" / ch
        if sessions_dir.exists():
            all_files.extend(sessions_dir.glob("*.md"))

    if not all_files:
        return ""

    # Sort by filename (ISO timestamp) and take last n
    recent = sorted(all_files, key=lambda f: f.name)[-n:]
    parts = [f.read_text(encoding="utf-8") for f in recent]
    return "\n\n---\n\n".join(parts)

import logging
from pathlib import Path
from council_config import COUNCIL_PATH, WORKER_URL

logger = logging.getLogger(__name__)


def load_session_memory(channel: str, n: int = 2) -> str:
    """Return the last n session summary files for a channel as a formatted block."""
    all_files = []
    sessions_dir = COUNCIL_PATH / "sessions" / channel
    if sessions_dir.exists():
        all_files.extend(sessions_dir.glob("*.md"))

    if not all_files:
        return ""

    recent = sorted(all_files, key=lambda f: f.name)[-n:]
    parts = [f.read_text(encoding="utf-8") for f in recent]
    return "\n\n---\n\n".join(parts)


def load_system_state() -> str:
    """Compact system state block injected into KAI's system prompt each turn.

    Calls /system/ops-state on the worker API. Returns empty string on failure
    so the persona loads even if the worker is temporarily unreachable.
    """
    try:
        import httpx
        r = httpx.get(f"{WORKER_URL}/system/ops-state", timeout=5)
        if r.status_code != 200:
            return ""
        data = r.json()
    except Exception as e:
        logger.warning("load_system_state: worker unreachable: %s", e)
        return ""

    lines = ["<system_state>"]

    failing = data.get("failing_invariants", {})
    if failing:
        lines.append(f"FAILING INVARIANTS ({len(failing)}):")
        for key, detail in failing.items():
            lines.append(f"  - {key}: {detail}")
    else:
        lines.append("All invariants passing.")

    backup = data.get("backup", {})
    b_status = backup.get("status", "unknown")
    b_detail = backup.get("detail", "")
    if b_status == "ok":
        lines.append(f"Backup: OK — {b_detail}")
    elif b_status in ("failing", "stale"):
        lines.append(f"Backup: {b_status.upper()} — {b_detail}")
        lines.append("  Use run_backup_now to trigger an immediate backup.")
    else:
        lines.append(f"Backup: {b_status}")

    if data.get("backup_trigger_pending"):
        lines.append("Backup: trigger pending — host cron will run within 5min.")

    lines.append("</system_state>")
    return "\n".join(lines)

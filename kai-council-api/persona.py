import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from fastapi import HTTPException
from council_config import VAULT_PATH, COUNCIL_PATH
from load_context import load_session_memory, load_system_state, load_org_model_context

logger = logging.getLogger(__name__)

PERSONA_HEALTH_FILE = VAULT_PATH / "_persona_health.json"


def _record_persona_health(failure_key: str, detail: str) -> None:
    """KAI-466: record explicit degraded-mode signal when a config loader raises.

    The KAI-458 persona invariant (inv_persona_assembly, kai-scheduler) reads
    this file plus checks block presence in load_persona() — together they
    surface the failure as CRITICAL §6 to #devops within one watchdog tick.
    """
    try:
        PERSONA_HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        if PERSONA_HEALTH_FILE.exists():
            try:
                state = json.loads(PERSONA_HEALTH_FILE.read_text())
            except Exception:
                state = {}
        state[failure_key] = {
            "detail": str(detail)[:500],
            "at": datetime.now(timezone.utc).isoformat(),
        }
        PERSONA_HEALTH_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.error("persona_health write failed: %s", e)


def load_persona(advisor: str, channel: str = None) -> str:
    advisor_dir = COUNCIL_PATH / advisor
    persona_file = advisor_dir / f"{advisor.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail=f"Persona not found: {advisor}")

    keystone_file = VAULT_PATH / '00_System' / 'KEYSTONE.md'
    bp_file = VAULT_PATH / '00_System' / 'business_profile.md'
    ctx_parts = []
    if keystone_file.exists(): ctx_parts.append(keystone_file.read_text(encoding='utf-8'))
    if bp_file.exists(): ctx_parts.append(bp_file.read_text(encoding='utf-8'))
    parts = []
    if ctx_parts:
        combined = '\n\n---\n\n'.join(ctx_parts)
        parts.append('<background_context>\n' + combined + '\n</background_context>')

    # CONTEXT_SPEC.md finding 2.3.2 / F6: minute-granularity datetime must sit
    # below the cache_control breakpoint (router.py splits after
    # </background_context>) — placing it at position 0 voided the cache nearly
    # every turn.
    now = datetime.now(ZoneInfo('America/New_York'))
    from datetime import timedelta
    date_map_lines = []
    for i in range(14):
        d = (now + timedelta(days=i)).date()
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else "")
        suffix = f" ({label})" if label else ""
        date_map_lines.append(f"  {d.isoformat()} = {d.strftime('%A')}{suffix}")
    date_ref = "<date_reference>\n"
    date_ref += f"Today is {now.strftime('%A, %B %d, %Y')}. Use ONLY this table for day names — never calculate:\n"
    date_ref += "\n".join(date_map_lines)
    date_ref += "\n</date_reference>"
    parts.append(f'<current_datetime>{now.strftime("%A, %B %d, %Y at %I:%M %p ET")}</current_datetime>')
    parts.append(date_ref)

    # KAI always gets live system state — knows what's broken before Leo asks.
    # KAI-466: catch structural loader failures here explicitly; record degraded
    # mode to /vault/_persona_health.json. The persona invariant + the block
    # presence check in inv_persona_assembly surface the failure to #devops.
    if advisor == "kai":
        try:
            system_state = load_system_state()
            if system_state:
                parts.append(system_state)
            else:
                _record_persona_health("system_state_empty",
                                       "loader returned empty — worker degraded")
        except Exception as e:
            logger.error("persona: load_system_state raised — degraded: %s", e)
            _record_persona_health("system_state_load_failed", str(e))

        try:
            org_model_ctx = load_org_model_context()
            if org_model_ctx:
                parts.append(org_model_ctx)
            else:
                _record_persona_health("org_model_empty",
                                       "loader returned empty — org_model.json missing")
        except Exception as e:
            logger.error("persona: load_org_model_context raised — degraded: %s", e)
            _record_persona_health("org_model_load_failed", str(e))

    parts.append(persona_file.read_text(encoding="utf-8"))

    org_file_map = {
        "creative": advisor_dir / "CreativeOrg.md",
        "dev": advisor_dir / "DevOrg.md",
    }
    org_file = org_file_map.get(advisor)
    if org_file and org_file.exists():
        parts.append('<organization_structure>\n' + org_file.read_text(encoding="utf-8") + '\n</organization_structure>')

    build_profile_file = advisor_dir / "BUILD_PROFILE.md"
    if build_profile_file.exists():
        parts.append('<build_profile>\n' + build_profile_file.read_text(encoding="utf-8") + '\n</build_profile>')

    style_guide = COUNCIL_PATH / "JARVIS_STYLE_GUIDE.md"
    if style_guide.exists():
        parts.append(style_guide.read_text(encoding="utf-8"))

    context_file = advisor_dir / "context.md"
    if context_file.exists():
        parts.append(context_file.read_text(encoding="utf-8"))

    session_memory = load_session_memory(channel or advisor)
    if session_memory:
        parts.append('<session_memory>\n' + session_memory + '\n</session_memory>')

    if channel == "beats-personal" and (advisor_dir / "deep.md").exists():
        parts.append((advisor_dir / "deep.md").read_text(encoding="utf-8"))

    if advisor == "ember" and (advisor_dir / "insights.md").exists():
        insights = (advisor_dir / "insights.md").read_text(encoding="utf-8")
        if insights.strip():
            parts.append(insights)

    return "\n\n---\n\n".join(parts)

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from fastapi import HTTPException
from council_config import VAULT_PATH, COUNCIL_PATH
from load_context import load_session_memory

logger = logging.getLogger(__name__)


def load_persona(advisor: str, channel: str = None) -> str:
    advisor_dir = COUNCIL_PATH / advisor
    persona_file = advisor_dir / f"{advisor.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail=f"Persona not found: {advisor}")

    now = datetime.now(ZoneInfo('America/New_York'))
    # Pre-compute 14-day date map so KAI never has to calculate day-of-week
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
    parts = [f'<current_datetime>{now.strftime("%A, %B %d, %Y at %I:%M %p ET")}</current_datetime>', date_ref]
    keystone_file = VAULT_PATH / '00_System' / 'KEYSTONE.md'
    bp_file = VAULT_PATH / '00_System' / 'business_profile.md'
    ctx_parts = []
    if keystone_file.exists(): ctx_parts.append(keystone_file.read_text(encoding='utf-8'))
    if bp_file.exists(): ctx_parts.append(bp_file.read_text(encoding='utf-8'))
    if ctx_parts:
        combined = '\n\n---\n\n'.join(ctx_parts)
        parts.append('<background_context>\n' + combined + '\n</background_context>')

    parts.append(persona_file.read_text(encoding="utf-8"))

    # Inject JARVIS communication standard — applies to all advisors
    style_guide = COUNCIL_PATH / "JARVIS_STYLE_GUIDE.md"
    if style_guide.exists():
        parts.append(style_guide.read_text(encoding="utf-8"))

    context_file = advisor_dir / "context.md"
    if context_file.exists():
        parts.append(context_file.read_text(encoding="utf-8"))

    # Inject cross-session memory (KAI-28/30/22)
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

import logging
from pathlib import Path
from fastapi import HTTPException
from council_config import VAULT_PATH, COUNCIL_PATH

logger = logging.getLogger(__name__)


def load_persona(advisor: str, channel: str = None) -> str:
    advisor_dir = COUNCIL_PATH / advisor
    persona_file = advisor_dir / f"{advisor.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail=f"Persona not found: {advisor}")

    parts = []
    keystone_file = VAULT_PATH / '00_System' / 'KEYSTONE.md'
    bp_file = VAULT_PATH / '00_System' / 'business_profile.md'
    ctx_parts = []
    if keystone_file.exists(): ctx_parts.append(keystone_file.read_text(encoding='utf-8'))
    if bp_file.exists(): ctx_parts.append(bp_file.read_text(encoding='utf-8'))
    if ctx_parts:
        combined = '\n\n---\n\n'.join(ctx_parts)
        parts.append('<background_context>\n' + combined + '\n</background_context>')

    parts.append(persona_file.read_text(encoding="utf-8"))

    context_file = advisor_dir / "context.md"
    if context_file.exists():
        parts.append(context_file.read_text(encoding="utf-8"))

    if channel == "beats-personal" and (advisor_dir / "deep.md").exists():
        parts.append((advisor_dir / "deep.md").read_text(encoding="utf-8"))

    if advisor == "ember" and (advisor_dir / "insights.md").exists():
        insights = (advisor_dir / "insights.md").read_text(encoding="utf-8")
        if insights.strip():
            parts.append(insights)

    return "\n\n---\n\n".join(parts)

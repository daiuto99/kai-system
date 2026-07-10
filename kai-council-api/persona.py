import hashlib
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


def _register(blocks: list, text: str, stability: str) -> None:
    """CONTEXT_SPEC §7 F6 enforcement: a stable block can never be registered
    after a volatile one. Raises rather than silently caching a volatile leak —
    a code-level invariant, not a review checklist."""
    if not text:
        return
    if blocks and blocks[-1][1] == "volatile" and stability == "stable":
        raise AssertionError(
            "CONTEXT_SPEC §7/F6 violation: a stable block was registered after a "
            "volatile block during persona assembly — the cache breakpoint would "
            "cache volatile content. This is a code bug, not a data problem."
        )
    blocks.append((text, stability))


def assemble_prompt(advisor: str, channel: str = None) -> tuple[str, str, str]:
    """Builds the system prompt as explicit stable/volatile blocks per CONTEXT_SPEC
    §7. Returns (full_prompt, stable_prefix, stable_prefix_hash). The caller uses
    stable_prefix's length as the cache_control breakpoint — no substring search."""
    advisor_dir = COUNCIL_PATH / advisor
    persona_file = advisor_dir / f"{advisor.upper()}.md"
    if not persona_file.exists():
        raise HTTPException(status_code=404, detail=f"Persona not found: {advisor}")

    blocks: list[tuple[str, str]] = []

    # ── STABLE (cached) — persona/voice + KEYSTONE/business/org/style, §7 items 2-3 ──
    keystone_file = VAULT_PATH / '00_System' / 'KEYSTONE.md'
    bp_file = VAULT_PATH / '00_System' / 'business_profile.md'
    ctx_parts = []
    if keystone_file.exists(): ctx_parts.append(keystone_file.read_text(encoding='utf-8'))
    if bp_file.exists(): ctx_parts.append(bp_file.read_text(encoding='utf-8'))
    if ctx_parts:
        combined = '\n\n---\n\n'.join(ctx_parts)
        _register(blocks, '<background_context>\n' + combined + '\n</background_context>', "stable")

    _register(blocks, persona_file.read_text(encoding="utf-8"), "stable")

    org_file_map = {
        "creative": advisor_dir / "CreativeOrg.md",
        "dev": advisor_dir / "DevOrg.md",
    }
    org_file = org_file_map.get(advisor)
    if org_file and org_file.exists():
        _register(blocks, '<organization_structure>\n' + org_file.read_text(encoding="utf-8") + '\n</organization_structure>', "stable")

    if advisor == "kai":
        try:
            org_model_ctx = load_org_model_context()
            if org_model_ctx:
                _register(blocks, org_model_ctx, "stable")
            else:
                _record_persona_health("org_model_empty",
                                       "loader returned empty — org_model.json missing")
        except Exception as e:
            logger.error("persona: load_org_model_context raised — degraded: %s", e)
            _record_persona_health("org_model_load_failed", str(e))

    build_profile_file = advisor_dir / "BUILD_PROFILE.md"
    if build_profile_file.exists():
        _register(blocks, '<build_profile>\n' + build_profile_file.read_text(encoding="utf-8") + '\n</build_profile>', "stable")

    style_guide = COUNCIL_PATH / "JARVIS_STYLE_GUIDE.md"
    if style_guide.exists():
        _register(blocks, style_guide.read_text(encoding="utf-8"), "stable")

    # context.md is rolling but changes only on deliberate session-close writes,
    # not continuously — stable per §7 item 5 ("freshness-dated entries that
    # churn get demoted to volatile"; this doesn't churn within a session).
    context_file = advisor_dir / "context.md"
    if context_file.exists():
        _register(blocks, context_file.read_text(encoding="utf-8"), "stable")

    if channel == "beats-personal" and (advisor_dir / "deep.md").exists():
        _register(blocks, (advisor_dir / "deep.md").read_text(encoding="utf-8"), "stable")

    if advisor == "ember" and (advisor_dir / "insights.md").exists():
        insights = (advisor_dir / "insights.md").read_text(encoding="utf-8")
        if insights.strip():
            _register(blocks, insights, "stable")

    # ── VOLATILE (uncached) — §7 items 6-7: datetime, live system state ──────────
    # CONTEXT_SPEC.md finding 2.3.2 / F6: minute-granularity datetime must sit
    # below the cache_control breakpoint — placing it in the stable prefix voided
    # the cache nearly every turn. Registered volatile explicitly, not by position.
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
    _register(blocks, f'<current_datetime>{now.strftime("%A, %B %d, %Y at %I:%M %p ET")}</current_datetime>', "volatile")
    _register(blocks, date_ref, "volatile")

    # KAI always gets live system state — knows what's broken before Leo asks.
    # KAI-466: catch structural loader failures here explicitly; record degraded
    # mode to /vault/_persona_health.json. The persona invariant + the block
    # presence check in inv_persona_assembly surface the failure to #devops.
    if advisor == "kai":
        try:
            system_state = load_system_state()
            if system_state:
                _register(blocks, system_state, "volatile")
            else:
                _record_persona_health("system_state_empty",
                                       "loader returned empty — worker degraded")
        except Exception as e:
            logger.error("persona: load_system_state raised — degraded: %s", e)
            _record_persona_health("system_state_load_failed", str(e))

    session_memory = load_session_memory(channel or advisor)
    if session_memory:
        _register(blocks, '<session_memory>\n' + session_memory + '\n</session_memory>', "volatile")

    stable_parts = [t for t, s in blocks if s == "stable"]
    volatile_parts = [t for t, s in blocks if s == "volatile"]
    stable_text = "\n\n---\n\n".join(stable_parts)
    volatile_text = "\n\n---\n\n".join(volatile_parts)
    full_prompt = stable_text + ("\n\n---\n\n" + volatile_text if volatile_text else "")
    stable_prefix_hash = hashlib.sha256(stable_text.encode("utf-8")).hexdigest()

    return full_prompt, stable_text, stable_prefix_hash


def load_persona(advisor: str, channel: str = None) -> str:
    """Backward-compatible wrapper — full concatenated prompt. Callers that only
    need block-presence checks (internal_persona_check) keep using this; router.py's
    real cache-aware call path uses assemble_prompt() for the stable/volatile split."""
    full_prompt, _stable, _hash = assemble_prompt(advisor, channel)
    return full_prompt

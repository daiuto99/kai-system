import logging
from pathlib import Path  # noqa: F401
from council_config import COUNCIL_PATH, VAULT_PATH, WORKER_URL  # noqa: F401
import function_map as fm

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

    KAI-466 raise-don't-swallow: only network/HTTP failures degrade to "".
    Structural failures (JSON decode, missing fields, AttributeError, NameError)
    propagate so the caller records a visible degraded-mode signal instead of
    silently producing a persona without system_state.
    """
    try:
        import httpx
        r = httpx.get(f"{WORKER_URL}/system/ops-state", timeout=5)
        if r.status_code != 200:
            logger.warning("load_system_state: worker returned %s — degraded", r.status_code)
            return ""
        data = r.json()
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
        logger.warning("load_system_state: worker unreachable (%s) — degraded", type(e).__name__)
        return ""
    except Exception as e:
        logger.error("load_system_state: structural failure: %s", e)
        raise

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


def load_org_model_context() -> str:
    """Inject org model routing rules and advisor domain map into KAI's context.

    KAI-466 raise-don't-swallow: file-genuinely-missing returns ""; everything
    else (JSON parse error, schema mismatch, NameError like KAI-457) raises so
    the caller / persona invariant sees the failure instead of producing a KAI
    persona without org_model routing rules.
    """
    try:
        cd = (fm.get_governance("creative_agency") or {}).get("director", "creative")
        ed = (fm.get_governance("engineering_agency") or {}).get("director", "dev")
        bt = fm.get_first_receiver_for_bug()

        lines = ["<org_model>"]
        lines.append("You are the PM and system orchestrator. Leo is the client.")
        lines.append(f"Creative agency director: {cd} (sign-off required before KAI review)")
        lines.append(f"Engineering agency director: {ed} (sign-off required before KAI review)")
        lines.append("DevOps: autonomous on health/maintenance. Escalates structural changes.")
        lines.append(f"Bug triage: all bugs start at {bt} (KAI internal role — classifies + routes via bug.routing)")

        lines.append("")
        lines.append("Advisor domain routing — pull in the right advisor when working in their domain:")
        for entry in fm.list_advisor_domains():
            kw = ", ".join(entry["keywords"][:5])
            lines.append(f"  {entry['domain']} -> {entry['advisor']} (triggers: {kw})")

        direct = fm.list_direct_advisors()
        if direct:
            lines.append(f"Direct advisors (Leo also talks to them directly): {', '.join(direct)}")

        lines.append("")
        lines.append("Task routing:")
        for rtype, rule in fm.list_routing_rules().items():
            owner = rule.get("owner") or rule.get("pm") or rule.get("first_receiver", "")
            gate = rule.get("gate", "none")
            lines.append(f"  {rtype}: owner={owner}, gate={gate}")

        lines.append("</org_model>")
        return "\n".join(lines)
    except Exception as e:
        logger.error("load_org_model_context: structural failure: %s", e)
        raise

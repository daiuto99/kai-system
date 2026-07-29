"""
Focus brief generator — pulls Todoist tasks, builds Top 3 / Next 5 brief,
posts to #kai-focus in Slack, and writes to kai/context.md for morning check-in.
"""
from pathlib import Path
from datetime import date
import httpx
import os
import anthropic

from usage_tracker import _track_usage


def load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    if p.exists():
        return p.read_text().strip()
    return os.environ.get(name.upper(), "")


def get_todoist_tasks() -> dict:
    """Fetch today's due + overdue tasks from Todoist."""
    token = load_secret("todoist_api_key")
    if not token:
        return {"today": [], "overdue": []}

    headers = {"Authorization": f"Bearer {token}"}
    today = date.today().isoformat()

    with httpx.Client() as client:
        r = client.get(
            "https://api.todoist.com/api/v1/tasks",
            headers=headers,
            timeout=15.0,
        )
        r.raise_for_status()
        all_tasks = r.json().get("results", [])

    today_tasks = [
        t["content"] for t in all_tasks
        if t.get("due") and t["due"]["date"] == today
    ]
    overdue_tasks = [
        t["content"] for t in all_tasks
        if t.get("due") and t["due"]["date"] < today
    ]

    return {"today": today_tasks, "overdue": overdue_tasks}


def load_kai_close_notes(vault_path: Path) -> str:
    """Load yesterday's close notes from kai context."""
    context_file = vault_path / "60_Council" / "kai" / "context.md"
    if not context_file.exists():
        return ""
    content = context_file.read_text(encoding="utf-8")
    # Extract the close notes section if present
    if "## Close Notes" in content:
        return content.split("## Close Notes")[-1].strip()
    return ""


def build_focus_brief(tasks: dict, close_notes: str) -> str:
    """Use Claude Haiku to build the Top 3 / Next 5 brief."""
    api_key = load_secret("anthropic_api_key")
    client = anthropic.Anthropic(api_key=api_key)

    today_list = "\n".join(f"- {t}" for t in tasks["today"]) or "- (none)"
    overdue_list = "\n".join(f"- {t}" for t in tasks["overdue"]) or "- (none)"
    close_section = f"Yesterday's close notes:\n{close_notes}" if close_notes else ""

    prompt = f"""You are KAI, building a concise daily focus brief.

Today's date: {date.today().strftime('%A, %B %d')}

Tasks due today:
{today_list}

Overdue tasks:
{overdue_list}

{close_section}

Build a brief with exactly this format:

**Good morning. Here's your focus for today.**

**Top 3** — the 3 most important things to move today:
1. [task]
2. [task]
3. [task]

**Next 5** — on deck after the Top 3:
- [task]
- [task]
- [task]
- [task]
- [task]

**Carried over** — overdue items needing attention:
[list any overdue, or "None" if clear]

Keep it tight. No preamble. Just the brief."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    _track_usage("focus", response.usage.input_tokens, response.usage.output_tokens,
                 provider="anthropic", model="claude-haiku-4-5-20251001",
                 trigger_source="worker:focus",
                 cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                 cache_creation_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
    return response.content[0].text


def post_to_slack(brief: str, channel_id: str) -> None:
    """AR-5.3: rerouted to Telegram (sole surface). Name/signature kept so call
    sites stay unchanged; channel_id ignored; fail-soft via the shared chokepoint."""
    from tg_alert import tg_alert
    tg_alert(brief)


def write_to_kai_context(brief: str, vault_path: Path) -> None:
    """Write today's brief to kai context so morning check-in starts informed."""
    context_file = vault_path / "60_Council" / "kai" / "context.md"
    existing = context_file.read_text(encoding="utf-8") if context_file.exists() else ""

    today = date.today().isoformat()
    section = f"\n\n## Daily Brief — {today}\n{brief}\n"

    # Replace previous daily brief section or append
    if "## Daily Brief" in existing:
        import re
        existing = re.sub(r"## Daily Brief.*", "", existing, flags=re.DOTALL).strip()

    context_file.write_text(existing + section, encoding="utf-8")


def run_focus_brief(kai_focus_channel_id: str, vault_path: Path = Path("/vault")) -> dict:
    """Full pipeline: fetch tasks → build brief → post to Slack → write to vault."""
    tasks = get_todoist_tasks()
    close_notes = load_kai_close_notes(vault_path)
    brief = build_focus_brief(tasks, close_notes)
    post_to_slack(brief, kai_focus_channel_id)
    write_to_kai_context(brief, vault_path)
    return {
        "status": "ok",
        "tasks_today": len(tasks["today"]),
        "tasks_overdue": len(tasks["overdue"]),
    }

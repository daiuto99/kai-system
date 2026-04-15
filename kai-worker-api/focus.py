"""
Focus brief generator — pulls Todoist tasks, builds Top 3 / Next 5 brief,
posts to #kai-focus in Slack, and writes to chief/context.md for morning check-in.
"""
from pathlib import Path
from datetime import date
import httpx
import os
import anthropic


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


def load_chief_close_notes(vault_path: Path) -> str:
    """Load yesterday's close notes from chief context."""
    context_file = vault_path / "60_Council" / "chief" / "context.md"
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
    return response.content[0].text


def post_to_slack(brief: str, channel_id: str) -> None:
    """Post the brief to Slack."""
    token = load_secret("slack_bot_token")
    with httpx.Client() as client:
        r = client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel_id, "text": brief, "mrkdwn": True},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Slack error: {data.get('error')}")


def write_to_chief_context(brief: str, vault_path: Path) -> None:
    """Write today's brief to chief context so morning check-in starts informed."""
    context_file = vault_path / "60_Council" / "chief" / "context.md"
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
    close_notes = load_chief_close_notes(vault_path)
    brief = build_focus_brief(tasks, close_notes)
    post_to_slack(brief, kai_focus_channel_id)
    write_to_chief_context(brief, vault_path)
    return {
        "status": "ok",
        "tasks_today": len(tasks["today"]),
        "tasks_overdue": len(tasks["overdue"]),
    }

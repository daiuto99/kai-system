#!/usr/bin/env python3
"""
daily_brief — self-contained daily focus brief generator (Hermes skill core).

Strangler-fig port of kai-worker-api/focus.py. Same inputs (Todoist today+overdue
tasks + KAI close notes), same LLM (claude-haiku-4-5), same Top 3 / Next 5 /
Carried-over format. Unlike focus.py it is standalone (no kai-worker-api import,
configurable secrets dir + vault path) so it can run under the Hermes hardened
profile on cron.

Modes:
  --mode shadow  (default) : write the brief to a shadow sink file only. NEVER posts to Slack.
  --mode live              : post to the real Slack channel (--channel) + write vault context.

Output: a schema-valid JSON envelope (kai.daily_brief.v1) on stdout, so a caller /
harness can evaluate parity deterministically. See references/brief-contract.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

SCHEMA = "kai.daily_brief.v1"
LLM_MODEL = "claude-haiku-4-5-20251001"
REQUIRED_SECTIONS = ("top3", "next5", "carried_over")


def load_secret(name: str, secrets_dir: str) -> str:
    """Read a secret from <secrets_dir>/<name> (or <name>.txt), then env NAME."""
    base = Path(secrets_dir)
    for candidate in (base / name, base / f"{name}.txt"):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return os.environ.get(name.upper(), "")


def get_todoist_tasks(secrets_dir: str) -> dict:
    """Fetch today's due + overdue tasks from Todoist. Same contract as focus.py."""
    token = load_secret("todoist_api_key", secrets_dir)
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
    """Load yesterday's close notes from kai context. Same contract as focus.py."""
    context_file = vault_path / "60_Council" / "kai" / "context.md"
    if not context_file.exists():
        return ""
    content = context_file.read_text(encoding="utf-8")
    if "## Close Notes" in content:
        return content.split("## Close Notes")[-1].strip()
    return ""


def build_brief_text(tasks: dict, close_notes: str, secrets_dir: str) -> str:
    """Use Claude Haiku to build the Top 3 / Next 5 brief. Prompt identical to focus.py."""
    import anthropic

    api_key = load_secret("anthropic_api_key", secrets_dir)
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
        model=LLM_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def detect_sections(brief: str) -> list[str]:
    """Which required sections are present in the brief text (case-insensitive)."""
    lower = brief.lower()
    present = []
    if "top 3" in lower:
        present.append("top3")
    if "next 5" in lower:
        present.append("next5")
    if "carried over" in lower:
        present.append("carried_over")
    return present


def post_to_slack(brief: str, channel_id: str, secrets_dir: str) -> None:
    """AR-5.3: rerouted to Telegram (sole surface). Name/signature kept so call
    sites stay unchanged; channel_id ignored. Self-contained (no shared import on
    the hermes path): reads telegram secrets directly, best-effort."""
    from pathlib import Path as _P
    tok_f = _P("/run/secrets/telegram_bot_token")
    ids_f = _P("/run/secrets/telegram_allowed_chat_ids")
    token = tok_f.read_text().strip() if tok_f.exists() else load_secret("telegram_bot_token", secrets_dir)
    raw = ids_f.read_text() if ids_f.exists() else ""
    chat_ids = [c.strip() for c in raw.replace("\n", ",").split(",") if c.strip()]
    if not token or not chat_ids:
        return
    with httpx.Client() as client:
        for cid in chat_ids:
            try:
                client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": int(cid), "text": brief},
                    timeout=15.0,
                )
            except Exception:
                pass


def write_to_vault_context(brief: str, vault_path: Path) -> None:
    """Write today's brief to kai context (LIVE mode only). Same contract as focus.py."""
    context_file = vault_path / "60_Council" / "kai" / "context.md"
    existing = context_file.read_text(encoding="utf-8") if context_file.exists() else ""
    today = date.today().isoformat()
    section = f"\n\n## Daily Brief — {today}\n{brief}\n"
    if "## Daily Brief" in existing:
        existing = re.sub(r"## Daily Brief.*", "", existing, flags=re.DOTALL).strip()
    context_file.write_text(existing + section, encoding="utf-8")


def generate(secrets_dir: str, vault_path: Path) -> dict:
    """Deterministic input pull + brief generation. Returns the envelope dict (no side effects)."""
    tasks = get_todoist_tasks(secrets_dir)
    close_notes = load_kai_close_notes(vault_path)
    brief = build_brief_text(tasks, close_notes, secrets_dir)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date_label": date.today().strftime("%A, %B %d"),
        "tasks_today": len(tasks["today"]),
        "tasks_overdue": len(tasks["overdue"]),
        "sections_present": detect_sections(brief),
        "brief_markdown": brief,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="daily_brief — KAI daily focus brief")
    ap.add_argument("--mode", choices=["shadow", "live"], default="shadow")
    ap.add_argument("--secrets-dir", default=os.environ.get("KAI_SECRETS_DIR", "/run/secrets"))
    ap.add_argument("--vault", default=os.environ.get("KAI_VAULT_PATH", "/vault"))
    ap.add_argument("--sink-file", default=None, help="shadow: write brief text here")
    ap.add_argument("--channel", default=None, help="live: Slack channel id to post to")
    args = ap.parse_args()

    vault_path = Path(args.vault)
    env = generate(args.secrets_dir, vault_path)

    if args.mode == "live":
        if not args.channel:
            print("live mode requires --channel", file=sys.stderr)
            return 2
        post_to_slack(env["brief_markdown"], args.channel, args.secrets_dir)
        write_to_vault_context(env["brief_markdown"], vault_path)
        env["sink"] = f"slack:{args.channel}"
    else:
        # SHADOW: never touches Slack or vault. Write brief to a sink file if asked.
        if args.sink_file:
            Path(args.sink_file).write_text(env["brief_markdown"], encoding="utf-8")
            env["sink"] = f"shadow-file:{args.sink_file}"
        else:
            env["sink"] = "shadow-stdout"

    print(json.dumps(env, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

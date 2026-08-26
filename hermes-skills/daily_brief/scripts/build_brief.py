#!/usr/bin/env python3
"""
daily_brief — self-contained daily focus brief generator (Hermes skill core).

Strangler-fig port of kai-worker-api/focus.py. Same inputs (Todoist today+overdue
tasks + KAI close notes), same Top 3 / Next 5 / Carried-over format. Standalone
(no kai-worker-api import, configurable secrets dir + vault path) so it runs under
the Hermes hardened profile on cron.

LLM is LOCAL (self-hosted-default): qwen2.5:7b on the mini's Ollama, no cloud key.
Delivery is surface-configurable (currently Telegram; Buzz proactive-push pending).

Modes:
  --mode shadow  (default) : write the brief to a shadow sink file only. Never delivers.
  --mode live              : deliver to the configured comms surface + write vault context.

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
# LOCAL inference (self-hosted-default): qwen-mid on the mini's Ollama. No cloud LLM key.
LLM_MODEL = os.environ.get("KAI_LLM_MODEL", "qwen2.5:7b")
LLM_BASE_URL = os.environ.get("KAI_LLM_BASE_URL", "http://100.85.243.2:11434")
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
    """Build the Top 3 / Next 5 brief with LOCAL qwen-mid (Ollama). Prompt identical to focus.py."""
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

CRITICAL RULES:
- Use ONLY the tasks listed above. NEVER invent, assume, or add tasks that are not listed.
- If there are no tasks due today, write "None" under Top 3 and Next 5 — do not fabricate work.
- Top 3 = the 3 most important tasks due today (fewer if fewer exist). Next 5 = the remaining
  tasks due today (fewer if fewer exist). Carried over = the overdue tasks verbatim, or "None".

Build a brief with exactly this format (fill only from the real tasks above):

**Good morning. Here's your focus for today.**

**Top 3** — the 3 most important things to move today:
(numbered list of up to 3 real tasks, or "None")

**Next 5** — on deck after the Top 3:
(bulleted list of up to 5 real tasks, or "None")

**Carried over** — overdue items needing attention:
(bulleted list of the real overdue tasks, or "None")

Keep it tight. No preamble. Just the brief."""

    with httpx.Client() as client:
        r = client.post(
            f"{LLM_BASE_URL}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 512, "temperature": 0.3},
            },
            timeout=120.0,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]


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


def deliver_brief(brief: str, secrets_dir: str) -> str:
    """Deliver the brief to the configured comms surface. Returns a sink label.

    Current surface = Telegram (self-contained: reads telegram secrets directly,
    best-effort). Buzz is KAI's primary comms; a Buzz proactive-push path for the
    routine brief is the open cutover decision (Telegram is emergency-only in the
    comms model, so this is interim). No Slack — Slack is retired system-wide."""
    tok_f = Path("/run/secrets/telegram_bot_token")
    ids_f = Path("/run/secrets/telegram_allowed_chat_ids")
    token = tok_f.read_text().strip() if tok_f.exists() else load_secret("telegram_bot_token", secrets_dir)
    raw = ids_f.read_text() if ids_f.exists() else ""
    chat_ids = [c.strip() for c in raw.replace("\n", ",").split(",") if c.strip()]
    if not token or not chat_ids:
        return "undelivered:no-telegram-config"
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
    return "telegram"


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
        "llm_model": LLM_MODEL,
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
    args = ap.parse_args()

    vault_path = Path(args.vault)
    env = generate(args.secrets_dir, vault_path)

    if args.mode == "live":
        env["sink"] = deliver_brief(env["brief_markdown"], args.secrets_dir)
        write_to_vault_context(env["brief_markdown"], vault_path)
    else:
        # SHADOW: never delivers to any comms surface or vault. Write to a sink file if asked.
        if args.sink_file:
            Path(args.sink_file).write_text(env["brief_markdown"], encoding="utf-8")
            env["sink"] = f"shadow-file:{args.sink_file}"
        else:
            env["sink"] = "shadow-stdout"

    print(json.dumps(env, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

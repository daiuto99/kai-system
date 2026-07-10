import json
import logging
import re
from datetime import datetime as _dt
from council_config import VAULT_PATH, COUNCIL_PATH
from council_config import _track_usage
from providers import get_anthropic_client

logger = logging.getLogger(__name__)


def _write_session_summary(channel: str, title: str, topics: list, decisions: list,
                            actions: list, context_note: str) -> dict:
    sessions_dir = COUNCIL_PATH / "sessions" / channel
    sessions_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.utcnow()
    filename = ts.strftime("%Y-%m-%dT%H%M") + ".md"
    filepath = sessions_dir / filename

    lines = [
        f"# Session — {channel} — {ts.strftime('%Y-%m-%d')}",
        f"",
        f"**Title:** {title}  ",
        f"**Channel:** {channel}  ",
        f"**Date:** {ts.strftime('%Y-%m-%d %H:%M')} UTC  ",
        f"",
    ]
    if topics:
        lines += ["## Topics", ""] + [f"- {t}" for t in topics] + [""]
    if decisions:
        lines += ["## Decisions", ""] + [f"- {d}" for d in decisions] + [""]
    if actions:
        lines += ["## Action Items", ""] + [f"- [ ] {a}" for a in actions] + [""]
    if context_note:
        lines += ["## Context for Next Session", "", context_note, ""]

    filepath.write_text("\n".join(lines), encoding="utf-8")

    # Update context.md with rolling next-session state (KAI-28/30)
    if context_note:
        advisor_dir = COUNCIL_PATH / channel
        advisor_dir.mkdir(parents=True, exist_ok=True)
        context_file = advisor_dir / "context.md"
        context_file.write_text(
            "# KAI — Session Context\n\n"
            f"_Last updated: {ts.strftime('%Y-%m-%d %H:%M')} UTC — {title}_\n\n"
            f"{context_note}\n",
            encoding="utf-8"
        )

    return {"ok": True, "path": f"60_Council/sessions/{channel}/{filename}", "title": title}


def _write_decision(channel: str, decision: str, context: str, outcome: str) -> dict:
    decisions_dir = COUNCIL_PATH / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.utcnow()
    filename = ts.strftime("%Y-%m") + ".md"
    filepath = decisions_dir / filename

    header = f"# Decisions — {ts.strftime('%Y-%m')}\n\n" if not filepath.exists() else ""
    entry = (
        f"## {ts.strftime('%Y-%m-%d')} — {channel}\n\n"
        f"**Decision:** {decision}  \n"
        f"**Context:** {context}  \n"
    )
    if outcome:
        entry += f"**Outcome:** {outcome}  \n"
    entry += "\n---\n\n"

    with open(filepath, "a", encoding="utf-8") as f:
        if header:
            f.write(header)
        f.write(entry)
    return {"ok": True, "path": f"60_Council/decisions/{filename}", "decision": decision}


def _auto_summarize(channel: str, advisor: str):
    """Background: generate a session summary if history has grown significantly since last auto-summary."""
    try:
        from council_config import VAULT_PATH
        history_dir = VAULT_PATH / "60_Council" / "_history"
        history_file = history_dir / f"{channel}.jsonl"
        if not history_file.exists():
            return

        marker_file = history_dir / f".{channel}.summarized"
        last_count = int(marker_file.read_text().strip()) if marker_file.exists() else 0
        lines = history_file.read_text(encoding="utf-8").strip().splitlines()
        current_count = len(lines)

        if current_count - last_count < 20:
            return

        recent = []
        for line in lines[last_count:]:
            try:
                recent.append(json.loads(line))
            except Exception as e:
                logger.exception("auto_summarize parse line: %s", e)

        if len(recent) < 10:
            return

        transcript_parts = []
        for msg in recent[-30:]:
            role = "Leo" if msg["role"] == "user" else advisor.upper()
            transcript_parts.append(f"{role}: {msg['content'][:400]}")
        transcript = "\n".join(transcript_parts)

        client = get_anthropic_client()
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": (
                    f"Summarize this {advisor} conversation session concisely. "
                    f"Return ONLY a JSON object with keys: title (short string), "
                    f"topics (array of strings), decisions (array of strings), "
                    f"actions (array of strings), context (one paragraph for next session).\n\n"
                    f"Transcript:\n{transcript}"
                )
            }]
        )
        raw = response.content[0].text.strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return
        summary = json.loads(match.group())

        _write_session_summary(
            channel=channel,
            title=summary.get("title", f"Auto-summary {_dt.utcnow().strftime('%Y-%m-%d')}"),
            topics=summary.get("topics", []),
            decisions=summary.get("decisions", []),
            actions=summary.get("actions", []),
            context_note=summary.get("context", ""),
        )
        marker_file.write_text(str(current_count))
        _track_usage(advisor, response.usage.input_tokens, response.usage.output_tokens,
                     trigger_source="council:auto_summarize")
    except Exception as e:
        logger.exception("auto_summarize error: %s", e)


def _log_mission_deliverable(path: str, description: str):
    mission_file = VAULT_PATH / "00_System" / "active_mission.json"
    if not mission_file.exists():
        return
    try:
        mission = json.loads(mission_file.read_text())
        if mission.get("status") == "in_progress":
            mission.setdefault("deliverables", []).append({"path": path, "description": description})
            mission_file.write_text(json.dumps(mission, indent=2))
    except Exception as e:
        logger.exception("log_mission_deliverable: %s", e)

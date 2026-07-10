import json
import logging
from datetime import datetime as _dt
from council_config import VAULT_PATH, COUNCIL_PATH

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

"""Durable conversational agenda — hold a multi-step flow across turns.

Fixes KAI-3ba4cabd (Leo Buzz test 2026-08-03): KAI ran a red/yellow/green scan of
the life pillars, failed to ask the first pillar until reminded, then never
returned to it — it could not hold a simple structured agenda across turns because
the only cross-turn state was prose history the model had to re-infer each turn.

This gives the conversation a FIRST-CLASS agenda: an ordered checklist persisted
per advisor, advanced explicitly by tool call, and re-injected into the system
prompt EVERY turn as an unambiguous "you are on item N — ask it, then advance"
instruction. The model no longer has to remember where it was; the environment
tells it, every turn.

State lives at VAULT_PATH/00_System/agendas/{advisor}.json (one active agenda per
advisor — a KAI-run RYG scan is a KAI flow), mirroring the active_mission.json
pattern. Keyed by advisor because that is the identity available at tool-execution
and context-assembly time.

SAFETY: every read path (get/render_block) is fail-open — a missing, corrupt, or
unreadable agenda returns None / "" and NEVER raises, so an agenda glitch can never
break a live DM. Only the explicit write tools (start/advance/abandon) surface errors.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from council_config import VAULT_PATH
except Exception:  # pragma: no cover - allows standalone unit tests off-worker
    import os
    VAULT_PATH = Path(os.environ.get("VAULT_PATH", "/vault"))

_AGENDA_DIR = VAULT_PATH / "00_System" / "agendas"
_SLUG = re.compile(r"[^a-z0-9_-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(advisor: str) -> Path:
    slug = _SLUG.sub("-", (advisor or "kai").strip().lower()) or "kai"
    return _AGENDA_DIR / f"{slug}.json"


def _load(advisor: str) -> dict | None:
    """Read the advisor's agenda, or None. Fail-open: never raises. Returns None
    for any state that is not a JSON object — a valid non-object (e.g. a bare list
    or string) would otherwise pass json.loads and then blow up .get() downstream."""
    try:
        p = _path(advisor)
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write(advisor: str, agenda: dict) -> None:
    p = _path(advisor)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(agenda, indent=2))


# ---- write operations (explicit tool calls; surface errors) ----------------

def start(advisor: str, title: str, items: list[str]) -> dict:
    """Begin a structured multi-step flow. Overwrites any prior agenda for the
    advisor (a new flow supersedes an abandoned one)."""
    clean = [str(i).strip() for i in (items or []) if str(i).strip()]
    if not title or not clean:
        raise ValueError("start_agenda needs a title and at least one item")
    agenda = {
        "title": str(title).strip(),
        "status": "active",
        "current": 0,
        "items": [{"label": lbl, "status": "pending", "answer": None} for lbl in clean],
        "started": _now(),
        "updated": _now(),
    }
    _write(advisor, agenda)
    return agenda


def advance(advisor: str, answer: str | None = None) -> dict:
    """Mark the current item done (recording the answer), move to the next pending
    item. When the last item completes, the agenda's status becomes 'complete'."""
    agenda = _load(advisor)
    if not agenda or agenda.get("status") != "active":
        raise ValueError("no active agenda to advance")
    items = agenda["items"]
    cur = agenda.get("current", 0)
    if 0 <= cur < len(items):
        items[cur]["status"] = "done"
        if answer is not None:
            items[cur]["answer"] = str(answer)
    nxt = next((i for i, it in enumerate(items) if it["status"] == "pending"), None)
    if nxt is None:
        agenda["status"] = "complete"
        agenda["current"] = len(items)
    else:
        agenda["current"] = nxt
    agenda["updated"] = _now()
    _write(advisor, agenda)
    return agenda


def abandon(advisor: str) -> dict:
    """Explicitly drop the active agenda (user changed their mind / cancelled)."""
    agenda = _load(advisor) or {"items": [], "title": None}
    agenda["status"] = "abandoned"
    agenda["updated"] = _now()
    _write(advisor, agenda)
    return agenda


# ---- read operations (fail-open; used on the live turn path) ---------------

def get(advisor: str) -> dict | None:
    """The advisor's active agenda, or None if none is active. Never raises."""
    agenda = _load(advisor)
    if not agenda or agenda.get("status") != "active":
        return None
    return agenda


def render_block(advisor: str) -> str:
    """The <active_agenda> system-prompt block for the current turn, or '' if no
    active agenda. Fail-open: any error yields '' so it can never break a turn."""
    try:
        agenda = get(advisor)
        if not agenda:
            return ""
        items = agenda["items"]
        cur = agenda.get("current", 0)
        lines = []
        for i, it in enumerate(items):
            if it["status"] == "done":
                mark = "[x]"
            elif i == cur:
                mark = "[→ ASK NOW]"
            else:
                mark = "[ ]"
            lines.append(f"  {mark} {it['label']}")
        checklist = "\n".join(lines)
        current_label = items[cur]["label"] if 0 <= cur < len(items) else "(none)"
        done = sum(1 for it in items if it["status"] == "done")
        return (
            "\n\n<active_agenda>\n"
            f"You are running a structured multi-step flow: \"{agenda['title']}\" "
            f"({done}/{len(items)} done).\n"
            f"{checklist}\n\n"
            "RULES — follow exactly:\n"
            f"• Ask ONLY the current item now: \"{current_label}\". One item per turn — never batch.\n"
            "• When the user answers it, call advance_agenda (pass their answer) BEFORE replying, "
            "then ask the next item.\n"
            "• If the user digresses, answer briefly, then return to the current item — do not lose the thread.\n"
            "• Do not end the flow, summarize, or change topic until every item is done. "
            "When the last item completes the agenda closes itself; only then wrap up.\n"
            "• If the user says stop/cancel, call abandon_agenda.\n"
            "</active_agenda>"
        )
    except Exception:
        return ""

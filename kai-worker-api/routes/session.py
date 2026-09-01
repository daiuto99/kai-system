import subprocess
import sys
import json
import re
import os
from pathlib import Path
from datetime import datetime, date, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VAULT_PATH
from routes import plane as plane_routes

router = APIRouter()

MANIFEST_PATH = VAULT_PATH / "00_System" / "session_close_log.json"
WARMBOOT_MANIFEST_PATH = VAULT_PATH / "00_System" / "session_warmboot_log.json"
NEXT_ACTION_PATH = VAULT_PATH / "00_System" / "next_action.json"

# HARDEN-2: these are the exact wiki artifacts step_vault_wiki_sync writes and
# content-verifies through the vault API. The brief must never read a workspace
# mirror that the close does not write.
BRIEF_WIKI_DIR = VAULT_PATH / "70_Knowledge" / "System"
BRIEF_SOTU_PATH = BRIEF_WIKI_DIR / "StateOfTheUnion.md"
BRIEF_SPRINT_HISTORY_PATH = BRIEF_WIKI_DIR / "Sprint_History.md"


class NextActionRequest(BaseModel):
    """Only a Plane identity is accepted; next-action prose is never trusted."""

    issue_id: str
    project_id: str | None = None

    class Config:
        extra = "forbid"


def _next_action_text(issue: dict) -> str:
    return (
        f"KAI-{issue['sequence_id']} ({issue['id']}, {issue['state']}, "
        f"{issue.get('priority', 'none')}) — {issue['name']}"
    )


def _read_guarded_next_action() -> tuple[dict | None, str]:
    """Consume only a provenance-marked pointer that still matches live Plane."""
    if not NEXT_ACTION_PATH.exists():
        return None, "next_action.json missing"
    try:
        saved = json.loads(NEXT_ACTION_PATH.read_text())
        if saved.get("source") != "live_plane_readback" or not saved.get("issue_id"):
            return None, "next_action refused: missing live-board provenance"
        issue = plane_routes.get_plane_issue(
            saved["issue_id"], saved.get("project_id") or None,
        )
        if issue.get("state_group") not in ("backlog", "unstarted", "started"):
            return None, "next_action refused: live issue is no longer open"
        if saved.get("action") != _next_action_text(issue):
            return None, "next_action refused: saved content differs from live board"
        expires = saved.get("expires_at")
        if expires:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp_dt < datetime.now(exp_dt.tzinfo):
                return None, "next_action refused: pointer expired"
        return saved, "accepted: content matches live open Plane issue"
    except Exception as e:
        return None, f"next_action refused: live validation failed ({type(e).__name__})"


@router.post("/session/next-action")
def write_next_action(body: NextActionRequest):
    """Derive and atomically write next_action.json from a live open Plane issue."""
    uuid_pattern = (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    if not re.fullmatch(uuid_pattern, body.issue_id):
        raise HTTPException(status_code=422, detail="issue_id must be a full Plane UUID")
    if body.project_id is not None and not re.fullmatch(uuid_pattern, body.project_id):
        raise HTTPException(status_code=422, detail="project_id must be a full Plane UUID")

    issue = plane_routes.get_plane_issue(body.issue_id, body.project_id)
    if issue.get("id", "").lower() != body.issue_id.lower():
        raise HTTPException(status_code=502, detail="Plane readback identity mismatch")
    if issue.get("state_group") not in ("backlog", "unstarted", "started"):
        raise HTTPException(
            status_code=409,
            detail=(
                "next_action refused: live Plane readback is not open "
                f"({issue.get('state')!r})"
            ),
        )
    sequence_id = issue.get("sequence_id")
    if not isinstance(sequence_id, int):
        raise HTTPException(status_code=502, detail="Plane readback omitted sequence_id")

    now = datetime.now(timezone.utc).isoformat()
    project_id = body.project_id or plane_routes.KAI_PROJECT_ID
    payload = {
        "action": _next_action_text(issue),
        "sprint": issue["name"],
        "context": f"Derived from live Plane readback at {now}; state verified open.",
        "written_at": now,
        "source": "live_plane_readback",
        "issue_id": issue["id"],
        "project_id": project_id,
    }
    NEXT_ACTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = NEXT_ACTION_PATH.with_suffix(".json.tmp")
    encoded = json.dumps(payload, indent=2)
    tmp_path.write_text(encoded)
    tmp_path.replace(NEXT_ACTION_PATH)
    readback = json.loads(NEXT_ACTION_PATH.read_text())
    if readback != payload:
        raise HTTPException(status_code=500, detail="next_action content readback mismatch")
    return {"ok": True, "verified": True, "next_action": readback}


@router.get("/session/brief")
def session_brief():
    """S7-2: Compact session brief (~200 tokens) for catch-up.
    Replaces full reads of StateOfTheUnion + Sprint_History + vault session files.
    """
    today = date.today().isoformat()
    brief = {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "version": None,
        "last_session": None,
        "sprint": None,
        "sprint_status": None,
        "open_items": [],
        "recent_decisions": [],
        "last_close": None,
        "warmboot": None,
        "warmboot_required": True,
        "next_action": None,
        "next_action_guard": None,
    }

    # 1. StateOfTheUnion.md
    sotu = BRIEF_SOTU_PATH
    if sotu.exists():
        lines = sotu.read_text().splitlines()
        in_open = in_next = False
        for line in lines:
            if not brief["version"]:
                m = re.search(r"Last updated:.*?v([\d.]+)", line)
                if m:
                    brief["version"] = m.group(1)
            if not brief["last_session"]:
                m = re.search(r"Last session:\*\*\s*(.+)", line)
                if m:
                    brief["last_session"] = m.group(1).strip()[:100]
            if "**Open:**" in line:
                in_open, in_next = True, False
            elif "**What's next:**" in line:
                in_open = False
                # Fix 2026-05-19: close_engine.step_sotu writes "What's next" inline
                # rather than as a bullet list. The previous parser set in_next=True
                # on the marker line and expected bullets on subsequent lines, so
                # brief["sprint"] was never populated from SOTU.
                # See: ~/sonicink/docs/specs/warmboot-surgical-additions.md §3.1
                m = re.search(r"\*\*What's next:\*\*\s*(.+)", line)
                if m and not brief["sprint"]:
                    brief["sprint"] = m.group(1).strip()[:80]
                    brief["sprint_status"] = "planned"
                in_next = True  # also catch bullet-style entries on subsequent lines
            elif line.startswith("---") or line.startswith("##"):
                in_open = in_next = False
            elif in_open and line.startswith("- "):
                brief["open_items"].append(line[2:].strip()[:80])
            elif in_next and line.startswith("- ") and not brief["sprint"]:
                brief["sprint"] = line[2:].strip()[:80]
                brief["sprint_status"] = "planned"

    # 2. Sprint_History.md — top entry
    sh = BRIEF_SPRINT_HISTORY_PATH
    if sh.exists():
        for line in sh.read_text().splitlines()[:10]:
            # Fix 2026-05-19: close_engine.step_sprint_history writes
            # "## {title} — Complete — {date}" but the old regex required the
            # literal word "Session" and a different field order, so
            # brief["sprint_status"] was never populated from Sprint_History.
            # See: ~/sonicink/docs/specs/warmboot-surgical-additions.md §3.2

            # Primary: matches close_engine.step_sprint_history format (2026-05-19+)
            m = re.match(r"^## (.+?)\s+[—-]{1,2}\s+(Complete|In Progress)\s+[—-]{1,2}\s+(\d{4}-\d{2}-\d{2})", line)
            if m:
                title, status, dt = m.group(1), m.group(2), m.group(3)
                brief["last_session"] = brief["last_session"] or f"{dt} — {title}"
                brief["sprint"] = brief["sprint"] or title[:80]
                brief["sprint_status"] = status.lower().replace(" ", "_")
                break

            # Fallback: legacy "## Session ..." format (pre-close-engine)
            m = re.match(r"## Session (\S+).*?[—-]{1,2} (.+?) [—-]{1,2} (Complete|In Progress)", line)
            if m:
                brief["last_session"] = brief["last_session"] or f"{m.group(1)} — {m.group(2)}"
                brief["sprint"] = brief["sprint"] or m.group(2)
                brief["sprint_status"] = m.group(3).lower().replace(" ", "_")
                break

    # 3. Latest vault session file — last_session label only.
    session_dir = VAULT_PATH / "60_Council" / "sessions" / "kai"
    if session_dir.exists():
        files = sorted(session_dir.glob("*.md"), key=os.path.getmtime, reverse=True)
        if files:
            brief["last_session"] = brief["last_session"] or files[0].stem

    # 3b. Recent decisions — read the DECISIONS STORE, not the session file.
    # The close writes the session file's "## Decisions" section as a literal
    # "- (none recorded)" placeholder; the real decisions live in
    # 60_Council/decisions/YYYY-MM.md (one "## <date> — <title>" per decision,
    # newest appended at the bottom). Reading the placeholder is why the brief
    # reported 0 decisions despite decision commits. (session/brief degraded-fields fix)
    try:
        dec_dir = VAULT_PATH / "60_Council" / "decisions"
        for mf in (sorted(dec_dir.glob("*.md"), reverse=True) if dec_dir.exists() else []):
            titles = []
            for line in mf.read_text().splitlines():
                if line.startswith("## "):
                    title = re.sub(r"^\d{4}-\d{2}-\d{2}\s*[—-]{1,2}\s*", "", line[3:].strip())
                    if title and "(none recorded)" not in title.lower():
                        titles.append(title[:80])
            if titles:
                brief["recent_decisions"] = list(reversed(titles))[:4]
                break
    except Exception:
        pass

    # 4. Close manifest — last_close field
    # Fix 2026-05-19: brief now reads the close manifest written by close_engine.py
    # and exposes last-close state and failed steps so the welcome block can surface
    # unresolved close failures without re-reading vault files.
    # See: ~/sonicink/docs/specs/warmboot-surgical-additions.md §3.4
    if MANIFEST_PATH.exists():
        try:
            cm = json.loads(MANIFEST_PATH.read_text())
            cm_date = cm.get("date", "")
            cm_steps = cm.get("steps", [])
            failed = [
                {"name": s.get("name", ""), "label": s.get("label", ""), "detail": s.get("detail", "")}
                for s in cm_steps if s.get("status") == "fail"
            ]
            # Warmboot verification: did the previous close actually RUN the
            # plan.json <-> Plane reconciliation? "missing" means it did not
            # (an old close, or one that skipped the gate) — surfaced as open work.
            recon = next((s for s in cm_steps if s.get("name") == "plan_reconcile"), None)
            from datetime import date as _date
            try:
                close_dt = _date.fromisoformat(cm_date)
                stale = (date.today() - close_dt).days > 1
            except Exception:
                stale = True
            brief["last_close"] = {
                "date": cm_date,
                "overall": cm.get("overall", "unknown"),
                "failed_steps": failed,
                "stale": stale,
                "plan_reconcile": {
                    "ran": recon is not None,
                    "status": (recon or {}).get("status", "missing"),
                    "detail": (recon or {}).get("detail", "no plan_reconcile step in last close"),
                },
            }
        except Exception:
            pass

    # 5. Warmboot manifest — warmboot field + warmboot_required
    # Fix 2026-05-19: brief now reads the warmboot manifest written by
    # sync_plane_state.py warmboot, exposing freshness and container state so
    # the welcome block traces [4] PLANE WARM BOOT to a single source of truth.
    # See: ~/sonicink/docs/specs/warmboot-surgical-additions.md §3.4
    if WARMBOOT_MANIFEST_PATH.exists():
        try:
            wm = json.loads(WARMBOOT_MANIFEST_PATH.read_text())
            wm_date = wm.get("date", "")
            wb_stale = wm_date != today
            brief["warmboot"] = {
                "date": wm_date,
                "timestamp": wm.get("timestamp", ""),
                "stale": wb_stale,
                "containers_down": wm.get("containers_down", []),
                # plane_open_issues = the WORKING board (open AND NOT parked-post-gate).
                # plane_parked = open issues hidden by the recovery-plan board shrink.
                "plane_open_issues": wm.get("plane", {}).get("open_issues", None),
                "plane_parked": wm.get("plane", {}).get("parked", None),
                "reconciliation": wm.get("reconciliation", "unknown"),
                "overall": wm.get("overall", "unknown"),
            }
            brief["warmboot_required"] = wb_stale
            # next_sprint = the NEXT-sprint pointer (a distinct field), kept as-is.
            ns = wm.get("next_sprint")
            if ns:
                brief["next_sprint"] = ns
            # Authoritative CURRENT sprint: plan.json active_stage, written into the
            # warmboot manifest by sync_plane_state.py. Brief reads it here from the
            # close/warmboot-written VAULT manifest — NOT a /workspace mirror (HARDEN-2).
            # It overrides the SOTU "What's next" prose (block 1), which is the top
            # open ticket and goes stale the moment a sprint ships — the reason the
            # brief was reporting a ticket name as the sprint. (brief degraded-fields fix)
            act = wm.get("active_stage")
            if act and act.get("id"):
                brief["sprint"] = f"{act['id']} — {act.get('title', '')}".strip(" —")[:80]
                brief["sprint_status"] = "active"
        except Exception:
            pass
    # warmboot_required stays True if manifest is missing or parse failed


    # 6. next_action.json — highest-precedence next-session pointer, but only
    # when its content still matches a live open Plane readback.
    na, guard_detail = _read_guarded_next_action()
    brief["next_action_guard"] = {
        "accepted": na is not None,
        "detail": guard_detail,
    }
    if na:
        brief["next_action"] = {
            "action": na.get("action", ""),
            "sprint": na.get("sprint"),
            "context": na.get("context", ""),
            "written_at": na.get("written_at", ""),
            "source": na.get("source", ""),
            "issue_id": na.get("issue_id", ""),
        }

    return brief


@router.post("/session/tool-output-log")
def log_tool_output(tool: str, size: int, session_id: str = "unknown"):
    """S7-6: Log large tool outputs for pattern analysis."""
    log_path = VAULT_PATH / "00_System" / "tool_output_patterns.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(log_path.read_text()) if log_path.exists() else {"entries": []}
    existing["entries"].append({
        "tool": tool,
        "size": size,
        "session_id": session_id,
        "at": datetime.utcnow().isoformat() + "Z",
    })
    existing["entries"] = existing["entries"][-500:]
    log_path.write_text(json.dumps(existing, indent=2))
    return {"ok": True}


@router.get("/session/close-status")
def close_status():
    if not MANIFEST_PATH.exists():
        return {"status": "no_manifest", "message": "No close manifest found — session not yet closed via engine"}
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
        steps = manifest.get("steps", [])
        failed = [s for s in steps if s.get("status") == "fail"]
        ok_count = sum(1 for s in steps if s.get("status") == "ok")
        return {
            "status": "ok" if not failed else "partial",
            "date": manifest.get("date"),
            "session_title": manifest.get("session_title"),
            "mode": manifest.get("mode"),
            "overall": manifest.get("overall"),
            "steps_ok": ok_count,
            "steps_total": len(steps),
            "failed": [
                {"name": s["name"], "label": s["label"], "detail": s["detail"]}
                for s in failed
            ],
            "steps": [
                {"name": s["name"], "label": s["label"], "status": s["status"], "detail": s["detail"]}
                for s in steps
            ],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.post("/session/close")
def trigger_close(issue_id: str, background: bool = False):
    """Trigger manual_close.py. Returns stdout/stderr and exit code."""
    script = Path("/home/leo/sonicink/scripts/manual_close.py")
    python = sys.executable

    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        issue_id,
    ):
        return {"ok": False, "error": "issue_id must be a full Plane issue UUID"}

    command = [python, str(script), "--issue-id", issue_id]

    if not script.exists():
        return {"ok": False, "error": "manual_close.py not found", "path": str(script)}

    if background:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"ok": True, "mode": "background", "message": "manual_close.py launched in background"}

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout[-4000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "detail": "manual_close.py exceeded 300s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

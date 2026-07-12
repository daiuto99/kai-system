import subprocess
import sys
import json
import re
import os
from pathlib import Path
from datetime import datetime, date
from fastapi import APIRouter
from config import VAULT_PATH

router = APIRouter()

MANIFEST_PATH = VAULT_PATH / "00_System" / "session_close_log.json"
WARMBOOT_MANIFEST_PATH = VAULT_PATH / "00_System" / "session_warmboot_log.json"


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
    }

    # 1. StateOfTheUnion.md
    sotu = Path("/workspace/StateOfTheUnion.md")
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
    sh = Path("/workspace/Sprint_History.md")
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

    # 3. Latest vault session file — recent decisions
    session_dir = VAULT_PATH / "60_Council" / "sessions" / "kai"
    if session_dir.exists():
        files = sorted(session_dir.glob("*.md"), key=os.path.getmtime, reverse=True)
        if files:
            try:
                lines = files[0].read_text().splitlines()
                brief["last_session"] = brief["last_session"] or files[0].stem
                in_decisions = False
                for line in lines:
                    if line.startswith("## Decisions"):
                        in_decisions = True
                    elif line.startswith("## ") and in_decisions:
                        break
                    elif in_decisions and line.startswith("- "):
                        brief["recent_decisions"].append(line[2:].strip()[:80])
                        if len(brief["recent_decisions"]) >= 4:
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
            # Plane is authoritative for next-sprint. SOTU "What's next" prose
            # goes stale the moment a sprint ships (e.g. close didn't rewrite it).
            # If warmboot derived a next_sprint, override the SOTU-parsed values.
            ns = wm.get("next_sprint")
            if ns:
                brief["sprint"] = ns.get("name", brief["sprint"])[:80]
                # Map Plane state_group → brief contract: backlog/unstarted → planned
                brief["sprint_status"] = "planned"
                brief["next_sprint"] = ns
        except Exception:
            pass
    # warmboot_required stays True if manifest is missing or parse failed


    # 6. next_action.json — explicit "what to do next session" override.
    # Highest-precedence signal. Set by close engine or directly. CLAUDE.md
    # NEXT UP rule consumes this verbatim when present.
    next_action_path = VAULT_PATH / "00_System" / "next_action.json"
    if next_action_path.exists():
        try:
            na = json.loads(next_action_path.read_text())
            expires = na.get("expires_at")
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    if exp_dt < datetime.now(exp_dt.tzinfo):
                        na = None
                except Exception:
                    pass
            if na:
                brief["next_action"] = {
                    "action": na.get("action", ""),
                    "sprint": na.get("sprint"),
                    "context": na.get("context", ""),
                    "written_at": na.get("written_at", ""),
                }
        except Exception:
            pass

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

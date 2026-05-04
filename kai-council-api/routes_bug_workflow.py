import json
import logging
import threading
import time
import uuid
import urllib.request as ur
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from graphs.bug_graph import get_bug_graph

logger = logging.getLogger(__name__)
router = APIRouter()

PLANE_BASE    = "http://172.17.0.1:8090/api/v1/workspaces/sonicink"
KAI_PID       = "78c49227-82d4-477d-a920-66b08cb91c56"
PROCESSED_FILE = Path("/vault/00_System/bug_workflow_processed.json")
POLL_INTERVAL  = 300  # 5 minutes


def _plane_token() -> str:
    p = Path("/run/secrets/plane_api_token")
    if not p.exists():
        p = Path("/home/leo/kai-system/secrets/plane_api_token.txt")
    return p.read_text().strip().split("\n")[0] if p.exists() else ""


def _plane_req(path: str) -> dict:
    token = _plane_token()
    req = ur.Request(
        f"{PLANE_BASE}/{path}",
        headers={"X-API-Key": token},
    )
    with ur.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _load_processed() -> set:
    if PROCESSED_FILE.exists():
        return set(json.loads(PROCESSED_FILE.read_text()))
    return set()


def _mark_processed(issue_id: str):
    processed = _load_processed()
    processed.add(issue_id)
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_FILE.write_text(json.dumps(list(processed)))


def _run_bug_workflow(issue_id: str, issue_name: str, description: str,
                      project_name: str, priority: str):
    """Run the full bug investigation chain in a background thread."""
    try:
        graph = get_bug_graph()
        state = {
            "issue_id":          issue_id,
            "issue_name":        issue_name,
            "issue_description": description or "(no description provided)",
            "project_name":      project_name,
            "priority":          priority,
            "diagnosis":         "",
            "proposed_fix":      "",
            "confidence":        "",
            "iteration":         0,
            "prior_feedback":    "",
            "lse_review":        "",
            "lse_approved":      False,
            "architect_review":  "",
            "architect_approved": False,
            "kai_assessment":    "",
            "kai_approved":      False,
            "kai_return_notes":  "",
            "status":            "diagnosing",
            "slack_thread_ts":   "",
            "audit_log":         [],
        }
        config = {"configurable": {"thread_id": f"bug-{issue_id}"}}
        result = graph.invoke(state, config=config)
        logger.info(f"Bug workflow complete for {issue_id}: status={result.get('status')}")
        _mark_processed(issue_id)
    except Exception as e:
        logger.error(f"Bug workflow failed for {issue_id}: {e}", exc_info=True)


def _check_plane_for_bugs():
    """Poll Plane for new unprocessed bug-priority issues."""
    try:
        processed = _load_processed()
        # Get all projects
        projects_raw = _plane_req("projects/")
        projects = projects_raw.get("results", [])

        for proj in projects:
            pid = proj["id"]
            # Get states for priority detection
            issues_raw = _plane_req(f"projects/{pid}/issues/?per_page=50")
            issues = issues_raw.get("results", [])

            for issue in issues:
                iid = issue.get("id", "")
                if iid in processed:
                    continue
                # Check if it's a bug (priority=urgent/high or label contains bug)
                priority = issue.get("priority", "none")
                name = issue.get("name", "").lower()
                is_bug = (
                    priority in ("urgent", "high")
                    or "bug" in name
                    or "fix" in name
                    or "broken" in name
                    or "error" in name
                    or "crash" in name
                    or "fail" in name
                )
                if not is_bug:
                    continue

                logger.info(f"New bug detected: {issue.get('name')} ({iid})")
                _mark_processed(iid)  # mark before starting to avoid double-trigger
                t = threading.Thread(
                    target=_run_bug_workflow,
                    args=(
                        iid,
                        issue.get("name", "Unknown Bug"),
                        issue.get("description", "") or issue.get("description_stripped", ""),
                        proj.get("name", "Unknown Project"),
                        priority,
                    ),
                    daemon=True,
                )
                t.start()

    except Exception as e:
        logger.error(f"Plane poll error: {e}", exc_info=True)


def start_bug_poller():
    """Start background thread that polls Plane every 5 minutes."""
    def loop():
        logger.info("Bug workflow poller started (interval=%ds)", POLL_INTERVAL)
        while True:
            _check_plane_for_bugs()
            time.sleep(POLL_INTERVAL)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ── API endpoints ────────────────────────────────────────────────────────────

class BugTrigger(BaseModel):
    issue_id:    str
    issue_name:  str
    description: str = ""
    project_name: str = "KAI System"
    priority:    str = "high"


@router.post("/council/bug-investigate")
def trigger_bug_investigation(body: BugTrigger, bg: BackgroundTasks):
    """Manually trigger bug investigation workflow for a specific issue."""
    bg.add_task(
        _run_bug_workflow,
        body.issue_id,
        body.issue_name,
        body.description,
        body.project_name,
        body.priority,
    )
    return {"ok": True, "message": f"Bug investigation started for: {body.issue_name}"}


@router.post("/webhooks/plane")
def plane_webhook(payload: dict):
    """Receive Plane webhook on issue creation."""
    event = payload.get("event", "")
    if event not in ("issue_created", "issue.created"):
        return {"ok": True, "skipped": True}

    issue = payload.get("issue", payload.get("data", {}))
    priority = issue.get("priority", "none")
    name = (issue.get("name") or "").lower()

    is_bug = (
        priority in ("urgent", "high")
        or any(kw in name for kw in ("bug", "fix", "broken", "error", "crash", "fail"))
    )
    if not is_bug:
        return {"ok": True, "skipped": True}

    iid = issue.get("id", str(uuid.uuid4()))
    processed = _load_processed()
    if iid in processed:
        return {"ok": True, "skipped": "already_processed"}

    _mark_processed(iid)
    t = threading.Thread(
        target=_run_bug_workflow,
        args=(
            iid,
            issue.get("name", "Unknown Bug"),
            issue.get("description", ""),
            payload.get("project", {}).get("name", "Unknown"),
            priority,
        ),
        daemon=True,
    )
    t.start()
    return {"ok": True, "started": True, "issue": issue.get("name")}

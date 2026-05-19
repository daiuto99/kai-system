"""Auto-triage pipeline — failure → Plane BUG (DevOps) → structured Slack to Leo."""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

PLANE_API   = "http://host.docker.internal:8090/api/v1"
PLANE_WS    = "sonicink"
COUNCIL_API = "http://kai-council-api:8002"
KAI_PROJECT  = "78c49227-82d4-477d-a920-66b08cb91c56"
DEVOPS_ASSIGNEE = "aef72284-5b76-4e06-9b9b-d2cc4f9c591d"  # DevOps bot user (devops@sonicink.space)

_backlog_state_id: str | None = None


def _load(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    return p.read_text().strip().split("\n")[0] if p.exists() else os.environ.get(name.upper(), "")


def _get_backlog_state() -> str | None:
    global _backlog_state_id
    if _backlog_state_id:
        return _backlog_state_id
    try:
        r = httpx.get(
            f"{PLANE_API}/workspaces/{PLANE_WS}/projects/{KAI_PROJECT}/states/",
            headers={"X-API-Key": _load("plane_api_token")}, timeout=10
        )
        for s in r.json().get("results", []):
            if s["group"] == "backlog":
                _backlog_state_id = s["id"]
                return _backlog_state_id
    except Exception as e:
        log.error("triage: Plane states fetch failed: %s", e)
    return None


def create_plane_bug(function_name: str, error: str, proposed_fix: str = "Pending DevOps analysis", risk: str = "Unknown") -> int | None:
    """Create a Plane BUG assigned to DevOps. Returns sequence_id or None."""
    token = _load("plane_api_token")
    if not token:
        log.error("triage: plane_api_token not available")
        return None

    state_id = _get_backlog_state()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body = {
        "name": f"[BUG] Scheduled function failed: {function_name} — {now_str}",
        "description_html": (
            f"<p><strong>Function:</strong> <code>{function_name}</code></p>"
            f"<p><strong>Error:</strong> <code>{error[:500]}</code></p>"
            f"<p><strong>Detected:</strong> {now_str}</p>"
            f"<p><strong>Proposed fix:</strong> {proposed_fix}</p>"
            f"<p><strong>Risk:</strong> {risk}</p>"
            f"<p>Auto-created by KAI triage pipeline.</p>"
        ),
        "priority": "high",
        "assignees": [DEVOPS_ASSIGNEE],
    }
    if state_id:
        body["state_id"] = state_id

    try:
        r = httpx.post(
            f"{PLANE_API}/workspaces/{PLANE_WS}/projects/{KAI_PROJECT}/issues/",
            headers={"X-API-Key": token, "Content-Type": "application/json"},
            json=body, timeout=15,
        )
        r.raise_for_status()
        seq = r.json().get("sequence_id")
        log.info("triage: Plane BUG created — KAI-%s", seq)
        return seq
    except Exception as e:
        log.error("triage: Plane issue creation failed: %s", e)
        return None


def slack_triage_alert(function_name: str, error: str, plane_seq: int | None, proposed_fix: str = "Pending", risk: str = "Unknown"):
    token = _load("slack_bot_token")
    if not token:
        log.error("triage: slack_bot_token not available")
        return

    plane_ref = f"KAI-{plane_seq}" if plane_seq else "pending (Plane unreachable)"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = (
        f":rotating_light: *KAI System Alert — {now_str}*\n\n"
        f"*Issue:* `{function_name}` failed — `{error[:300]}`\n"
        f"*Logged in Plane:* {plane_ref} — assigned to DevOps\n"
        f"*Proposed fix:* {proposed_fix}\n"
        f"*Risk:* {risk}\n\n"
        f"_Approve fix in {plane_ref} to proceed._"
    )

    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "#kai-system", "text": msg,
                  "username": "KAI DevOps", "icon_emoji": ":rotating_light:"},
            timeout=10,
        )
        log.info("triage: Slack alert sent for %s (ticket %s)", function_name, plane_ref)
    except Exception as e:
        log.error("triage: Slack alert failed: %s", e)


def _get_devops_analysis(function_name: str, error: str) -> tuple[str, str]:
    """Consult DevOps specialist for root cause, proposed fix, and risk level."""
    try:
        prompt = (
            "A KAI scheduled function failed. Analyze and respond in EXACTLY this format:\n\n"
            "PROPOSED FIX: <one concrete sentence>\n"
            "RISK: <Low|Medium|High> — <one sentence justification>\n\n"
            f"Function: {function_name}\n"
            f"Error: {error[:600]}"
        )
        r = httpx.post(
            f"{COUNCIL_API}/message",
            json={"channel": "devops", "message": prompt, "user_id": "triage"},
            timeout=60,
        )
        r.raise_for_status()
        reply = r.json().get("reply", "")
        proposed_fix = "See Plane ticket for DevOps analysis"
        risk = "Unknown"
        for line in reply.splitlines():
            stripped = line.strip()
            if stripped.startswith("PROPOSED FIX:"):
                proposed_fix = stripped.replace("PROPOSED FIX:", "").strip()
            elif stripped.startswith("RISK:"):
                risk = stripped.replace("RISK:", "").strip()
        log.info("DevOps analysis: fix=%s risk=%s", proposed_fix[:60], risk[:30])
        return proposed_fix, risk
    except Exception as e:
        log.error("DevOps council analysis failed: %s", e)
        return "DevOps analysis failed — investigate manually", "Unknown"


def triage_failure(function_name: str, error: str):
    """Full pipeline: DevOps analysis → Plane BUG → structured Slack to Leo."""
    log.error("TRIAGE ACTIVATED — %s: %s", function_name, error)
    proposed_fix, risk = _get_devops_analysis(function_name, error)
    seq = create_plane_bug(function_name, error, proposed_fix=proposed_fix, risk=risk)
    slack_triage_alert(function_name, error, seq, proposed_fix=proposed_fix, risk=risk)

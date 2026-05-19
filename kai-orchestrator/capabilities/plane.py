"""plane.create_issue and plane.update_state capabilities."""
import os
from pathlib import Path

from models import CapabilityResult
from transports.base import safe_request
from . import capability

_PLANE_BASE = os.environ.get("PLANE_API_URL", "http://host.docker.internal:8090/api/v1")
_PLANE_WS   = "sonicink"

_STATE_ALIASES = {
    "backlog":      "backlog",
    "todo":         "todo",
    "in progress":  "in progress",
    "in_progress":  "in progress",
    "done":         "done",
    "complete":     "done",
    "completed":    "done",
    "cancelled":    "cancelled",
    "canceled":     "cancelled",
}


def _plane_token() -> str:
    p = Path("/run/wp_secrets/plane_api_token.txt")
    return p.read_text().splitlines()[0].strip() if p.exists() else os.environ.get("PLANE_API_TOKEN", "")


def _plane_headers() -> dict:
    return {"X-API-Key": _plane_token(), "Content-Type": "application/json"}


def _get_state_id(project_id: str, state_name: str) -> str | None:
    """Look up a state ID by name (case-insensitive, with aliases)."""
    r = safe_request(
        "GET", f"{_PLANE_BASE}/workspaces/{_PLANE_WS}/projects/{project_id}/states/",
        headers=_plane_headers(), timeout=10,
    )
    if not r.ok or not isinstance(r.data, dict):
        return None
    states = r.data.get("results", r.data) if isinstance(r.data, dict) else r.data
    if isinstance(states, dict):
        states = list(states.values())
    resolved = _STATE_ALIASES.get(state_name.lower(), state_name.lower())
    for s in (states or []):
        if isinstance(s, dict) and s.get("name", "").lower() == resolved:
            return s["id"]
    return None


@capability("plane.create_issue")
def create_issue(
    project_id: str,
    title: str,
    description: str = "",
    state_id: str = None,
    priority: str = "medium",
    **_,
) -> CapabilityResult:
    """Create a Plane issue in the given project. Returns the new issue id."""
    token = _plane_token()
    if not token:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "no_plane_token"},
        )

    payload: dict = {
        "name": title,
        "description_html": f"<p>{description}</p>" if description else "",
        "priority": priority,
    }
    if state_id:
        payload["state"] = state_id

    r = safe_request(
        "POST", f"{_PLANE_BASE}/workspaces/{_PLANE_WS}/projects/{project_id}/issues/",
        headers=_plane_headers(), json=payload, timeout=15,
    )

    if not r.ok or not isinstance(r.data, dict):
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "plane_http_error", "status_code": r.status_code, "detail": r.body_preview or r.error},
        )

    issue_id = r.data.get("id", "")
    if not issue_id:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "plane_no_id", "response": str(r.data)[:200]},
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={"issue_id": issue_id, "title": title, "project_id": project_id},
        verification={"verified": True, "method": "plane_id_present"},
    )


@capability("plane.update_state")
def update_state(
    issue_id: str,
    project_id: str,
    state_name: str,
    notes: str = "",
    **_,
) -> CapabilityResult:
    """Update a Plane issue's state by name. Appends notes to description if provided."""
    token = _plane_token()
    if not token:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "no_plane_token"},
        )

    state_id = _get_state_id(project_id, state_name)
    if not state_id:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "state_not_found", "state_name": state_name, "project_id": project_id},
        )

    payload: dict = {"state": state_id}
    r = safe_request(
        "PATCH", f"{_PLANE_BASE}/workspaces/{_PLANE_WS}/projects/{project_id}/issues/{issue_id}/",
        headers=_plane_headers(), json=payload, timeout=15,
    )

    if not r.ok:
        return CapabilityResult(
            ok=False, status="failed_recoverable",
            error={"type": "plane_patch_error", "status_code": r.status_code, "detail": r.body_preview or r.error},
        )

    if notes:
        # Append notes as a comment
        safe_request(
            "POST", f"{_PLANE_BASE}/workspaces/{_PLANE_WS}/projects/{project_id}/issues/{issue_id}/comments/",
            headers=_plane_headers(),
            json={"comment_html": f"<p>{notes}</p>"},
            timeout=10,
        )

    return CapabilityResult(
        ok=True, status="succeeded",
        data={"issue_id": issue_id, "state_name": state_name, "state_id": state_id},
        verification={"verified": True, "method": "plane_patch_ok"},
    )

"""Auto-triage pipeline — failure → classify → route via function_map → Plane BUG → Slack to routed team."""
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

PLANE_API   = "http://host.docker.internal:8090/api/v1"
PLANE_WS    = "sonicink"
COUNCIL_API = "http://kai-council-api:8002"
KAI_PROJECT  = "78c49227-82d4-477d-a920-66b08cb91c56"
# Fallback when function_map returns no Plane user for the routed team — keeps
# the system filing tickets even when a team role lacks a Plane user.
DEVOPS_ASSIGNEE = "aef72284-5b76-4e06-9b9b-d2cc4f9c591d"  # DevOps bot user (devops@sonicink.space)

_BUG_CATEGORIES = ("code_bug", "infra_bug", "content_bug", "unknown")

_backlog_state_id: str | None = None

# Human-readable labels for scheduled function names. Keys match the fn_name
# passed to triage_failure() — both direct crashes (e.g. "watchdog") and gap
# alerts from watchdog.py which append "_gap" (e.g. "watchdog_gap").
FUNCTION_LABELS: dict[str, str] = {
    "watchdog":                "System health watchdog crashed",
    "watchdog_gap":            "System health watchdog stopped running",
    "morning_brief":           "Morning brief errored",
    "morning_brief_gap":       "Morning brief stopped running",
    "evening_brief":           "Evening brief errored",
    "evening_brief_gap":       "Evening brief stopped running",
    "worker_health_check":     "Worker health check errored",
    "worker_health_check_gap": "Worker health check stopped running",
    "inbox_scan":              "Inbox scanner crashed",
    "inbox_scan_gap":          "Inbox scanner stopped running",
    "security":                "Security check crashed",
    "security_gap":            "Security checks stopped running",
    "invariants":              "Invariant check crashed",
    "invariants_gap":          "Invariant checks stopped running",
    "backup":                  "Backup job errored",
    "backup_gap":              "Backups stopped running",
    "tz_check":                "Timezone check crashed",
    "tz_check_gap":            "Timezone check stopped running",
    "checkin_morning":         "Morning check-in failed to send",
    "checkin_nightly":         "Nightly check-in failed to send",
}


def _label(function_name: str) -> str:
    """Return a human-readable label for a scheduled function name.

    Falls back to a best-effort prettifier when the name isn't in FUNCTION_LABELS:
    "foo_bar_gap" → "Foo bar stopped running", "foo_bar" → "Foo bar errored".
    """
    if function_name in FUNCTION_LABELS:
        return FUNCTION_LABELS[function_name]
    is_gap = function_name.endswith("_gap")
    stem = function_name[:-4] if is_gap else function_name
    pretty = re.sub(r"[_\-]+", " ", stem).strip().capitalize() or function_name
    return f"{pretty} {'stopped running' if is_gap else 'errored'}"


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


# ── Routing via function_map (Sprint 03 T3, option b) ────────────────────────

def _route_bug(category: str) -> tuple[str, str, str]:
    """Resolve a bug category to (team_role, assignee_uuid, slack_channel).

    Consults the function_map HTTP surface on kai-council-api so triage shares
    the same source of truth as orchestration. Falls back to (devops,
    DEVOPS_ASSIGNEE, #devops) on any failure — never silently drops a bug.
    """
    team_role = "devops"
    assignee  = DEVOPS_ASSIGNEE
    channel   = "#devops"

    try:
        r = httpx.get(f"{COUNCIL_API}/function_map/bug/owner",
                      params={"category": category}, timeout=5)
        if r.status_code == 200:
            team_role = r.json().get("owner", team_role) or team_role
    except Exception as e:
        log.warning("triage: function_map bug/owner unreachable (%s) — defaulting devops", e)

    try:
        r = httpx.get(f"{COUNCIL_API}/function_map/team_assignee/{team_role}", timeout=5)
        if r.status_code == 200:
            uuid = r.json().get("assignee_uuid")
            if uuid:
                assignee = uuid
            # else fall through — DEVOPS_ASSIGNEE remains
    except Exception as e:
        log.warning("triage: function_map team_assignee unreachable (%s) — devops fallback", e)

    try:
        r = httpx.get(f"{COUNCIL_API}/function_map/team_slack/{team_role}", timeout=5)
        if r.status_code == 200:
            channel = r.json().get("channel") or channel
    except Exception as e:
        log.warning("triage: function_map team_slack unreachable (%s) — defaulting #devops", e)

    return team_role, assignee, channel


def create_plane_bug(
    function_name: str,
    error: str,
    proposed_fix: str = "Pending DevOps analysis",
    risk: str = "Unknown",
    category: str = "infra_bug",
) -> int | None:
    """Create a Plane BUG routed via function_map. Returns sequence_id or None."""
    token = _load("plane_api_token")
    if not token:
        log.error("triage: plane_api_token not available")
        return None

    team_role, assignee, _ = _route_bug(category)

    state_id = _get_backlog_state()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    label = _label(function_name)
    error_block = f"<code>{error[:500]}</code>" if error else "<em>no error captured</em>"

    body = {
        "name": f"[BUG] {label} — {now_str}",
        "description_html": (
            f"<p><strong>What happened:</strong> {label}</p>"
            f"<p><strong>Function:</strong> <code>{function_name}</code></p>"
            f"<p><strong>Error:</strong> {error_block}</p>"
            f"<p><strong>Detected:</strong> {now_str}</p>"
            f"<p><strong>Classified as:</strong> {category} → routed to <strong>{team_role}</strong></p>"
            f"<p><strong>Proposed fix:</strong> {proposed_fix}</p>"
            f"<p><strong>Risk:</strong> {risk}</p>"
            f"<p>Auto-created by KAI triage pipeline — support-engineer intake (kai_internal mode).</p>"
        ),
        "priority": "high",
        "assignees": [assignee],
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
        log.info("triage: Plane BUG created — KAI-%s · %s → %s", seq, category, team_role)
        return seq
    except Exception as e:
        log.error("triage: Plane issue creation failed: %s", e)
        return None


def slack_triage_alert(
    function_name: str,
    error: str,
    plane_seq: int | None,
    proposed_fix: str = "Pending",
    risk: str = "Unknown",
    category: str = "infra_bug",
):
    """Post one-line Slack alert to the routed team's channel.

    Format: "Issue: <label> — Status: Action needed — <one-sentence action> (<plane_ref>)"
    """
    token = _load("slack_bot_token")
    if not token:
        log.error("triage: slack_bot_token not available")
        return

    team_role, _, channel = _route_bug(category)
    plane_ref = f"KAI-{plane_seq}" if plane_seq else "Plane unreachable"
    label = _label(function_name)

    if error:
        action = proposed_fix if proposed_fix and proposed_fix != "Pending" else "see Plane ticket"
    else:
        now_hm = datetime.now().strftime("%H:%M")
        action = f"no error captured — check kai-scheduler logs around {now_hm}"

    msg = f"Issue: *{label}* — Status: Action needed — {action} ({plane_ref})"

    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": msg,
                  "username": f"KAI {team_role.capitalize()}",
                  "icon_emoji": ":rotating_light:"},
            timeout=10,
        )
        log.info("triage: Slack alert sent for %s (ticket %s) → %s", function_name, plane_ref, channel)
    except Exception as e:
        log.error("triage: Slack alert failed: %s", e)


_LITELLM_URL = "http://kai-litellm:4000"


def _classify_and_analyze(function_name: str, error: str) -> tuple[str, str, str]:
    """Classify a scheduled-function failure via local qwen-mid through LiteLLM.

    Returns (proposed_fix, risk, category). Category is the support-engineer's
    triage call: code_bug / infra_bug / content_bug / unknown — used by
    _route_bug to pick the assignee + Slack channel.

    KAI-464 — Sonnet→Qwen retarget; structural classification belongs on Qwen
    per the KAI-459 local-first rule. Cost: $0.
    """
    try:
        master_key_p = Path("/run/secrets/litellm_master_key")
        master_key = master_key_p.read_text().strip() if master_key_p.exists() else ""

        system_prompt = (
            "You are KAI's support-engineer triage classifier. Given a failed "
            "scheduled function and its error message, respond in EXACTLY this "
            "format on three lines, nothing else:\n"
            "PROPOSED FIX: <one concrete sentence>\n"
            "RISK: <Low|Medium|High> — <one sentence justification>\n"
            "CATEGORY: <code_bug|infra_bug|content_bug|unknown> — pick the team "
            "that should own it. code_bug = application code in kai-council-api "
            "/ kai-orchestrator / kai-web. infra_bug = containers, deployments, "
            "scheduler, system health, databases, n8n, monitoring. content_bug "
            "= copy, design, brand assets. unknown when none fits."
        )
        user_prompt = f"Function: {function_name}\nError: {error[:600]}"
        payload = {
            "model": "qwen-mid",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 240,
            "temperature": 0,
        }
        r = httpx.post(
            f"{_LITELLM_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]

        proposed_fix = "See Plane ticket for support-engineer analysis"
        risk = "Unknown"
        category = "infra_bug"
        for line in reply.splitlines():
            stripped = line.strip()
            up = stripped.upper()
            if up.startswith("PROPOSED FIX:"):
                proposed_fix = stripped.split(":", 1)[1].strip()
            elif up.startswith("RISK:"):
                risk = stripped.split(":", 1)[1].strip()
            elif up.startswith("CATEGORY:"):
                raw = stripped.split(":", 1)[1].strip().split()[0].lower().rstrip(",")
                if raw in _BUG_CATEGORIES:
                    category = raw
        log.info("classify (qwen-mid): cat=%s fix=%s risk=%s",
                 category, proposed_fix[:60], risk[:30])
        return proposed_fix, risk, category
    except Exception as e:
        log.error("classify qwen-mid failed: %s", e)
        return "Support-engineer analysis failed — investigate manually", "Unknown", "infra_bug"


def _default_category_for(function_name: str) -> str:
    """Zero-evidence default — every scheduled function in this scheduler is
    infra-class. Used when error is empty so we skip the qwen call.
    """
    return "infra_bug"


def triage_failure(function_name: str, error: str):
    """Full pipeline: classify (support-engineer) → route → Plane BUG → Slack to routed team.

    Zero-evidence path: when `error` is empty (common for watchdog gap alerts
    that only know a function didn't run, not why), skip the qwen call — there's
    nothing to analyse and it just hallucinates. Default to infra_bug.
    """
    log.error("TRIAGE ACTIVATED — %s: %s", function_name, error)
    if error:
        proposed_fix, risk, category = _classify_and_analyze(function_name, error)
    else:
        proposed_fix = "no error captured — inspect kai-scheduler logs"
        risk = "Unknown"
        category = _default_category_for(function_name)
    seq = create_plane_bug(function_name, error, proposed_fix=proposed_fix,
                           risk=risk, category=category)
    slack_triage_alert(function_name, error, seq, proposed_fix=proposed_fix,
                       risk=risk, category=category)

"""Auto-triage pipeline — failure → Plane BUG (DevOps) → structured Slack to Leo."""
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
DEVOPS_ASSIGNEE = "aef72284-5b76-4e06-9b9b-d2cc4f9c591d"  # DevOps bot user (devops@sonicink.space)

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


def create_plane_bug(function_name: str, error: str, proposed_fix: str = "Pending DevOps analysis", risk: str = "Unknown") -> int | None:
    """Create a Plane BUG assigned to DevOps. Returns sequence_id or None."""
    token = _load("plane_api_token")
    if not token:
        log.error("triage: plane_api_token not available")
        return None

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
    """Post a single one-line Slack message in Leo's standard format:
       "Issue: <label> — Status: Action needed — <one-sentence action> (<plane_ref>)"
    """
    token = _load("slack_bot_token")
    if not token:
        log.error("triage: slack_bot_token not available")
        return

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
            json={"channel": "#devops", "text": msg,
                  "username": "KAI DevOps", "icon_emoji": ":rotating_light:"},
            timeout=10,
        )
        log.info("triage: Slack alert sent for %s (ticket %s)", function_name, plane_ref)
    except Exception as e:
        log.error("triage: Slack alert failed: %s", e)


_LITELLM_URL = "http://kai-litellm:4000"


def _get_devops_analysis(function_name: str, error: str) -> tuple[str, str]:
    """Classify a scheduled-function failure via local qwen-mid through LiteLLM.

    KAI-464 — first call-site retarget from Sonnet (via council /message) to
    local Qwen. This is a structural classification task (extract proposed-fix
    + risk from an error string) — exactly the monitoring class that the
    KAI-459 local-first rule says belongs on Qwen. Cost: $0.
    """
    try:
        master_key_p = Path("/run/secrets/litellm_master_key")
        master_key = master_key_p.read_text().strip() if master_key_p.exists() else ""

        system_prompt = (
            "You are KAI's DevOps triage classifier. Given a failed scheduled "
            "function and its error message, respond in EXACTLY this format on "
            "two lines, nothing else:\n"
            "PROPOSED FIX: <one concrete sentence>\n"
            "RISK: <Low|Medium|High> — <one sentence justification>"
        )
        user_prompt = f"Function: {function_name}\nError: {error[:600]}"
        payload = {
            "model": "qwen-mid",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 200,
            "temperature": 0,
        }
        r = httpx.post(
            f"{_LITELLM_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
        r.raise_for_status()
        reply = r.json()["choices"][0]["message"]["content"]

        proposed_fix = "See Plane ticket for DevOps analysis"
        risk = "Unknown"
        for line in reply.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("PROPOSED FIX:"):
                proposed_fix = stripped.split(":", 1)[1].strip()
            elif stripped.upper().startswith("RISK:"):
                risk = stripped.split(":", 1)[1].strip()
        log.info("DevOps analysis (qwen-mid): fix=%s risk=%s", proposed_fix[:60], risk[:30])
        return proposed_fix, risk
    except Exception as e:
        log.error("DevOps qwen-mid analysis failed: %s", e)
        return "DevOps analysis failed — investigate manually", "Unknown"


def triage_failure(function_name: str, error: str):
    """Full pipeline: DevOps analysis → Plane BUG → one-line Slack to Leo.

    Zero-evidence path: when `error` is empty (common for watchdog gap alerts
    that only know a function didn't run, not why), skip the DevOps council
    call — there's nothing for it to analyse and it just hallucinates.
    """
    log.error("TRIAGE ACTIVATED — %s: %s", function_name, error)
    if error:
        proposed_fix, risk = _get_devops_analysis(function_name, error)
    else:
        proposed_fix = "no error captured — inspect kai-scheduler logs"
        risk = "Unknown"
    seq = create_plane_bug(function_name, error, proposed_fix=proposed_fix, risk=risk)
    slack_triage_alert(function_name, error, seq, proposed_fix=proposed_fix, risk=risk)

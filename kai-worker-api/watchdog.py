"""KAI Watchdog — functional health checks for all integrations. KAI-63."""
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

WORKER_API  = "http://kai-worker-api:8001"
COUNCIL_API = "http://kai-council-api:8002"
OLLAMA_API  = "http://kai-ollama:11434"

# Alert dedup — store last alert time per check key
_last_alert: dict = {}
ALERT_INTERVAL_HOURS = 2  # re-alert if still failing after 2h


def _load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    return p.read_text().strip() if p.exists() else os.environ.get(name.upper(), "")


def _slack_alert(token: str, message: str):
    """Post to #kai-system."""
    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "#devops", "text": message,
                  "username": "KAI Watchdog", "icon_emoji": ":eyes:"},
            timeout=10,
        )
    except Exception as e:
        log.error("watchdog slack alert failed: %s", e)


def _should_alert(key: str) -> bool:
    last = _last_alert.get(key)
    now = datetime.now(timezone.utc).timestamp()
    if last is None or (now - last) > ALERT_INTERVAL_HOURS * 3600:
        _last_alert[key] = now
        return True
    return False


def _clear_alert(key: str):
    _last_alert.pop(key, None)


# ── Individual checks ─────────────────────────────────────────────────────────

def check_worker_api() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{WORKER_API}/health", timeout=5)
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def check_council_api() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{COUNCIL_API}/health", timeout=5)
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def check_ollama() -> tuple[bool, str]:
    try:
        r = httpx.get(f"{OLLAMA_API}/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return True, f"{len(models)} models loaded"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def check_slack() -> tuple[bool, str]:
    token = _load_secret("slack_bot_token")
    if not token:
        return False, "slack_bot_token missing"
    try:
        r = httpx.post("https://slack.com/api/auth.test",
                       headers={"Authorization": f"Bearer {token}"}, timeout=10)
        data = r.json()
        if data.get("ok"):
            return True, f"bot={data.get('bot_id','?')}"
        return False, data.get("error", "auth failed")
    except Exception as e:
        return False, str(e)


def check_telegram() -> tuple[bool, str]:
    token = _load_secret("telegram_bot_token")
    if not token:
        return False, "telegram_bot_token missing"
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
        if data.get("ok"):
            return True, f"bot=@{data['result'].get('username','?')}"
        return False, data.get("description", "getMe failed")
    except Exception as e:
        return False, str(e)


def check_oura() -> tuple[bool, str]:
    token = _load_secret("oura_token")
    if not token:
        return False, "oura_token missing"
    try:
        r = httpx.get(
            "https://api.ouraring.com/v2/usercollection/personal_info",
            headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def check_todoist() -> tuple[bool, str]:
    token = _load_secret("todoist_api_key")
    if not token:
        return False, "todoist_api_key missing"
    try:
        r = httpx.get("https://api.todoist.com/api/v1/tasks",
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
        if r.status_code == 200:
            return True, f"{len(r.json())} tasks"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def check_google_calendar() -> tuple[bool, str]:
    """Check calendar via worker API — if worker returns events or empty list it's healthy."""
    try:
        r = httpx.get(f"{WORKER_API}/calendar/ics?days=1", timeout=10)
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


# ── Tier 2: auto-remediation ──────────────────────────────────────────────────

def _try_restart_container(name: str) -> str:
    """Attempt to restart a Docker container. Returns result string."""
    try:
        result = subprocess.run(
            ["docker", "restart", name],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return f"restarted {name} ✅"
        return f"restart failed: {result.stderr[:100]}"
    except Exception as e:
        return f"restart error: {e}"


RESTARTABLE = {
    "worker_api":   "kai-worker-api",
    "council_api":  "kai-council-api",
    "ollama":       "kai-ollama",
}


# ── Main runner ───────────────────────────────────────────────────────────────

CHECKS = [
    ("worker_api",       "Worker API",       check_worker_api),
    ("council_api",      "Council API",      check_council_api),
    ("ollama",           "Ollama",           check_ollama),
    ("slack",            "Slack",            check_slack),
    ("telegram",         "Telegram",         check_telegram),
    ("oura",             "Oura",             check_oura),
    ("todoist",          "Todoist",          check_todoist),
    ("google_calendar",  "Google Calendar",  check_google_calendar),
]


def run_watchdog_checks():
    """Run all functional health checks. Post failures to #kai-system."""
    token = _load_secret("slack_bot_token")
    failures = []
    remediations = []

    for key, label, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"check error: {e}"

        if ok:
            _clear_alert(key)
            log.debug("watchdog ✅ %s: %s", label, detail)
        else:
            log.warning("watchdog ❌ %s: %s", label, detail)
            # Tier 2: auto-remediate restartable services
            if key in RESTARTABLE:
                remedy = _try_restart_container(RESTARTABLE[key])
                remediations.append(f"  • {label}: {remedy}")
                log.info("watchdog remediation: %s → %s", label, remedy)
            if _should_alert(key):
                failures.append(f"  • *{label}*: `{detail}`")

    if failures and token:
        lines = [f":warning: *KAI Watchdog — {datetime.now().strftime('%H:%M')}*"]
        lines.append("*Failures detected:*")
        lines.extend(failures)
        if remediations:
            lines.append("*Auto-remediation attempted:*")
            lines.extend(remediations)
        _slack_alert(token, "\n".join(lines))
        log.info("watchdog alert posted: %d failures", len(failures))
    else:
        log.info("watchdog ✅ all checks passed")

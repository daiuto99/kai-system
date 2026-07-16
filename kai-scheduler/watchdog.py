"""KAI Watchdog — functional health checks for all integrations. KAI-63."""
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import httpx
import time

from worker_auth import worker_auth

log = logging.getLogger(__name__)

WORKER_API  = "http://kai-worker-api:8001"
COUNCIL_API = "http://kai-council-api:8002"
OLLAMA_API  = "http://kai-ollama:11434"

# Alert dedup — persisted to disk so scheduler restart does not wipe state.
# Behavior contract (KAI-471, 2026-06-11) — JARVIS §3 behavioral floor:
#   • Every check requires CONSECUTIVE_FAIL_THRESHOLD consecutive failed ticks
#     before any escalation. Single transient ticks log only.
#   • SYSTEM-WIDE: no check ever escalates a 'transient' failure (timeout/5xx/
#     connection error) to Leo, regardless of subsystem. Classification gate
#     fires before tier handlers — applies to OAuth, infra, API health, all.
#   • Only 'auth' (401/403/invalid_token) or 'other' classifications can reach
#     the tier handlers. OAuth specifically escalates "re-authenticate".
#   • OAuth snooze is preserved across recovery. Once Leo is told, we stay
#     silent for the full 24h window regardless of flap recovery.
#   • Non-OAuth alert keys are cleared on recovery so genuine outages re-alert
#     immediately when they cross the threshold next time.
# Tests live at kai-scheduler/test_watchdog_dedup.py.
# Schema v2: nested format with `alerts` (snooze map) and `fail_counters`
# (consecutive-failure counter map). Migration from legacy flat format is
# transparent — old {key: epoch} is read as `alerts`. See KAI-471.
ALERT_STATE_FILE = Path("/vault/_alert_state.json")
ALERT_INTERVAL_HOURS = 2  # re-alert if still failing after 2h
CONSECUTIVE_FAIL_THRESHOLD = 3  # ticks of failure before any check escalates


def _load_alert_state() -> dict:
    """Return {"alerts": {...}, "fail_counters": {...}}. Migrate legacy flat format."""
    try:
        if ALERT_STATE_FILE.exists():
            import json as _json
            data = _json.loads(ALERT_STATE_FILE.read_text())
            if isinstance(data, dict) and "alerts" not in data and "fail_counters" not in data:
                # Legacy flat format: {key: epoch}
                return {"alerts": data, "fail_counters": {}}
            return {
                "alerts": data.get("alerts", {}) if isinstance(data, dict) else {},
                "fail_counters": data.get("fail_counters", {}) if isinstance(data, dict) else {},
            }
    except Exception as e:
        log.warning("alert state read failed: %s", e)
    return {"alerts": {}, "fail_counters": {}}


def _save_alert_state() -> None:
    try:
        import json as _json
        ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 2,
            "alerts": _last_alert,
            "fail_counters": _fail_counter,
        }
        ALERT_STATE_FILE.write_text(_json.dumps(payload, indent=2))
    except Exception as e:
        log.warning("alert state write failed: %s", e)


_last_alert: dict = {}
_fail_counter: dict = {}
_initial_state = _load_alert_state()
_last_alert.update(_initial_state.get("alerts", {}))
_fail_counter.update(_initial_state.get("fail_counters", {}))

# Plane CE crash-loop detection — track restart count between watchdog runs
_plane_restart_baseline: int | None = None
PLANE_COMPOSE_DIR = "/home/leo/plane"
PLANE_CRASH_LOOP_THRESHOLD = 5  # restarts since last check = crash loop

# Disk + journal maintenance — runs once per day, not every 30-min cycle
DISK_WARN_PCT = 85
DISK_CRIT_PCT = 90
JOURNAL_VACUUM_DAYS = 7
_last_maintenance: float = 0.0
MAINTENANCE_INTERVAL_HOURS = 24


def _load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    return p.read_text().strip() if p.exists() else os.environ.get(name.upper(), "")


def _slack_alert(token: str, message: str):
    """Post to #devops as the DevOps persona."""
    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "#devops", "text": message,
                  "username": "DevOps",
                  "icon_url": "https://kai.sonicink.space/avatar-devops.png"},
            timeout=10,
        )
    except Exception as e:
        log.error("watchdog slack alert failed: %s", e)


def _should_alert(key: str) -> bool:
    last = _last_alert.get(key)
    now = datetime.now(timezone.utc).timestamp()
    if last is None or (now - last) > ALERT_INTERVAL_HOURS * 3600:
        _last_alert[key] = now
        _save_alert_state()
        return True
    return False


def _clear_alert(key: str):
    if key in _last_alert:
        _last_alert.pop(key, None)
        _save_alert_state()


def _record_failure(key: str) -> int:
    """Increment the persistent consecutive-failure counter and return new value."""
    _fail_counter[key] = _fail_counter.get(key, 0) + 1
    _save_alert_state()
    return _fail_counter[key]


def _record_success(key: str):
    """Reset the consecutive-failure counter. Does NOT touch the snooze map."""
    if key in _fail_counter:
        _fail_counter.pop(key, None)
        _save_alert_state()


_TRANSIENT_MARKERS = ("502", "503", "504", "500", "HTTP 5", "timed out", "timeout",
                     "Connection error", "ConnectError", "ReadTimeout", "RemoteDisconnected")

_AUTH_FAIL_MARKERS = ("401", "403", "Unauthorized", "Forbidden",
                     "invalid_token", "invalid_grant", "credential")


def _classify_failure(detail: str) -> str:
    """Classify a failure detail string. Returns 'auth' | 'transient' | 'other'.

    OAuth credential-expiry alerts must only fire on 'auth' — a 'transient'
    classification means network or upstream-service flap, never a credential
    problem. The §6 retrofit treated all retried-out failures the same; this
    split is the fix.
    """
    s = str(detail)
    if any(m in s for m in _AUTH_FAIL_MARKERS):
        return "auth"
    if any(m in s for m in _TRANSIENT_MARKERS):
        return "transient"
    return "other"


def _check_with_retry(fn, retries: int = 2, delay: float = 10.0):
    """Run a check; retry on 5xx OR timeout before treating as failure.

    A 10-second HTTP timeout is not an OAuth credential expiry — it is a
    transient. We retry up to `retries` times with `delay` backoff. Only a
    persistent failure becomes an alert.
    """
    ok, msg = fn()
    attempts = 0
    while not ok and attempts < retries and any(x in str(msg) for x in _TRANSIENT_MARKERS):
        time.sleep(delay)
        ok, msg = fn()
        attempts += 1
    return ok, msg


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
        # L18: httpx error text embeds the bot-token URL; this string flows
        # into transport status + Slack alerts — redact before returning.
        return False, str(e).replace(token, "[REDACTED]")


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
        r = httpx.get(f"{WORKER_API}/calendar/ics?days=1", timeout=10, auth=worker_auth())
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def check_disk() -> tuple[bool, str]:
    """Check root filesystem usage. Returns warning/critical based on thresholds."""
    try:
        result = subprocess.run(
            ["df", "--output=pcent", "/"], capture_output=True, text=True, timeout=5
        )
        pct = int(result.stdout.strip().splitlines()[-1].replace("%", "").strip())
        if pct >= DISK_CRIT_PCT:
            return False, f"CRITICAL: {pct}% used — run cleanup"
        if pct >= DISK_WARN_PCT:
            return False, f"WARNING: {pct}% used"
        return True, f"{pct}% used"
    except Exception as e:
        return False, str(e)


def run_maintenance():
    """Daily: vacuum journald logs + clean apt cache. Requires sudoers entry."""
    global _last_maintenance
    now = datetime.now(timezone.utc).timestamp()
    if (now - _last_maintenance) < MAINTENANCE_INTERVAL_HOURS * 3600:
        return
    _last_maintenance = now
    try:
        subprocess.run(
            ["sudo", "journalctl", f"--vacuum-time={JOURNAL_VACUUM_DAYS}d"],
            capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["sudo", "apt-get", "clean"],
            capture_output=True, text=True, timeout=30,
        )
        log.info("watchdog maintenance: journal vacuum + apt cache clean done")
    except Exception as e:
        log.warning("watchdog maintenance failed: %s", e)


def check_plane_ce() -> tuple[bool, str]:
    """Check plane-api container status and detect crash loops via docker inspect."""
    global _plane_restart_baseline
    try:
        result = subprocess.run(
            ["docker", "inspect", "plane-api", "--format", "{{.State.Status}} {{.RestartCount}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return False, "plane-api container not found"
        parts = result.stdout.strip().split()
        status, restart_count = parts[0], int(parts[1])

        crash_looping = False
        delta = 0
        if _plane_restart_baseline is not None:
            delta = restart_count - _plane_restart_baseline
            if delta >= PLANE_CRASH_LOOP_THRESHOLD:
                crash_looping = True
        _plane_restart_baseline = restart_count

        if status == "running" and not crash_looping:
            return True, f"ok (restarts={restart_count})"
        if crash_looping:
            return False, f"crash loop (+{delta} restarts since last check, total={restart_count})"
        return False, f"status={status} restarts={restart_count}"
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

# S5R-28: plane-api recreation escalated to Leo — containers/create blocked by
# docker-socket-proxy (L16). Run manually on host:
#   cd ~/plane && docker compose rm -sf api && docker compose up -d api
def _try_recreate_plane_api() -> str:
    return "plane-api needs recreation — containers/create blocked by socket proxy (L16). Run on host: cd ~/plane && docker compose rm -sf api && docker compose up -d api"


# ── Main runner ───────────────────────────────────────────────────────────────


# ── KAI-218: Backup integrity ─────────────────────────────────────────────────

def check_backup_integrity() -> tuple[bool, str]:
    """Verify backup ran within 26h, log shows success, output file is non-zero."""
    import re
    from datetime import datetime
    backup_log = Path("/backups/backup.log")
    backup_dir = Path("/backups/plane")

    if not backup_log.exists():
        return False, "backup.log not found at /backups/backup.log"

    lines = backup_log.read_text().splitlines()
    last_complete_ts = None
    for line in reversed(lines):
        if "Backup complete" in line:
            m = re.search(r"\[(\d{8}_\d{6})\]", line)
            if m:
                last_complete_ts = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
            break

    if not last_complete_ts:
        return False, "no successful backup found in log"

    hours_since = (datetime.now() - last_complete_ts).total_seconds() / 3600
    if hours_since > 26:
        return False, f"last backup {hours_since:.1f}h ago — expected <26h"

    if backup_dir.exists():
        files = sorted(backup_dir.glob("plane_*.sql.gz"))
        if not files:
            return False, "backup dir exists but no .sql.gz files found"
        latest = files[-1]
        if latest.stat().st_size == 0:
            return False, f"latest backup file is 0 bytes: {latest.name}"
        return True, f"ok — last: {last_complete_ts.strftime('%Y-%m-%d %H:%M')} — {len(files)} file(s)"

    return True, f"ok — last: {last_complete_ts.strftime('%Y-%m-%d %H:%M')}"


# ── KAI-217: Cert expiry ──────────────────────────────────────────────────────

def check_cert_expiry() -> tuple[bool, str]:
    """Alert if kai.sonicink.space SSL cert expires within 30 days."""
    import ssl, socket
    from datetime import datetime
    WARN_DAYS = 30
    HOST = "kai.sonicink.space"
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=HOST) as s:
            s.settimeout(10)
            s.connect((HOST, 443))
            cert = s.getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days_left = (expires - datetime.utcnow()).days
        if days_left < WARN_DAYS:
            return False, f"cert expires in {days_left} days ({expires.strftime('%Y-%m-%d')}) — renew now"
        return True, f"ok — {days_left} days remaining ({expires.strftime('%Y-%m-%d')})"
    except Exception as e:
        return False, f"cert check error: {e}"


# ── KAI-217: Component currency ───────────────────────────────────────────────

def check_component_currency() -> tuple[bool, str]:
    """Flag stale KAI Docker images (>30d) and pending apt security updates."""
    import json as _json
    from datetime import datetime, timezone
    issues = []

    # Docker image ages
    try:
        result = subprocess.run(
            ["docker", "images", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().splitlines():
            try:
                img = _json.loads(line)
                repo = img.get("Repository", "")
                if "kai-system" not in repo:
                    continue
                created_raw = img.get("CreatedAt", "")
                dt = datetime.strptime(created_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - dt).days
                if age_days > 30:
                    name = repo.split("/")[-1] + ":" + img.get("Tag", "?")
                    issues.append(f"{name} ({age_days}d old)")
            except Exception:
                continue
    except Exception as e:
        issues.append(f"image check error: {e}")

    # apt security updates from cached status file
    apt_file = Path("/vault/00_System/apt_status.txt")
    if apt_file.exists():
        content = apt_file.read_text()
        sec_pkgs = [l for l in content.splitlines() if l.strip() and "security" in l.lower()]
        if sec_pkgs:
            issues.append(f"{len(sec_pkgs)} security apt update(s) pending: {sec_pkgs[0].split('/')[0]}")

    if issues:
        return False, "; ".join(issues)
    return True, "all components current"


CHECKS = [
    ("worker_api",       "Worker API",       check_worker_api),
    ("council_api",      "Council API",      check_council_api),
    ("ollama",           "Ollama",           check_ollama),
    ("slack",            "Slack",            check_slack),
    ("telegram",         "Telegram",         lambda: _check_with_retry(check_telegram)),
    ("oura",             "Oura",             lambda: _check_with_retry(check_oura)),
    ("todoist",          "Todoist",          lambda: _check_with_retry(check_todoist)),
    ("google_calendar",  "Google Calendar",  lambda: _check_with_retry(check_google_calendar)),
    ("plane_ce",         "Plane CE",         check_plane_ce),
    ("disk",             "Disk",             check_disk),
    ("backup",           "Backup",           check_backup_integrity),
    ("cert_expiry",      "SSL Cert",         check_cert_expiry),
    ("component_currency","Components",      check_component_currency),
]

RECREATABLE = {
    "plane_ce": _try_recreate_plane_api,
}


def _remediate_backup() -> str:
    """Write trigger file to vault — host cron (ensure_backup_cron.sh) picks it up within 5m."""
    try:
        trigger = Path("/vault/00_System/backup_trigger")
        trigger.write_text(datetime.now(timezone.utc).isoformat())
        return "backup triggered — host cron will run within 5min ✅"
    except Exception as e:
        return f"remediation error: {e}"


def _post_oauth_escalation(token: str, service: str, detail: str):
    """JARVIS §6 CRITICAL format. Posted at most once per 24h per service.

    Only fired after persistent failure across retries — transient timeouts
    are absorbed by _check_with_retry. Caller is responsible for the 24h snooze.
    """
    msg = (
        f"CRITICAL — {service} authentication failed after retries. "
        f"You need to take action — re-authenticate at "
        f"http://100.78.94.80:5678 → Credentials → {service}. "
        f"Detail: `{detail}`."
    )
    _slack_alert(token, msg)


def _try_fix_disk() -> str:
    """Auto-remediate disk pressure: prune unused Docker images + build cache + vacuum logs.

    Safe operations only — no running containers or data are touched.
    Runs without sudo. Reports actual freed bytes per step.
    Escalates to Leo if still above threshold after cleanup.
    """
    freed_parts = []

    # Prune unused images first — largest potential win when images accumulate
    try:
        result = subprocess.run(
            ["docker", "image", "prune", "-a", "--force"],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.strip().lower().startswith("total reclaimed space:"):
                    freed_parts.append(f"images: {line.split(':', 1)[1].strip()}")
                    break
    except Exception as e:
        log.warning("disk remediation: docker image prune failed: %s", e)

    try:
        result = subprocess.run(
            ["docker", "builder", "prune", "--force"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            # Extract "Total: X.XXX GB" from output
            for line in result.stdout.splitlines():
                if line.strip().lower().startswith("total:"):
                    freed_parts.append(f"build cache: {line.split(':', 1)[1].strip()}")
                    break
    except Exception as e:
        log.warning("disk remediation: docker builder prune failed: %s", e)

    try:
        subprocess.run(
            ["journalctl", "--vacuum-size=200M"],
            capture_output=True, text=True, timeout=30,
        )
        freed_parts.append("journal: vacuumed to 200MB")
    except Exception as e:
        log.warning("disk remediation: journal vacuum failed: %s", e)

    try:
        # Remove compressed logs older than 7 days
        import glob as _glob
        removed = 0
        for f in _glob.glob("/var/log/**/*.gz", recursive=True):
            try:
                p = Path(f)
                if (datetime.now(timezone.utc).timestamp() - p.stat().st_mtime) > 7 * 86400:
                    p.unlink()
                    removed += 1
            except Exception:
                pass
        if removed:
            freed_parts.append(f"old logs: removed {removed} compressed files")
    except Exception as e:
        log.warning("disk remediation: log cleanup failed: %s", e)

    # Re-check disk after cleanup
    ok, detail = check_disk()
    summary = ", ".join(freed_parts) if freed_parts else "nothing freed"
    if ok:
        return f"disk freed ({summary}) — now {detail} ✅"
    else:
        return f"disk partially freed ({summary}) — still {detail} ⚠️ manual review needed"


def _try_fix_components() -> str:
    """Auto-rebuild KAI containers whose images are older than 30 days.

    Identifies stale kai-system images, rebuilds only those services,
    and restarts them. Safe: only touches containers that are already stale.
    Reports what was rebuilt. Escalates to Leo if rebuild fails.
    """
    import json as _json
    from datetime import datetime, timezone as _tz

    KAI_COMPOSE_DIR = Path("/home/leo/kai-system")

    # Map image name → compose service name
    IMAGE_TO_SERVICE = {
        "kai-system-kai-slack-bot":  "kai-slack-bot",
        "kai-system-kai-mcp-api":    "kai-mcp-api",
        "kai-system-kai-worker-api": "kai-worker-api",
        "kai-system-kai-council-api":"kai-council-api",
        "kai-system-kai-scheduler":  "kai-scheduler",
        "kai-system-kai-web":        "kai-web",
    }

    try:
        result = subprocess.run(
            ["docker", "images", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10,
        )
        stale_services = []
        for line in result.stdout.strip().splitlines():
            try:
                img = _json.loads(line)
                repo = img.get("Repository", "")
                if "kai-system" not in repo:
                    continue
                created_raw = img.get("CreatedAt", "")
                dt = datetime.strptime(created_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
                age_days = (datetime.now(_tz.utc) - dt).days
                if age_days > 30:
                    svc = IMAGE_TO_SERVICE.get(repo)
                    if svc and svc not in stale_services:
                        stale_services.append(svc)
            except Exception:
                continue
    except Exception as e:
        return f"component check failed: {e}"

    if not stale_services:
        return "no stale components found ✅"

    # S5R-28: containers/create blocked by docker-socket-proxy (L16).
    # Auto-rebuild not possible from inside the container — escalate to Leo.
    names = ", ".join(stale_services)
    svcs = " ".join(stale_services)
    return (
        f"stale images detected ({names}) — auto-rebuild unavailable (socket proxy blocks create). "
        f"Run on host: cd ~/kai-system && docker compose build {svcs} && docker compose up -d {svcs}"
    )


REMEDIATABLE = {
    "backup":             _remediate_backup,
    "disk":               _try_fix_disk,
    "component_currency": _try_fix_components,
}

# Services whose auth failures get a structured escalation (not repeated spam)
OAUTH_SERVICES = {
    "google_calendar": "Google Calendar",
}

# Checks intentionally skipped — not failures, not alerts, not counted in failure state.
# Remove the key when the deferral is resolved.
DEFERRED_CHECKS: dict[str, str] = {
    "google_calendar": "Deliberately deferred — n8n OAuth intentionally dead until S7-9 (n8n retirement + calendar transport rebuild)",
}


# Why KAI can't auto-fix each failure and what it affects
CANT_FIX_REASON = {
    "worker_api":        ("system",    "All KAI tools and API endpoints unavailable"),
    "council_api":       ("system",    "Chat with all advisors unavailable"),
    "ollama":            ("hardlimit", "Local model inference unavailable — Claude fallback active"),
    "slack":             ("hardlimit", "All Slack notifications and approvals unavailable"),
    "telegram":          ("hardlimit", "Telegram briefs and commands unavailable"),
    "oura":              ("hardlimit", "Health/readiness data unavailable in briefs"),
    "todoist":           ("hardlimit", "Task list unavailable — Todoist API down or token expired"),
    "google_calendar":   ("hardlimit", "OAuth token expired — cannot auto-renew"),
    "plane_ce":          ("system",    "Project management unavailable — sprint tracking broken"),
    "disk":              ("autofixed", "Storage high — attempting: unused image prune + build cache prune + log vacuum"),
    "backup":            ("system",    "Vault and Plane data not protected"),
    "cert_expiry":       ("hardlimit", "SSL cert expired — all HTTPS services unreachable from web"),
    "component_currency":("autofixed", "Stale KAI containers — DevOps auto-rebuilds and restarts them"),
}

ACTION_NEEDED = {
    "worker_api":        "Check: ssh kai 'docker logs kai-worker-api'",
    "council_api":       "Check: ssh kai 'docker logs kai-council-api'",
    "ollama":            "Check: ssh kai 'docker logs kai-ollama' — may need restart or GPU issue",
    "slack":             "Verify Slack bot token in ~/kai-system/secrets/slack_bot_token.txt",
    "telegram":          "Verify Telegram bot token in ~/kai-system/secrets/telegram_bot_token.txt",
    "oura":              "Check Oura token in ~/kai-system/secrets/oura_token.txt",
    "todoist":           "Check Todoist token in ~/kai-system/secrets/todoist_api_token.txt",
    "google_calendar":   "n8n → Credentials → Google Calendar → re-authenticate (http://100.78.94.80:5678)",
    "plane_ce":          "Check: ssh kai 'docker logs kai-plane-web' — container recreate attempted",
    "disk":              "Free space: ssh kai 'df -h' then clear logs/old Docker images",
    "backup":            "Auto-trigger queued. If still failing: ssh kai 'bash ~/kai-system/backup.sh'",
    "cert_expiry":       "Renew cert: ssh kai 'docker exec kai-nginx certbot renew --force-renewal'",
    "component_currency":"Host-side rebuild required (socket proxy blocks container-side): ssh kai 'cd ~/sonicink/kai-system && docker compose build <service> && docker compose up -d <service>'",
}


# ── KAI-465 / KAI-467: warning-log triage + archive ───────────────────────────

_LITELLM_URL = "http://kai-litellm:4000"
_LOG_ARCHIVE_DIR = Path("/vault/_logs")
_ARCHIVE_INTERVAL_HOURS = 1
_ARCHIVE_RETENTION_DAYS = 90
_last_log_archive: float = 0.0
_last_log_prune: float = 0.0
_WARNING_DEDUPE_PATH = Path("/vault/_warning_dedupe.json")
_WARNING_CONTAINERS = ("kai-council-api", "kai-worker-api", "kai-orchestrator")
_WARNING_SCAN_MINUTES = 25  # slightly larger than the 15-min watchdog cadence to overlap
_WARNING_DEDUPE_HOURS = 24
_WARNING_MAX_CLASSIFY_PER_RUN = 20
# Python logging level prefix + logger name + message
_WARNING_LINE_RE = __import__("re").compile(
    r"^(?P<level>WARNING|ERROR|CRITICAL)(?::|\s+[\-]\s+)(?P<logger>[\w\.\-]+)(?::|\s+[\-]\s+)(?P<msg>.+)$"
)
# Strip variable parts for fingerprint stability
_FP_STRIP_RE = __import__("re").compile(
    r"(\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|"  # UUIDs
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[\.\,]?\d*|"                    # timestamps
    r"'[^']{0,200}'|\"[^\"]{0,200}\"|"                                       # quoted strings
    r"\b[a-z]*\d+[a-z0-9]*\b|"                                               # mixed alnum / IDs (covers abc123, 2c100116, etc.)
    r"\b[a-f0-9]{6,}\b)",                                                    # hex chunks 6+
    __import__("re").IGNORECASE,
)


def _docker_logs_since(container: str, minutes: int) -> list[str]:
    """Return stdout+stderr lines from docker logs --since for container. Empty on error."""
    try:
        r = subprocess.run(
            ["docker", "logs", "--since", f"{minutes}m", container],
            capture_output=True, text=True, timeout=20,
        )
        return (r.stdout + r.stderr).splitlines()
    except Exception as e:
        log.error("warning-triage: docker logs failed for %s: %s", container, e)
        return []


def _fingerprint(container: str, logger_name: str, message: str) -> str:
    """Stable hash across variable substitutions in the same warning template."""
    import hashlib
    stripped = _FP_STRIP_RE.sub("§", message)[:400]
    seed = f"{container}|{logger_name}|{stripped}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


def _load_warning_dedupe() -> dict:
    import json
    if not _WARNING_DEDUPE_PATH.exists():
        return {}
    try:
        return json.loads(_WARNING_DEDUPE_PATH.read_text())
    except Exception as e:
        log.warning("warning-triage: dedupe cache unreadable, starting fresh: %s", e)
        return {}


def _save_warning_dedupe(d: dict) -> None:
    import json
    try:
        _WARNING_DEDUPE_PATH.write_text(json.dumps(d, indent=2))
    except Exception as e:
        log.error("warning-triage: dedupe cache write failed: %s", e)


def _classify_warning_local(container: str, logger_name: str, message: str) -> dict:
    """Classify a warning via local qwen-mid through LiteLLM. Returns {category, fix}.

    Category: real-bug | config-missing | transient-recoverable | noise.
    Local-first per KAI-459. $0 cost.
    """
    import json
    try:
        master_key_p = Path("/run/secrets/litellm_master_key")
        master_key = master_key_p.read_text().strip() if master_key_p.exists() else ""
    except Exception:
        master_key = ""

    sys_prompt = (
        "You classify a single Python WARNING/ERROR log line into ONE category. "
        "Categories: real-bug (code defect that will cause incorrect behavior), "
        "config-missing (a required config/env/secret is absent), "
        "transient-recoverable (network timeout, rate limit, retry will fix), "
        "noise (deprecation, expected info, shutdown trace, library chatter). "
        "Reply with raw JSON only: {\"category\":\"...\", \"fix\":\"one short sentence\"}."
    )
    user_prompt = f"Container: {container}\nLogger: {logger_name}\nMessage: {message[:400]}"
    payload = {
        "model": "qwen-mid",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 120,
        "temperature": 0,
    }
    try:
        r = httpx.post(
            f"{_LITELLM_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
        if r.status_code != 200:
            return {"category": "noise", "fix": f"classifier HTTP {r.status_code}"}
        content = r.json()["choices"][0]["message"]["content"].strip()
        # Strip any ```json fencing
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        parsed = json.loads(content)
        cat = parsed.get("category", "noise").strip().lower()
        if cat not in ("real-bug", "config-missing", "transient-recoverable", "noise"):
            cat = "noise"
        return {"category": cat, "fix": str(parsed.get("fix", ""))[:200]}
    except Exception as e:
        log.warning("warning-triage: classifier error: %s", e)
        return {"category": "noise", "fix": f"classifier failed: {e}"}


def check_container_warnings():
    """Scrape WARNING+ from container logs, dedupe, classify via qwen-mid, file Plane bugs.

    KAI-465 / KAI-459 epic. Closes the silent-warning channel that hid KAI-457
    for 5 days. Routes through local Qwen for $0 classification cost.
    """
    now_ts = datetime.now(timezone.utc).timestamp()
    dedupe = _load_warning_dedupe()
    window_sec = _WARNING_DEDUPE_HOURS * 3600

    new_warnings: list[dict] = []
    for container in _WARNING_CONTAINERS:
        for line in _docker_logs_since(container, _WARNING_SCAN_MINUTES):
            m = _WARNING_LINE_RE.match(line.strip())
            if not m:
                continue
            level = m.group("level")
            logger_name = m.group("logger")
            msg = m.group("msg").strip()
            # Skip uvicorn access lines that occasionally surface as WARNING
            if logger_name.startswith("uvicorn") and "HTTP/" in msg:
                continue
            fp = _fingerprint(container, logger_name, msg)
            cached = dedupe.get(fp)
            if cached and (now_ts - cached.get("last_seen", 0)) < window_sec:
                continue
            new_warnings.append({
                "container": container,
                "level": level,
                "logger": logger_name,
                "message": msg,
                "fp": fp,
            })

    if not new_warnings:
        log.info("warning-triage: no new warnings in last %d min", _WARNING_SCAN_MINUTES)
        return

    log.info("warning-triage: %d new warning(s) found, classifying", len(new_warnings))

    filed_count = 0
    for w in new_warnings[:_WARNING_MAX_CLASSIFY_PER_RUN]:
        cls = _classify_warning_local(w["container"], w["logger"], w["message"])
        dedupe[w["fp"]] = {
            "last_seen": now_ts,
            "category": cls["category"],
            "container": w["container"],
            "logger": w["logger"],
            "preview": w["message"][:120],
        }
        if cls["category"] in ("real-bug", "config-missing"):
            try:
                from triage import create_plane_bug
                seq = create_plane_bug(
                    function_name=f"{w['container']}/{w['logger']}",
                    error=f"[{w['level']}] {w['message'][:300]}",
                    proposed_fix=cls["fix"][:200] or "Pending DevOps analysis",
                    risk=cls["category"],
                    category="infra_bug",
                )
                if seq:
                    filed_count += 1
                    log.warning(
                        "warning-triage: filed KAI-%s for %s/%s (%s)",
                        seq, w["container"], w["logger"], cls["category"],
                    )
            except Exception as e:
                log.error("warning-triage: Plane file failed: %s", e)

    _save_warning_dedupe(dedupe)
    log.info(
        "warning-triage: cycle done — %d new, %d classified, %d Plane bugs filed",
        len(new_warnings),
        min(len(new_warnings), _WARNING_MAX_CLASSIFY_PER_RUN),
        filed_count,
    )


def archive_container_warnings():
    """KAI-467 — hourly archive of WARNING+ lines from each monitored container.

    Day-bucketed append-only files in /vault/_logs/<container>/<YYYY-MM-DD>.log.
    Survives container removal because the vault lives on a host-mounted volume.
    Forensic source-of-truth when KAI-465's dedupe references warnings whose
    in-container logs have been rotated or wiped.
    """
    global _last_log_archive
    now_ts = datetime.now(timezone.utc).timestamp()
    if (now_ts - _last_log_archive) < _ARCHIVE_INTERVAL_HOURS * 3600:
        return
    _last_log_archive = now_ts

    today_iso = datetime.now(timezone.utc).date().isoformat()
    archived_total = 0
    for container in _WARNING_CONTAINERS:
        # 65m overlap window so the hourly schedule never has a gap
        lines = _docker_logs_since(container, 65)
        warning_lines: list[str] = []
        for line in lines:
            m = _WARNING_LINE_RE.match(line.strip())
            if not m:
                continue
            if m.group("logger").startswith("uvicorn") and "HTTP/" in m.group("msg"):
                continue
            warning_lines.append(line.rstrip())
        if not warning_lines:
            continue
        try:
            container_dir = _LOG_ARCHIVE_DIR / container
            container_dir.mkdir(parents=True, exist_ok=True)
            archive_path = container_dir / f"{today_iso}.log"
            with archive_path.open("a", encoding="utf-8") as f:
                for line in warning_lines:
                    f.write(line + "\n")
            archived_total += len(warning_lines)
        except Exception as e:
            log.error("log-archive: write failed for %s: %s", container, e)

    if archived_total:
        log.info("log-archive: appended %d warning(s) across %d container(s)",
                 archived_total, len(_WARNING_CONTAINERS))


def prune_archived_logs():
    """KAI-467 — daily prune of log archive files older than retention window."""
    global _last_log_prune
    now_ts = datetime.now(timezone.utc).timestamp()
    if (now_ts - _last_log_prune) < 24 * 3600:
        return
    _last_log_prune = now_ts

    if not _LOG_ARCHIVE_DIR.exists():
        return
    cutoff_ts = now_ts - (_ARCHIVE_RETENTION_DAYS * 24 * 3600)
    pruned = 0
    try:
        for container_dir in _LOG_ARCHIVE_DIR.iterdir():
            if not container_dir.is_dir():
                continue
            for f in container_dir.glob("*.log"):
                if f.stat().st_mtime < cutoff_ts:
                    f.unlink()
                    pruned += 1
    except Exception as e:
        log.error("log-prune: walk failed: %s", e)
        return
    if pruned:
        log.info("log-prune: removed %d archive file(s) older than %dd",
                 pruned, _ARCHIVE_RETENTION_DAYS)


def run_watchdog_checks():
    """Run all functional health checks. Post failures to #kai-system."""
    run_maintenance()
    token = _load_secret("slack_bot_token")
    failures = []
    remediations = []
    fixed = []

    for key, label, fn in CHECKS:
        if key in DEFERRED_CHECKS:
            log.info("watchdog: skipping deferred check %s — %s", key, DEFERRED_CHECKS[key])
            continue
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"check error: {e}"

        if ok:
            # Recovery: reset consecutive-fail counter.
            # OAuth snooze is preserved — once we've told Leo to re-auth, we stay
            # silent for the full 24h window regardless of flap recovery.
            _record_success(key)
            if key not in OAUTH_SERVICES:
                _clear_alert(key)
            log.debug("watchdog ✅ %s: %s", label, detail)
            continue

        # Failure: increment consecutive-fail counter and gate on threshold.
        fail_count = _record_failure(key)
        log.warning("watchdog ❌ %s (consecutive=%d): %s", label, fail_count, detail)

        if fail_count < CONSECUTIVE_FAIL_THRESHOLD:
            log.info(
                "watchdog %s: %d/%d consecutive fails — deferring escalation",
                label, fail_count, CONSECUTIVE_FAIL_THRESHOLD,
            )
            continue

        # ── System-wide rule (JARVIS §3 behavioral floor) ─────────────────
        # No check, anywhere, ever escalates a 'transient' failure to Leo.
        # transient = timeout / 5xx / connection error / read-timeout. These
        # are upstream flap, not actionable. They are logged and tracked, but
        # never produce a Slack page. Only 'auth' or 'other' classifications
        # may proceed to the tier handlers below. This applies uniformly to
        # every check in CHECKS — OAuth, infra, API health, the lot.
        classification = _classify_failure(detail)
        if classification == "transient":
            log.warning(
                "watchdog %s: %d consecutive transient failures — upstream flap, not escalating (JARVIS §3 floor).",
                label, fail_count,
            )
            continue

        # Threshold met AND failure is auth or other — proceed to tiered handling.

        # Tier 1: auto-remediate known fixable failures
        if key in REMEDIATABLE:
            try:
                remedy = REMEDIATABLE[key]()
            except Exception as _rem_err:
                # Handler crash must not take down the whole watchdog — log and fall through to alert.
                log.error("watchdog remediation error for %s: %s", label, _rem_err)
                remediations.append(f"  • {label}: remediation error: {_rem_err}")
            else:
                log.info("watchdog remediation: %s → %s", label, remedy)
                if "✅" in remedy:
                    fixed.append(f"  • *{label}*: {remedy}")
                    _clear_alert(key)
                    _record_success(key)  # remediation succeeded = recovery
                    continue  # fixed — skip alert
                else:
                    remediations.append(f"  • {label}: {remedy}")

        # Tier 2: OAuth credential failures — "re-authenticate" page.
        # Classification already excluded transient, so this is genuinely an
        # auth failure (401/403/invalid_token) or unclassified 'other'.
        elif key in OAUTH_SERVICES:
            if _should_alert(key):
                _post_oauth_escalation(token, OAUTH_SERVICES[key], detail)
                _last_alert[key] = datetime.now(timezone.utc).timestamp() + (22 * 3600)  # snooze 24h
                _save_alert_state()
                log.info("watchdog oauth escalation posted for %s — snoozed 24h", key)
            continue

        # Tier 3: container restarts
        elif key in RECREATABLE:
            remedy = RECREATABLE[key]()
            remediations.append(f"  • {label}: {remedy}")
            log.info("watchdog remediation: %s → %s", label, remedy)
        elif key in RESTARTABLE:
            remedy = _try_restart_container(RESTARTABLE[key])
            remediations.append(f"  • {label}: {remedy}")
            log.info("watchdog remediation: %s → %s", label, remedy)

        if _should_alert(key):
            failures.append(f"  • *{label}*: `{detail}`")

    # Gap checks — scheduled function execution health
    try:
        run_gap_checks()
    except Exception as e:
        log.error("gap checks failed: %s", e)

    # Container warning triage (KAI-465) — scrape log warnings, classify, file Plane bugs
    try:
        check_container_warnings()
    except Exception as e:
        log.error("warning-triage failed: %s", e)

    # Container log archive (KAI-467) — hourly snapshot of WARNING+ into vault for forensics
    try:
        archive_container_warnings()
    except Exception as e:
        log.error("log-archive failed: %s", e)
    try:
        prune_archived_logs()
    except Exception as e:
        log.error("log-prune failed: %s", e)


    # Auto-fixed: log only, no Slack noise
    for f in fixed:
        log.info("watchdog auto-fixed: %s", f)

    if failures and token:
        import re as _re
        for failure_line in failures:
            m = _re.search(r"\*(.+?)\*: `(.+?)`", failure_line)
            if not m:
                _slack_alert(token, failure_line)
                continue
            label, detail = m.group(1), m.group(2)
            key = next((k for k, l, _ in CHECKS if l == label), "")
            reason_type, affects = CANT_FIX_REASON.get(key, ("unknown", "unknown impact"))
            action = ACTION_NEEDED.get(key, "Check: ssh kai 'docker ps'")
            reason_str = "Hard limit" if reason_type == "hardlimit" else "System issue"
            # JARVIS §6 CRITICAL format.
            msg = (
                f"CRITICAL — {label} {detail}. "
                f"You need to take action — {action}. "
                f"({reason_str} · affects: {affects})"
            )
            _slack_alert(token, msg)
        log.info("watchdog alert posted: %d failures", len(failures))
    elif not fixed:
        log.info("watchdog all checks passed")

# ── Scheduled function gap checks ─────────────────────────────────────────────

def check_scheduled_functions():
    """Check execution registry for functions that missed their expected run window."""
    try:
        from execution_registry import check_gaps
        return check_gaps()
    except Exception as e:
        log.error("gap check failed: %s", e)
        return []


def run_gap_checks():
    """Called by watchdog — log scheduled-function gaps. Does NOT file Plane bugs.

    A gap (function hasn't run in N hours) is a symptom, not a bug. It can mean
    a real failure, a container restart, an orphaned registry entry, a paused
    function, or watchdog clock skew. Real failures arrive via the exception
    path in scheduler.py:_safe() → triage_failure(); those create the Plane bug.

    Here we only log gaps. If a gap persists and matters, the real-failure path
    will catch it the next time the function actually tries to run and throws.
    """
    gaps = check_scheduled_functions()
    if not gaps:
        return

    for gap in gaps:
        fn   = gap["function"]
        hrs  = gap.get("hours_since")
        last = gap["last_run"] or "never"
        err  = gap.get("last_error") or ""
        log.warning(
            "gap detected: %s — %sh since last run (last: %s, last_error: %s)",
            fn, hrs, last, err or "none",
        )

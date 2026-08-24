"""KAI Watchdog — functional health checks for all integrations. KAI-63."""
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import httpx
import time

from worker_auth import worker_auth
from redact import redact

log = logging.getLogger(__name__)

WORKER_API  = "http://kai-worker-api:8001"
COUNCIL_API = "http://kai-council-api:8002"
OLLAMA_API  = "http://kai-ollama:11434"
ORCHESTRATOR_API = "http://kai-orchestrator:8003"

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

# KAI-44 — checks whose CRITICAL failure is BOTH self-unfixable by auto-remediation
# AND system-threatening (it will take the whole worker down). That is a
# personal-consequence event (Rule B), not DevOps noise, so it escalates to Leo's
# Telegram instead of the dashboard. Root cause of the 2026-08-24 crisis: every
# "Disk CRITICAL — you need to take action" page routed dashboard_only for ~11h
# while _try_fix_disk (docker/log prune only) could not touch the real consumer.
LEO_CRITICAL_CHECKS = {"disk"}
_last_maintenance: float = 0.0
MAINTENANCE_INTERVAL_HOURS = 24


def _load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    return p.read_text().strip() if p.exists() else os.environ.get(name.upper(), "")


def _slack_alert(token: str, message: str, *, cause: str | None = None,
                 audience: str = "dashboard"):
    """AR-5.1: rerouted to Telegram (sole surface). Legacy `token` arg ignored.
    KAI-1100: a watchdog page asserts something needs attention, so it routes
    through the Findings Contract as status="alert". A page with no verified
    cause ships as an explicit not-yet-diagnosed (a symptom, honestly undiagnosed)
    rather than a bare alarm the operator would fill in from memory.

    KAI-44: `audience` defaults to 'dashboard' (Rule B — operational noise is
    DevOps's to log, not Leo's to be pushed). A self-unfixable, system-threatening
    CRITICAL passes audience='personal' so the notify gateway routes it to Leo's
    Telegram instead of a dashboard he never sees (the 2026-08-24 disk-crisis hole)."""
    from tg_alert import tg_alert
    tg_alert(f"[DevOps] {message}", status="alert", cause=cause, audience=audience)


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
    # AR-5.3: Slack retired (AR-5) — nothing to health-check.
    return True, "retired (AR-5)"


def check_telegram() -> tuple[bool, str]:
    token = _load_secret("telegram_bot_token")
    if not token:
        return False, "telegram_bot_token missing"
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = r.json()
        # L18: the response body may reflect the token-bearing request URL,
        # literal or URL-encoded — in the success result as much as in error
        # descriptions or httpx exception text. Redact everything that flows
        # into transport status + Slack alerts.
        if data.get("ok"):
            return True, redact(f"bot=@{data['result'].get('username','?')}", token)
        return False, redact(data.get("description", "getMe failed"), token)
    except Exception as e:
        return False, redact(e, token)


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


def check_hostops_reconciliation() -> tuple[bool, str]:
    """HOSTOPS-(d) (KAI-820, seq915) Layer-2 audit: every executed privileged
    host-op mutation must trace to a consumed, correctly-bound council gate.

    Any mutation with no matching gate is a possible bypass — the detectable-and-
    loud half of §3.4. Fail-loud: an unreadable audit store is itself an alert. A
    transient orchestrator outage classifies as 'transient' upstream and is not
    escalated (that is not a bypass); only a real unreconciled record or store
    error reaches #devops, after the standard consecutive-fail threshold.
    """
    try:
        r = httpx.get(f"{ORCHESTRATOR_API}/hostops/reconcile", timeout=10)
        if r.status_code != 200:
            return False, f"reconcile endpoint HTTP {r.status_code}"
        data = r.json()
    except Exception as e:
        return False, str(e)
    if data.get("error"):
        return False, f"audit store unreadable: {data['error']}"
    unreconciled = data.get("unreconciled", [])
    if unreconciled:
        first = unreconciled[0]
        return False, (
            f"{len(unreconciled)} host-op mutation(s) with no matching consumed+bound gate — "
            f"e.g. {first.get('operation')} on {first.get('site')} "
            f"gate_id={first.get('gate_id')} ({first.get('reason')})"
        )
    return True, f"{data.get('checked', 0)} host-op mutation(s) reconciled"


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

DOCKER_NETWORK = "kai-system_default"


def _container_networks(name: str) -> list:
    """Return the docker networks a container is attached to ([] if detached/unknown)."""
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format",
             "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", name],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.split() if out.returncode == 0 else []
    except Exception:
        return []


def _try_restart_container(name: str) -> str:
    """Restart a container, re-attaching it first if it is network-detached.

    KAI-1046: a bare `docker restart` preserves a container's network config, so
    it can NEVER recover a container that came up attached to zero networks (it
    keeps passing its own localhost healthcheck while reaching nothing off-box).
    Detect the empty-Networks case, re-attach to the shared network, then VERIFY
    attachment — a ✅ must mean 'recovered', not merely 'the restart command ran'.
    """
    try:
        detached = not _container_networks(name)
        if detached:
            conn = subprocess.run(
                ["docker", "network", "connect", DOCKER_NETWORK, name],
                capture_output=True, text=True, timeout=15,
            )
            if conn.returncode != 0 and "already exists" not in (conn.stderr or ""):
                return f"re-attach FAILED for {name}: {conn.stderr[:100]}"
        result = subprocess.run(
            ["docker", "restart", name],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return f"restart failed: {result.stderr[:100]}"
        if not _container_networks(name):
            return f"{name} still detached after re-attach+restart (needs host recreate)"
        prefix = "re-attached + restarted" if detached else "restarted"
        return f"{prefix} {name} ✅"
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


# ── KAI-1047 · Fleet reader ───────────────────────────────────────────────────
# The container watchdog is a READER of the host-written fleet-state file
# (scripts/fleet_heartbeat.py writes it every ~3 min; this container cannot ssh
# the fleet, so it never probes hosts itself). Container-only watch is now a
# SUBSET of the host+container fleet model. The verdict + reboot logic live in
# the SHARED pure module (fleet_eval, on /shared) so the watchdog and the
# green-baseline gate cannot drift.
from fleet_eval import fleet_verdict, compute_reboots, maint_suppresses_page

FLEET_STATE_FILE = Path("/vault/_fleet_state.json")
FLEET_REBOOTS_SEEN_FILE = Path("/vault/_fleet_reboots_seen.json")
FLEET_MAINT_FILE = Path("/vault/_fleet_maint.json")
_FLEET_MAINT_SCHEMA = "kai.fleet_maint.v1"


def _fleet_muted_now():
    """Read the operator maintenance window -> (muted_hosts:set, meta:dict).

    Empty set unless a well-formed window is present AND unexpired, so expiry is
    self-healing (auto-restore): past expires_at the mute simply stops applying,
    no cleanup needed. Malformed/partial reads as 'no window' (fail-safe: page).
    """
    m = _read_json(FLEET_MAINT_FILE)
    try:
        if not m or m.get("schema") != _FLEET_MAINT_SCHEMA:
            return set(), {}
        exp = m.get("expires_at")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            return set(), {}
        if datetime.now(timezone.utc).timestamp() >= exp:
            return set(), {}
        muted = m.get("muted")
        if not isinstance(muted, list) or not all(isinstance(x, str) for x in muted):
            return set(), {}
        return set(muted), m
    except Exception as e:
        log.warning("fleet maint window read failed (paging normally): %s", e)
        return set(), {}


def _read_json(path: Path) -> dict:
    try:
        import json as _json
        return _json.loads(path.read_text())
    except Exception:
        return {}


def check_fleet() -> tuple[bool, str]:
    return fleet_verdict(_read_json(FLEET_STATE_FILE), int(time.time()))


def surface_fleet_reboots() -> None:
    """Post an informational DevOps note for any newly-observed host reboot.

    Durable: compares each host's always-present boot_epoch against a persisted
    seen-map (fleet_eval.compute_reboots) — so a reboot is caught whenever the
    watchdog next runs, even across a watchdog outage, and a probe failure never
    erases the baseline. Not a page on its own (a rebooted-and-back host is
    healthy). At-least-once by design: the seen-map is written atomically AFTER
    the send, so a crash mid-way re-announces (safe) rather than drops (silent).
    """
    state = _read_json(FLEET_STATE_FILE)
    hosts = (state or {}).get("hosts") or {}
    if not hosts:
        return
    seen = _read_json(FLEET_REBOOTS_SEEN_FILE)
    fresh, updated = compute_reboots(hosts, seen)
    _muted, _ = _fleet_muted_now()
    if _muted:
        # Muted nodes are rebooted on purpose during a cutover: do not announce
        # their reboots, but still advance the seen-map (below) so they never
        # replay once the window ends.
        fresh = [ev for ev in fresh if ev.get("host") not in _muted]
    if updated == seen:
        return  # nothing new and no baseline to seed
    if fresh:
        try:
            from tg_alert import tg_alert
            for ev in fresh:
                tg_alert(f"[DevOps] host rebooted: {ev['host']} booted {ev.get('new_boot')} "
                         f"(boot_epoch {ev.get('prev_boot_epoch')} -> {ev.get('new_boot_epoch')})")
        except Exception as e:
            log.warning("fleet reboot surface failed: %s", e)
            return  # do NOT advance the seen-map if the send failed — retry next cycle
    try:
        import json as _json
        tmp = FLEET_REBOOTS_SEEN_FILE.with_suffix(".tmp")
        tmp.write_text(_json.dumps(updated, indent=2))
        tmp.replace(FLEET_REBOOTS_SEEN_FILE)  # atomic
    except Exception as e:
        log.warning("fleet reboot seen-map write failed: %s", e)


# ── Fleet-wide container alarm (KAI-14155ea7 / M-R1) ──────────────────────────
# green_baseline and the CHECKS list watch a hand-maintained set of NAMED services.
# This sweep covers EVERY container from `docker ps -a` (~35): any in `restarting`
# state, or `exited` non-zero, sustained past a grace window, pages via the notify()
# gateway (dashboard — a crash-loop is DevOps's to handle, not Leo's phone, Rule B).
# It runs on its own 5-min scheduler job (NOT the 30-min watchdog + 3-tick threshold)
# so a down container is detected within the 10-min SLA. exited-0 one-shots (e.g.
# plane-migrator) are healthy completions and never page.
FLEET_CONTAINER_STATE_FILE = Path("/vault/00_System/fleet_container_alarm_state.json")
FLEET_CONTAINER_GRACE_SEC = int(os.environ.get("FLEET_CONTAINER_GRACE_SEC", "600"))  # 10 min


def _fleet_bad_containers() -> dict:
    """Return {name: reason} for containers restarting or exited-nonzero right now."""
    import re
    bad: dict = {}
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as e:
        log.error("fleet container scan: docker ps -a failed: %s", e)
        return bad
    if res.returncode != 0:
        log.error("fleet container scan: docker ps -a rc=%s", res.returncode)
        return bad
    for line in res.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        status = parts[2] if len(parts) > 2 else ""
        if state == "restarting":
            bad[name] = "restarting"
        elif state == "exited":
            m = re.search(r"Exited \((\d+)\)", status)
            code = int(m.group(1)) if m else 1
            if code != 0:  # exited(0) = healthy one-shot completion, never a failure
                bad[name] = f"exited({code})"
    return bad


def _load_fleet_container_state() -> dict:
    try:
        import json as _json
        return _json.loads(FLEET_CONTAINER_STATE_FILE.read_text()).get("bad", {})
    except Exception:
        return {}


def _save_fleet_container_state(bad_state: dict) -> None:
    try:
        import json as _json
        FLEET_CONTAINER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FLEET_CONTAINER_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(_json.dumps({"bad": bad_state}, indent=2))
        tmp.replace(FLEET_CONTAINER_STATE_FILE)  # atomic
    except Exception as e:
        log.warning("fleet container state write failed: %s", e)


def run_fleet_container_check() -> None:
    """Detect → grace-gate → page once → clear-on-recovery across all containers.

    A container must be continuously bad for FLEET_CONTAINER_GRACE_SEC before it
    pages (first-seen timestamp persisted), so a container bouncing during a normal
    redeploy does not alarm. Each bad container pages once; recovery clears it so a
    later recurrence re-pages. Fail-soft — never raises into the scheduler job.
    """
    now = int(time.time())
    current = _fleet_bad_containers()
    prev = _load_fleet_container_state()

    new_state: dict = {}
    newly_pageable = []
    for name, reason in current.items():
        p = prev.get(name) or {}
        since = int(p.get("since", now))
        alerted = bool(p.get("alerted", False))
        if not alerted and (now - since) >= FLEET_CONTAINER_GRACE_SEC:
            newly_pageable.append((name, reason, now - since))
            alerted = True
        new_state[name] = {"since": since, "reason": reason, "alerted": alerted}

    recovered = [n for n in prev if n not in current and prev[n].get("alerted")]

    if newly_pageable:
        try:
            from notify_gateway import notify, Event
            lines = ", ".join(f"{n} [{r}, {age // 60}m]" for n, r, age in newly_pageable)
            names = ",".join(sorted(n for n, _, _ in newly_pageable))
            notify(Event(
                source="fleet_container_alarm",
                kind="alert",
                title=f"Container alarm — {len(newly_pageable)} container(s) down",
                body=(f"[DevOps] Sustained >{FLEET_CONTAINER_GRACE_SEC // 60}m: {lines}. "
                      f"A crash-loop or non-zero exit at the container layer — inspect "
                      f"`docker logs` / `docker inspect` on the worker."),
                audience="dashboard",
                provenance="real",
                status="down",
                cause="container in restarting or exited-nonzero state past the grace window",
                dedup_key=f"fleet_container_alarm:{names}",
            ))
            log.warning("fleet container alarm paged: %s", lines)
        except Exception as e:
            log.error("fleet container alarm: notify failed: %s", e)

    if recovered:
        log.info("fleet container recovery (alarm cleared): %s", ", ".join(recovered))

    _save_fleet_container_state(new_state)


CHECKS = [
    ("fleet",            "Fleet (hosts)",    check_fleet),
    ("worker_api",       "Worker API",       check_worker_api),
    ("council_api",      "Council API",      check_council_api),
    ("ollama",           "Ollama",           check_ollama),
    ("slack",            "Slack",            check_slack),
    ("telegram",         "Telegram",         lambda: _check_with_retry(check_telegram)),
    ("oura",             "Oura",             lambda: _check_with_retry(check_oura)),
    ("todoist",          "Todoist",          lambda: _check_with_retry(check_todoist)),
    ("google_calendar",  "Google Calendar",  lambda: _check_with_retry(check_google_calendar)),
    ("plane_ce",         "Plane CE",         check_plane_ce),
    ("hostops_reconcile","HostOps Audit",    check_hostops_reconciliation),
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
    "fleet":             ("system",    "A KAI machine is unreachable, or the host heartbeat cron died — fleet visibility lost"),
    "worker_api":        ("system",    "All KAI tools and API endpoints unavailable"),
    "council_api":       ("system",    "Chat with all advisors unavailable"),
    "ollama":            ("hardlimit", "Local model inference unavailable — Claude fallback active"),
    "slack":             ("hardlimit", "All Slack notifications and approvals unavailable"),
    "telegram":          ("hardlimit", "Telegram briefs and commands unavailable"),
    "oura":              ("hardlimit", "Health/readiness data unavailable in briefs"),
    "todoist":           ("hardlimit", "Task list unavailable — Todoist API down or token expired"),
    "google_calendar":   ("hardlimit", "OAuth token expired — cannot auto-renew"),
    "plane_ce":          ("system",    "Project management unavailable — sprint tracking broken"),
    "hostops_reconcile": ("system",    "A privileged host-op may have run without a matching approval — possible bypass"),
    "disk":              ("autofixed", "Storage high — attempting: unused image prune + build cache prune + log vacuum"),
    "backup":            ("system",    "Vault and Plane data not protected"),
    "cert_expiry":       ("hardlimit", "SSL cert expired — all HTTPS services unreachable from web"),
    "component_currency":("autofixed", "Stale KAI containers — DevOps auto-rebuilds and restarts them"),
}

ACTION_NEEDED = {
    "fleet":             "Check: ssh kai 'cat /home/leo/vault/_fleet_state.json' — a host offline needs power/tailscale/ssh restored on that box; a stale file means the fleet_heartbeat cron stopped (crontab -l | grep fleet_heartbeat)",
    "worker_api":        "Check: ssh kai 'docker logs kai-worker-api'",
    "council_api":       "Check: ssh kai 'docker logs kai-council-api'",
    "ollama":            "Check: ssh kai 'docker logs kai-ollama' — may need restart or GPU issue",
    "slack":             "Verify Slack bot token in ~/kai-system/secrets/slack_bot_token.txt",
    "telegram":          "Verify Telegram bot token in ~/kai-system/secrets/telegram_bot_token.txt",
    "oura":              "Check Oura token in ~/kai-system/secrets/oura_token.txt",
    "todoist":           "Check Todoist token in ~/kai-system/secrets/todoist_api_token.txt",
    "google_calendar":   "n8n → Credentials → Google Calendar → re-authenticate (http://100.78.94.80:5678)",
    "plane_ce":          "Check: ssh kai 'docker logs kai-plane-web' — container recreate attempted",
    "hostops_reconcile": "Investigate: curl http://kai-orchestrator:8003/hostops/reconcile — review each unreconciled mutation's gate_id against the gates table; a real bypass means a host-op ran without a consumed+bound gate",
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
    # Vestigial (Slack retired, AR-5.x): _slack_alert ignores this and routes to
    # Telegram/notify-gateway. Kept only so the legacy _slack_alert(token, ...) /
    # _post_oauth_escalation(token, ...) signatures stay intact; paging no longer
    # depends on it (see the `if failures:` gate below).
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

        # KAI cutover: node-scoped maintenance-window mute (auto-restoring).
        # Suppress the fleet page ONLY when every red host sits inside an active
        # muted window; a spine/other-node outage or lost visibility still pages
        # (maint_suppresses_page enforces this). Reboot-spam is muted the same
        # way in surface_fleet_reboots.
        if not ok and key == "fleet":
            _muted, _meta = _fleet_muted_now()
            if _muted:
                _supp, _problem = maint_suppresses_page(
                    _read_json(FLEET_STATE_FILE), int(time.time()), _muted)
                if _supp:
                    log.info("watchdog: fleet page SUPPRESSED by maintenance "
                             "window -- red %s within muted %s (expires %s)",
                             _problem, sorted(_muted), _meta.get("expires_at"))
                    _record_success(key)   # keep consecutive-fail counter clean
                    _clear_alert(key)       # real outage re-alerts cleanly after
                    continue

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

    # KAI-1047 — surface any newly-observed host reboot (informational, deduped)
    try:
        surface_fleet_reboots()
    except Exception as e:
        log.error("fleet reboot surface failed: %s", e)

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

    # 5c4e94f4: paging goes through _slack_alert -> tg_alert -> notify gateway, which
    # does NOT use the (retired) slack_bot_token. Gating the send on `token` was a
    # silent-death landmine: the day the dead Slack secret is removed, `token` goes
    # falsy and EVERY watchdog page (incl. fleet host-down) would vanish while the
    # summary logged "all checks passed". Failures must page regardless of that secret.
    if failures:
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
            # KAI-44 — a self-unfixable, system-threatening CRITICAL (disk) is Leo's
            # to act on, not dashboard noise: escalate to his Telegram. Everything
            # else stays dashboard (Rule B). The existing _should_alert cooldown that
            # gated this into `failures` also throttles the re-page cadence.
            if key in LEO_CRITICAL_CHECKS:
                _slack_alert(token, msg, audience="personal")
            else:
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

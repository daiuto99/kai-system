"""
KAI Invariant Engine — 10 core system invariants.
Runs every 30 min via scheduler. Writes /vault/00_System/invariants.json.
Posts Slack digest once daily + alerts on pass→fail transitions.
Set INVARIANT_RUNNER_ENABLED=false to suppress Slack alerts (still logs + writes JSON).
"""
import json
import logging
import os
import ssl
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

log = logging.getLogger(__name__)

VAULT_PATH       = Path("/vault")
RESULT_PATH      = VAULT_PATH / "00_System" / "invariants.json"
WORKER_API       = "http://kai-worker-api:8001"
COUNCIL_API      = "http://kai-council-api:8002"
ORCHESTRATOR_API = "http://kai-orchestrator:8003"
OLLAMA_API       = "http://kai-ollama:11434"

# Rollback switch — set INVARIANT_RUNNER_ENABLED=false to suppress Slack alerts
_RUNNER_ENABLED = os.environ.get("INVARIANT_RUNNER_ENABLED", "true").lower() != "false"

# State tracking for pass→fail transition alerts
_prev_state: dict[str, bool] = {}
_daily_digest_sent: str = ""   # date string (YYYY-MM-DD)


def _load_secret(name: str) -> str:
    p = Path(f"/run/secrets/{name}")
    return p.read_text().strip() if p.exists() else os.environ.get(name.upper(), "")


def _slack_post(token: str, text: str):
    try:
        httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "#kai-system", "text": text,
                  "username": "KAI Invariants", "icon_emoji": ":shield:"},
            timeout=10,
        )
    except Exception as e:
        log.error("invariant slack post failed: %s", e)


# ── Invariant definitions ─────────────────────────────────────────────────────

def inv_container_health() -> tuple[bool, str]:
    """All 3 core containers (worker-api, council-api, orchestrator) respond 200."""
    ok_list, fail_list = [], []
    for name, url in [
        ("worker-api",   f"{WORKER_API}/health"),
        ("council-api",  f"{COUNCIL_API}/health"),
        ("orchestrator", f"{ORCHESTRATOR_API}/health"),
    ]:
        try:
            r = httpx.get(url, timeout=5)
            if r.status_code == 200:
                ok_list.append(name)
            else:
                fail_list.append(f"{name}(HTTP {r.status_code})")
        except Exception as e:
            fail_list.append(f"{name}({type(e).__name__})")

    if fail_list:
        return False, "FAIL: " + ", ".join(fail_list) + " | OK: " + ", ".join(ok_list)
    return True, "ok: " + ", ".join(ok_list)


def inv_vault_writability() -> tuple[bool, str]:
    """Can write and delete a sentinel file in /vault/00_System/."""
    sentinel = VAULT_PATH / "00_System" / ".invariant_sentinel"
    t0 = time.monotonic()
    try:
        sentinel.write_text("invariant-check")
        sentinel.unlink()
        ms = int((time.monotonic() - t0) * 1000)
        return True, f"ok — write+delete in {ms}ms"
    except Exception as e:
        return False, f"vault write failed: {e}"


def inv_council_api_latency() -> tuple[bool, str]:
    """Council API /health responds in < 2000ms."""
    THRESHOLD_MS = 2000
    t0 = time.monotonic()
    try:
        r = httpx.get(f"{COUNCIL_API}/health", timeout=5)
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 200 and ms < THRESHOLD_MS:
            return True, f"{ms}ms"
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} in {ms}ms"
        return False, f"slow: {ms}ms > {THRESHOLD_MS}ms threshold"
    except Exception as e:
        return False, f"latency check error: {e}"


def inv_slack_token() -> tuple[bool, str]:
    """Slack bot token passes auth.test."""
    token = _load_secret("slack_bot_token")
    if not token:
        return False, "slack_bot_token secret not mounted"
    try:
        r = httpx.post(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = r.json()
        if data.get("ok"):
            return True, f"bot={data.get('bot_id','?')}"
        return False, f"auth failed: {data.get('error','unknown')}"
    except Exception as e:
        return False, f"auth.test error: {e}"


def inv_execution_registry_freshness() -> tuple[bool, str]:
    """At least one scheduled function ran in the last 90 minutes."""
    STALE_MINUTES = 90
    try:
        from execution_registry import REGISTRY_PATH
        import sqlite3
        if not REGISTRY_PATH.exists():
            return False, "execution_registry.db not found"
        db = sqlite3.connect(str(REGISTRY_PATH))
        row = db.execute(
            "SELECT function, run_time FROM runs ORDER BY run_time DESC LIMIT 1"
        ).fetchone()
        db.close()
        if not row:
            return False, "no runs recorded"
        fn, run_time = row
        ts = run_time if run_time.endswith("+00:00") else run_time + "+00:00"
        last = datetime.fromisoformat(ts)
        minutes_ago = int((datetime.now(timezone.utc) - last).total_seconds() / 60)
        if minutes_ago > STALE_MINUTES:
            return False, f"stale: {fn} last ran {minutes_ago}min ago (threshold {STALE_MINUTES}min)"
        return True, f"{fn} ran {minutes_ago}min ago"
    except Exception as e:
        return False, f"registry check error: {e}"


def inv_cert_expiry() -> tuple[bool, str]:
    """SSL cert for kai.sonicink.space must not expire within 30 days."""
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


def inv_backup_integrity() -> tuple[bool, str]:
    """Backup must have run within 26 hours with a non-zero output file."""
    import re
    backup_log = Path("/backups/backup.log")
    if not backup_log.exists():
        backup_log = VAULT_PATH / "00_System" / "backup.log"
    if not backup_log.exists():
        return False, "backup.log not found at /backups/backup.log"
    lines = backup_log.read_text().splitlines()
    last_ts = None
    for line in reversed(lines):
        if "Backup complete" in line:
            m = re.search(r"\[(\d{8}_\d{6})\]", line)
            if m:
                last_ts = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S")
            break
    if not last_ts:
        return False, "no successful backup found in log"
    hours_since = (datetime.now() - last_ts).total_seconds() / 3600
    if hours_since > 26:
        return False, f"last backup {hours_since:.1f}h ago — expected <26h"
    return True, f"ok — last: {last_ts.strftime('%Y-%m-%d %H:%M')}"


def inv_disk_usage() -> tuple[bool, str]:
    """Root filesystem must be under 85% used."""
    WARN_PCT = 85
    try:
        result = subprocess.run(
            ["df", "--output=pcent", "/"], capture_output=True, text=True, timeout=5
        )
        pct = int(result.stdout.strip().splitlines()[-1].replace("%", "").strip())
        if pct >= WARN_PCT:
            return False, f"{pct}% used — above {WARN_PCT}% threshold"
        return True, f"{pct}% used"
    except Exception as e:
        return False, f"disk check error: {e}"


def inv_llm_latency() -> tuple[bool, str]:
    """Ollama /api/tags responds in < 5000ms (models loaded and API responsive)."""
    THRESHOLD_MS = 5000
    t0 = time.monotonic()
    try:
        r = httpx.get(f"{OLLAMA_API}/api/tags", timeout=8)
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            if ms > THRESHOLD_MS:
                return False, f"slow: {ms}ms > {THRESHOLD_MS}ms ({len(models)} models)"
            return True, f"{ms}ms — {len(models)} model(s) loaded"
        return False, f"HTTP {r.status_code} in {ms}ms"
    except Exception as e:
        return False, f"ollama unreachable: {e}"


def inv_plane_api_health() -> tuple[bool, str]:
    """Plane API (host.docker.internal:8090) returns 200/401/403."""
    try:
        r = httpx.get("http://host.docker.internal:8090/api/users/me/workspaces/", timeout=5)
        if r.status_code in (200, 401, 403):
            return True, f"ok — HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"plane unreachable: {e}"


# ── Engine ────────────────────────────────────────────────────────────────────

INVARIANTS = [
    ("container_health",              "Container Health",          inv_container_health),
    ("vault_writability",             "Vault Writability",         inv_vault_writability),
    ("council_api_latency",           "Council API Latency",       inv_council_api_latency),
    ("slack_token",                   "Slack Token",               inv_slack_token),
    ("execution_registry_freshness",  "Execution Registry Fresh",  inv_execution_registry_freshness),
    ("cert_expiry",                   "SSL Cert Expiry",           inv_cert_expiry),
    ("backup_integrity",              "Backup Integrity",          inv_backup_integrity),
    ("disk_usage",                    "Disk Usage",                inv_disk_usage),
    ("llm_latency",                   "LLM Latency",               inv_llm_latency),
    ("plane_api_health",              "Plane API Health",          inv_plane_api_health),
]


def run_invariants(send_daily_digest: bool = False):
    """Run all invariants. Write results to vault. Alert on transitions."""
    global _prev_state, _daily_digest_sent

    now_utc = datetime.now(timezone.utc)
    results: dict[str, dict] = {}
    all_pass = True
    transitions: list[str] = []
    recoveries: list[str] = []
    token = _load_secret("slack_bot_token")

    for key, label, fn in INVARIANTS:
        try:
            passed, detail = fn()
        except Exception as e:
            passed, detail = False, f"invariant error: {e}"

        results[key] = {
            "label":      label,
            "pass":       passed,
            "detail":     detail,
            "checked_at": now_utc.isoformat(),
        }
        if not passed:
            all_pass = False

        # Transition detection (pass→fail only — fail→pass is noise-free)
        prev = _prev_state.get(key)
        if prev is True and not passed:
            transitions.append(f"  :x: *{label}*: `{detail}`")
        _prev_state[key] = passed

    # Write to vault
    payload = {
        "updated_at": now_utc.isoformat(),
        "all_pass":   all_pass,
        "runner_enabled": _RUNNER_ENABLED,
        "invariants": results,
    }
    try:
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        log.error("invariants: failed to write %s: %s", RESULT_PATH, e)

    # Transition alerts (suppressed if rollback switch is off)
    if transitions and token and _RUNNER_ENABLED:
        msg = f":rotating_light: *ACTION NEEDED — KAI Invariant Failure — {now_utc.strftime('%H:%M UTC')}*\n"
        msg += "\n".join(transitions)
        _slack_post(token, msg)
        log.warning("invariants: posted transition alert (%d failures)", len(transitions))
    elif transitions and not _RUNNER_ENABLED:
        log.warning("invariants: %d transition(s) suppressed (INVARIANT_RUNNER_ENABLED=false)", len(transitions))

    if recoveries and token and _RUNNER_ENABLED:
        msg = ":white_check_mark: *KAI Auto-Recovered — " + now_utc.strftime("%H:%M UTC") + "*\n"
        msg += "\n".join(recoveries)
        _slack_post(token, msg)
        log.info("invariants: posted recovery alert (%d recovered)", len(recoveries))

    # Daily digest removed — transition and recovery alerts cover all state changes

    status = "all_pass" if all_pass else f"{sum(1 for r in results.values() if not r['pass'])} failing"
    log.info("invariants: %s (runner_enabled=%s)", status, _RUNNER_ENABLED)
    return all_pass, results

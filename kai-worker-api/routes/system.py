import json
import re as _re  # noqa: F401
import shutil
import subprocess  # noqa: F401
import logging
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401
from fastapi import APIRouter
from config import VAULT_PATH

logger = logging.getLogger(__name__)
router = APIRouter()

THRESHOLDS = {"disk_pct": 80, "mem_pct": 85, "temp_c": 75, "apt_updates": 10}
APT_STATUS_FILE  = VAULT_PATH / "00_System" / "apt_status.txt"
INVARIANTS_FILE  = VAULT_PATH / "00_System" / "invariants.json"
BACKUP_TRIGGER   = VAULT_PATH / "00_System" / "backup_trigger"


def _read_proc(path: str) -> str:
    with open(path) as f:
        return f.read()


@router.get("/system/health")
def system_health():
    # Disk
    disk = shutil.disk_usage("/")
    disk_pct = round(disk.used / disk.total * 100, 1)

    # Memory from /proc/meminfo
    mem = {}
    for line in _read_proc("/proc/meminfo").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            mem[k.strip()] = int(v.strip().split()[0])
    mem_total_kb = mem.get("MemTotal", 1)
    mem_avail_kb = mem.get("MemAvailable", 0)
    mem_pct = round((mem_total_kb - mem_avail_kb) / mem_total_kb * 100, 1)

    # Load average from /proc/loadavg
    load_parts = _read_proc("/proc/loadavg").split()
    load_1m = float(load_parts[0])

    # Uptime from /proc/uptime
    uptime_sec = int(float(_read_proc("/proc/uptime").split()[0]))
    uptime_days = uptime_sec // 86400
    uptime_h = (uptime_sec % 86400) // 3600
    uptime_str = f"{uptime_days}d {uptime_h}h"

    # CPU temperature
    temp_c = None
    try:
        temp_raw = int(_read_proc("/sys/class/thermal/thermal_zone0/temp").strip())
        temp_c = round(temp_raw / 1000, 1)
    except Exception:
        pass

    # Apt updates
    apt_updates = None
    try:
        if APT_STATUS_FILE.exists():
            lines = [l for l in APT_STATUS_FILE.read_text().splitlines() if l.strip() and "Listing..." not in l]  # noqa: E741
            apt_updates = len(lines)
    except Exception:
        pass

    alerts = []
    if disk_pct >= THRESHOLDS["disk_pct"]:
        alerts.append(f"disk {disk_pct}% (threshold {THRESHOLDS['disk_pct']}%)")
    if mem_pct >= THRESHOLDS["mem_pct"]:
        alerts.append(f"memory {mem_pct}% (threshold {THRESHOLDS['mem_pct']}%)")
    if temp_c is not None and temp_c >= THRESHOLDS["temp_c"]:
        alerts.append(f"temp {temp_c}°C (threshold {THRESHOLDS['temp_c']}°C)")
    if apt_updates is not None and apt_updates >= THRESHOLDS["apt_updates"]:
        alerts.append(f"{apt_updates} pending apt updates (threshold {THRESHOLDS['apt_updates']})")

    return {
        "disk_pct": disk_pct,
        "disk_free_gb": round(disk.free / 1024 ** 3, 1),
        "disk_total_gb": round(disk.total / 1024 ** 3, 1),
        "mem_pct": mem_pct,
        "mem_free_gb": round(mem_avail_kb / 1024 / 1024, 1),
        "mem_total_gb": round(mem_total_kb / 1024 / 1024, 1),
        "load_1m": load_1m,
        "temp_c": temp_c,
        "uptime": uptime_str,
        "uptime_sec": uptime_sec,
        "apt_updates": apt_updates,
        "alerts": alerts,
        "ok": len(alerts) == 0,
        "thresholds": THRESHOLDS,
    }


# ── KAI ambient status (used by Übersicht HUD widget) ─────────────────────────

def _parse_status_yaml(path):
    result = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or not line or line == "---":
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def _last_session_title(sessions_dir):
    try:
        files = sorted(sessions_dir.glob("*.md"))
        if not files:
            return ""
        content = files[-1].read_text()
        for line in content.splitlines():
            if line.startswith("**Title:**"):
                return line.replace("**Title:**", "").strip()
    except Exception:
        pass
    return ""


@router.get("/api/status")
def kai_status():
    kai_status_path = VAULT_PATH / "20_Projects" / "KAI" / "STATUS.md"
    fields = _parse_status_yaml(kai_status_path)

    today_count = 0
    try:
        from services.todoist import get_today
        today_count = len(get_today())
    except Exception:
        pass

    last_title = _last_session_title(VAULT_PATH / "60_Council" / "sessions" / "kai")

    return {
        "status": fields.get("status", "unknown"),
        "version": fields.get("version", ""),
        "milestone": fields.get("milestone", ""),
        "milestone_pct": fields.get("milestone_pct", "0"),
        "next_action": fields.get("next", ""),
        "today_task_count": today_count,
        "last_session_title": last_title,
        "updated": fields.get("updated", ""),
    }


# ── Session context % (populated by Mac Stop hook check_context.py) ───────────

_session_context: dict = {}

from fastapi import Request as _Request  # noqa: E402, F401
from pydantic import BaseModel as _BaseModel  # noqa: E402

class _SessionContextPayload(_BaseModel):
    pct: float
    session_start_iso: str
    resets_iso: str


@router.post('/session-context')
def set_session_context(payload: _SessionContextPayload):
    global _session_context
    _session_context = payload.dict()
    return {'ok': True}


@router.get('/session-context')
def get_session_context():
    return _session_context or {'pct': 0.0, 'session_start_iso': None, 'resets_iso': None}


# ── Ops endpoints — system self-management ─────────────────────────────────────
# Architecture: backup runs on the HOST (cron). Containers communicate via:
#   - invariants.json in vault (written by scheduler every 30m) → read-only status
#   - backup_trigger file in vault (written by API) → host cron picks up within 5m

def _get_backup_invariant() -> dict:
    """Read backup status from invariants.json (written by scheduler, in vault)."""
    try:
        if INVARIANTS_FILE.exists():
            data = json.loads(INVARIANTS_FILE.read_text())
            inv = data.get("invariants", {}).get("backup_integrity", {})
            return {
                "status": "ok" if inv.get("pass") else "failing",
                "detail": inv.get("detail", "unknown"),
                "checked_at": inv.get("checked_at"),
            }
    except Exception:
        pass
    return {"status": "unknown", "detail": "invariants.json not readable"}


@router.get("/system/ops-state")
def ops_state():
    """Live system state: failing invariants + backup + trigger status."""
    failing = {}
    try:
        if INVARIANTS_FILE.exists():
            data = json.loads(INVARIANTS_FILE.read_text())
            failing = {
                k: v.get("detail", "")
                for k, v in data.get("invariants", {}).items()
                if not v.get("pass")
            }
    except Exception:
        pass

    backup = _get_backup_invariant()
    trigger_pending = BACKUP_TRIGGER.exists()

    return {
        "failing_invariants": failing,
        "backup": backup,
        "backup_trigger_pending": trigger_pending,
        "ok": len(failing) == 0,
    }


@router.post("/system/run-backup")
def run_backup():
    """Write a trigger file to vault. Host cron (every 5m) picks it up and runs backup.sh."""
    try:
        BACKUP_TRIGGER.write_text(datetime.now(timezone.utc).isoformat())
        return {"ok": True, "action": "trigger_written",
                "message": "Backup will run within 5 minutes via host cron."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/system/backup-trigger-status")
def backup_trigger_status():
    """Check if a backup trigger is pending."""
    return {"pending": BACKUP_TRIGGER.exists()}

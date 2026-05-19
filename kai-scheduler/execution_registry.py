"""Execution health registry — persistent record of every scheduled function run."""
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

REGISTRY_PATH = Path("/vault/00_System/execution_registry.db")

# Expected max gap between runs. Watchdog flags anything overdue.
EXPECTED_SCHEDULE = {
    "morning_checkin":     {"max_gap_hours": 26},
    "evening_checkin":     {"max_gap_hours": 26},
    "worker_health_check": {"max_gap_hours": 26},
    "watchdog":            {"max_gap_hours": 1},
    "inbox_scan":          {"max_gap_hours": 1},
}


def _conn():
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(REGISTRY_PATH))
    db.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            function       TEXT    NOT NULL,
            scheduled_time TEXT,
            run_time       TEXT    NOT NULL,
            result         TEXT    NOT NULL,
            error          TEXT,
            duration_s     REAL
        )
    """)
    db.commit()
    return db


def record(function_name: str, result: str, scheduled_time: str = None,
           error: str = None, duration_s: float = None):
    """Record a function execution. result: 'ok' or 'fail'."""
    run_time = datetime.now(timezone.utc).isoformat()
    with _conn() as db:
        db.execute(
            "INSERT INTO runs (function, scheduled_time, run_time, result, error, duration_s) VALUES (?,?,?,?,?,?)",
            (function_name, scheduled_time, run_time, result, error, duration_s)
        )


def get_last_run(function_name: str) -> dict | None:
    with _conn() as db:
        row = db.execute(
            "SELECT function, scheduled_time, run_time, result, error, duration_s "
            "FROM runs WHERE function=? ORDER BY run_time DESC LIMIT 1",
            (function_name,)
        ).fetchone()
    if not row:
        return None
    return dict(zip(["function", "scheduled_time", "run_time", "result", "error", "duration_s"], row))


def check_gaps() -> list[dict]:
    """Return functions that have not run within their expected schedule."""
    now = datetime.now(timezone.utc)
    gaps = []
    for fn, cfg in EXPECTED_SCHEDULE.items():
        last = get_last_run(fn)
        if last is None:
            gaps.append({"function": fn, "last_run": None, "hours_since": None,
                         "last_result": None, "last_error": None})
            continue
        last_time_str = last["run_time"]
        if not last_time_str.endswith("+00:00") and not last_time_str.endswith("Z"):
            last_time_str += "+00:00"
        last_run_time = datetime.fromisoformat(last_time_str.replace("Z", "+00:00"))
        hours_since = (now - last_run_time).total_seconds() / 3600
        if hours_since > cfg["max_gap_hours"]:
            gaps.append({
                "function": fn,
                "last_run": last["run_time"],
                "hours_since": round(hours_since, 1),
                "last_result": last["result"],
                "last_error": last.get("error"),
            })
    return gaps

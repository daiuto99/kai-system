"""WP-20.6 — Cloudways backup-POLICY reader honesty invariants.

Pins: a scheduled server reads `protected`; a server with no schedule/retention
or a terminated server reads `at_risk` (never a faked pass); last-run time is
ALWAYS `not_exposed_by_api` (the Cloudways v1 API has no last-backup timestamp,
so we never fabricate one); and a fetch failure yields error + empty servers
(honest not-checked at the board), never a green with no reading.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import wp_backup_scan as w  # noqa: E402


def _server(id="1623875", terminated="0", btime="09:25", retention="8", freq="1",
            local="0", status="running"):
    # Mirrors the LIVE Cloudways shape: flags/ints are STRINGS ('0'/'1'/'8').
    return {"id": id, "label": "kai-wp-01", "is_terminated": terminated,
            "backup_time": btime, "backup_retention": retention,
            "backup_frequency": freq, "local_backups": local, "status": status}


def test_scheduled_server_is_protected_and_never_fakes_last_run():
    state = w.scan(fetch=lambda: [_server()])
    assert state["error"] is None
    cell = state["servers"]["1623875"]
    assert cell["status"] == "protected"
    assert cell["last_run"] == "not_exposed_by_api"      # never a fabricated timestamp
    assert "9:25" not in str(cell["last_run"])           # policy time is not a last-run time
    assert "not exposed" in cell["detail"].lower()


def test_string_zero_flags_do_not_read_as_terminated():
    # Regression: bool('0') is True in Python — a live server (is_terminated='0',
    # status='running') must read protected, NOT falsely terminated/at_risk.
    cell = w.scan(fetch=lambda: [_server(terminated="0", status="running")])["servers"]["1623875"]
    assert cell["status"] == "protected"
    assert "terminated" not in cell["detail"].lower()


def test_no_schedule_is_at_risk():
    state = w.scan(fetch=lambda: [_server(btime="", retention="0")])
    assert state["servers"]["1623875"]["status"] == "at_risk"


def test_terminated_server_is_at_risk():
    cell = w.scan(fetch=lambda: [_server(terminated="1")])["servers"]["1623875"]
    assert cell["status"] == "at_risk" and "terminated" in cell["detail"].lower()


def test_non_running_server_is_at_risk():
    cell = w.scan(fetch=lambda: [_server(status="restarting")])["servers"]["1623875"]
    assert cell["status"] == "at_risk" and "restarting" in cell["detail"].lower()


def test_maps_multiple_servers_by_id():
    state = w.scan(fetch=lambda: [_server(id="111"), _server(id="222", btime="", retention=0)])
    assert set(state["servers"]) == {"111", "222"}
    assert state["servers"]["111"]["status"] == "protected"
    assert state["servers"]["222"]["status"] == "at_risk"


def test_fetch_failure_is_error_and_empty_never_faked_green():
    def boom():
        raise RuntimeError("cloudways down")
    state = w.scan(fetch=boom)
    assert state["servers"] == {} and "cloudways down" in state["error"]


def test_local_backups_flag_surfaced_in_detail():
    cell = w.scan(fetch=lambda: [_server(local=True)])["servers"]["1623875"]
    assert "local_backups on" in cell["detail"]

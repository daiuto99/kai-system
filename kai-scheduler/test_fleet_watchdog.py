"""KAI-1047 · integration tests for the watchdog fleet wiring.

The verdict/reboot LOGIC is unit-tested in test_fleet_eval; here we verify the
watchdog READER wires to it correctly and the reboot surfacer is durable +
crash-safe (seen-map advanced only after a successful send, atomically)."""
import json
import tempfile
import types
import sys
from pathlib import Path

import watchdog as w


def test_check_fleet_reads_file_and_reds_on_offline():
    import time
    d = Path(tempfile.mkdtemp())
    w.FLEET_STATE_FILE = d / "fs.json"
    w.FLEET_STATE_FILE.write_text(json.dumps({
        "schema": "kai.fleet_state.v1",
        "updated_epoch": int(time.time()),  # fresh so we reach the offline check
        "transport_loaded": True,
        "expected_hosts": ["kai-worker", "kai-mini"],
        "hosts": {
            "kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
            "kai-mini": {"reachable": False, "ssh_ok": False, "ssh_expected": True},
        },
    }))
    ok, detail = w.check_fleet()
    assert ok is False and "OFFLINE" in detail and "kai-mini" in detail


def test_check_fleet_missing_file_is_red():
    w.FLEET_STATE_FILE = Path(tempfile.mkdtemp()) / "nope.json"
    ok, detail = w.check_fleet()
    assert ok is False and "missing" in detail


def _stub_tg():
    sent = []
    mod = types.ModuleType("tg_alert")
    mod.tg_alert = lambda m: sent.append(m)
    sys.modules["tg_alert"] = mod
    return sent


def test_reboot_surfaced_once_then_deduped():
    d = Path(tempfile.mkdtemp())
    w.FLEET_STATE_FILE = d / "fs.json"
    w.FLEET_REBOOTS_SEEN_FILE = d / "seen.json"
    # seed baseline first (first observation is silent)
    w.FLEET_STATE_FILE.write_text(json.dumps({"hosts": {"71kai": {"boot_epoch": 100}}}))
    sent = _stub_tg()
    w.surface_fleet_reboots()
    assert sent == []  # first obs seeds, no page
    # now a reboot (higher boot_epoch)
    w.FLEET_STATE_FILE.write_text(json.dumps(
        {"hosts": {"71kai": {"boot_epoch": 200, "last_boot": "2026-08-05T16:47Z"}}}))
    w.surface_fleet_reboots()
    assert len(sent) == 1 and "71kai" in sent[0]
    # second pass: deduped, no new send
    w.surface_fleet_reboots()
    assert len(sent) == 1
    assert json.loads(w.FLEET_REBOOTS_SEEN_FILE.read_text())["71kai"] == 200


def test_reboot_send_failure_does_not_advance_seen_map():
    # Crash-safety (Codex #8): if the send raises, the seen-map must NOT advance,
    # so the reboot re-surfaces next cycle (never silently dropped).
    d = Path(tempfile.mkdtemp())
    w.FLEET_STATE_FILE = d / "fs.json"
    w.FLEET_REBOOTS_SEEN_FILE = d / "seen.json"
    w.FLEET_REBOOTS_SEEN_FILE.write_text(json.dumps({"71kai": 100}))
    w.FLEET_STATE_FILE.write_text(json.dumps({"hosts": {"71kai": {"boot_epoch": 200}}}))
    mod = types.ModuleType("tg_alert")

    def _boom(m):
        raise RuntimeError("telegram down")
    mod.tg_alert = _boom
    sys.modules["tg_alert"] = mod
    w.surface_fleet_reboots()
    # seen-map unchanged -> reboot still pending
    assert json.loads(w.FLEET_REBOOTS_SEEN_FILE.read_text())["71kai"] == 100

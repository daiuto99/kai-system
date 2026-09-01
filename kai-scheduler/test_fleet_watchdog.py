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


# ── a812b567: post-reboot recovery verification (watchdog wiring) ─────────────
def _fresh_state(now, ssh_ok):
    return json.dumps({
        "schema": "kai.fleet_state.v1", "updated_epoch": now, "transport_loaded": True,
        "expected_hosts": ["kai-worker"],
        "hosts": {"kai-worker": {"reachable": True, "ssh_ok": ssh_ok, "ssh_expected": True}},
    })


def test_surface_reboot_seeds_recovery_pending():
    d = Path(tempfile.mkdtemp())
    w.FLEET_STATE_FILE = d / "fs.json"
    w.FLEET_REBOOTS_SEEN_FILE = d / "seen.json"
    w.FLEET_RECOVERY_PENDING_FILE = d / "pending.json"
    w.FLEET_STATE_FILE.write_text(json.dumps({"hosts": {"kai-worker": {"boot_epoch": 100}}}))
    _stub_tg()
    w.surface_fleet_reboots()                      # baseline seed, silent
    w.FLEET_STATE_FILE.write_text(json.dumps(
        {"hosts": {"kai-worker": {"boot_epoch": 200, "last_boot": "2026-08-05T16:47Z"}}}))
    w.surface_fleet_reboots()                      # reboot -> announce + seed pending
    pend = json.loads(w.FLEET_RECOVERY_PENDING_FILE.read_text())
    assert "kai-worker" in pend and pend["kai-worker"]["boot_epoch"] == 200


def test_post_reboot_recovery_realert_then_clear():
    import time
    d = Path(tempfile.mkdtemp())
    w.FLEET_STATE_FILE = d / "fs.json"
    w.FLEET_RECOVERY_PENDING_FILE = d / "pending.json"
    now = int(time.time())
    w.FLEET_RECOVERY_PENDING_FILE.write_text(json.dumps({"kai-worker": {"since": now, "boot_epoch": 200}}))
    w._fleet_bad_containers = lambda: {}
    # hosts still ssh-blind -> INCOMPLETE re-alert
    w.FLEET_STATE_FILE.write_text(_fresh_state(now, ssh_ok=False))
    sent = _stub_tg()
    w.verify_post_reboot_recovery()
    assert any("INCOMPLETE" in m for m in sent), sent
    assert "kai-worker" in json.loads(w.FLEET_RECOVERY_PENDING_FILE.read_text())
    # now fully recovered -> COMPLETE all-clear + pending cleared
    sent.clear()
    w.FLEET_STATE_FILE.write_text(_fresh_state(now, ssh_ok=True))
    w.verify_post_reboot_recovery()
    assert any("COMPLETE" in m for m in sent), sent
    assert json.loads(w.FLEET_RECOVERY_PENDING_FILE.read_text()) == {}


def test_post_reboot_recovery_incomplete_on_bad_container():
    import time
    d = Path(tempfile.mkdtemp())
    w.FLEET_STATE_FILE = d / "fs.json"
    w.FLEET_RECOVERY_PENDING_FILE = d / "pending.json"
    now = int(time.time())
    w.FLEET_RECOVERY_PENDING_FILE.write_text(json.dumps({"kai-worker": {"since": now, "boot_epoch": 200}}))
    # hosts OK but a container is exited-nonzero -> still INCOMPLETE (off-container truth)
    w.FLEET_STATE_FILE.write_text(_fresh_state(now, ssh_ok=True))
    w._fleet_bad_containers = lambda: {"kai-worker-api": "exited(1)"}
    sent = _stub_tg()
    w.verify_post_reboot_recovery()
    assert any("INCOMPLETE" in m and "kai-worker-api" in m for m in sent), sent
    assert "kai-worker" in json.loads(w.FLEET_RECOVERY_PENDING_FILE.read_text())


def test_post_reboot_recovery_empty_pending_is_noop():
    d = Path(tempfile.mkdtemp())
    w.FLEET_RECOVERY_PENDING_FILE = d / "none.json"
    sent = _stub_tg()
    w.verify_post_reboot_recovery()
    assert sent == []


def test_post_reboot_recovery_send_failure_does_not_advance_pending():
    # At-least-once: if the alert send raises, pending state must NOT advance
    # (no last_alerted written) so it re-alerts next cycle — never silently dropped.
    import time, types, sys as _sys
    d = Path(tempfile.mkdtemp())
    w.FLEET_STATE_FILE = d / "fs.json"
    w.FLEET_RECOVERY_PENDING_FILE = d / "pending.json"
    now = int(time.time())
    w.FLEET_RECOVERY_PENDING_FILE.write_text(json.dumps({"kai-worker": {"since": now, "boot_epoch": 200}}))
    w._fleet_bad_containers = lambda: {}
    w.FLEET_STATE_FILE.write_text(_fresh_state(now, ssh_ok=False))   # incomplete -> would alert
    boom = types.ModuleType("tg_alert")
    def _raise(_m): raise RuntimeError("telegram down")
    boom.tg_alert = _raise
    _sys.modules["tg_alert"] = boom
    w.verify_post_reboot_recovery()          # must not raise, must not advance
    rec = json.loads(w.FLEET_RECOVERY_PENDING_FILE.read_text())["kai-worker"]
    assert "last_alerted" not in rec         # state NOT advanced -> re-alerts next cycle

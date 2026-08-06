"""KAI-1047 · unit tests for the SHARED fleet evaluator (fleet_eval).

Covers every Codex-flagged false-GREEN / fail-open path. Prime directive: a real
outage or lost visibility must NEVER return ok=True.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "fleet_eval", Path(__file__).resolve().parents[2] / "shared" / "fleet_eval.py")
fe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fe)

NOW = 1_000_000


def _state(hosts, expected=None, updated=NOW, transport_loaded=True, schema="kai.fleet_state.v1"):
    return {"schema": schema, "updated_epoch": updated, "transport_loaded": transport_loaded,
            "expected_hosts": expected if expected is not None else list(hosts.keys()),
            "hosts": hosts}


# ── R3-2: schema / roster-shape / hosts-shape validation ──────────────────────

def test_missing_schema_is_red():
    st = _state({"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}})
    del st["schema"]
    ok, d = fe.fleet_verdict(st, NOW + 60)
    assert ok is False and "schema" in d


def test_wrong_schema_is_red():
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        schema="something.else"), NOW + 60)
    assert ok is False and "schema" in d


def test_corrupt_schema_suffix_is_red():
    # startswith would have let this pass; exact-match must reject it.
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        schema="kai.fleet_state.corrupt"), NOW + 60)
    assert ok is False and "schema" in d


def test_subsecond_future_timestamp_is_red():
    # now + 120.9 must trip the future bound (no int() truncation to 120).
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        updated=NOW + 120.9), NOW)
    assert ok is False and "FUTURE" in d


def test_extra_nonrostered_host_cannot_affect_verdict():
    # A healthy roster plus a junk extra key (string bools, missing fields) stays
    # GREEN — extra hosts are ignored; only the roster decides.
    st = _state({"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
                expected=["kai-worker"])
    st["hosts"]["ghost"] = {"reachable": "false", "ssh_ok": "true"}  # not in roster
    ok, d = fe.fleet_verdict(st, NOW + 60)
    assert ok is True and "1 hosts reachable" in d


def test_dict_roster_is_red():
    st = _state({"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}})
    st["expected_hosts"] = {"kai-worker": 1}  # a dict, not a list
    ok, d = fe.fleet_verdict(st, NOW + 60)
    assert ok is False and "expected_hosts" in d


def test_hosts_not_object_is_red():
    st = _state({"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}})
    st["hosts"] = ["kai-worker"]
    ok, d = fe.fleet_verdict(st, NOW + 60)
    assert ok is False and "hosts" in d


# ── healthy ───────────────────────────────────────────────────────────────────

def test_all_healthy_is_ok():
    ok, d = fe.fleet_verdict(_state({
        "kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
        "71-kai-mini": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
    }), NOW + 60)
    assert ok is True and "2 hosts reachable" in d


def test_ssh_off_unexpected_is_ok_with_note():
    # mac-mini online, ssh not expected -> healthy, noted (Codex #2 boundary).
    ok, d = fe.fleet_verdict(_state({
        "kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
        "mac-mini": {"reachable": True, "ssh_ok": False, "ssh_expected": False},
    }), NOW + 60)
    assert ok is True and "ssh-off" in d and "mac-mini" in d


# ── Codex #1: offline never GREEN ─────────────────────────────────────────────

def test_offline_host_is_red():
    ok, d = fe.fleet_verdict(_state({
        "kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
        "71-kai-mini": {"reachable": False, "ssh_ok": False, "ssh_expected": True,
                        "tailscale_last_seen": "2026-08-06T12:00:00Z"},
    }), NOW + 60)
    assert ok is False and "OFFLINE" in d and "71-kai-mini" in d


# ── Codex #2: ssh-expected but down is blind -> RED ───────────────────────────

def test_online_ssh_expected_down_is_red():
    ok, d = fe.fleet_verdict(_state({
        "kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
        "71-kai-mini": {"reachable": True, "ssh_ok": False, "ssh_expected": True},
    }), NOW + 60)
    assert ok is False and "SSH-unreachable" in d and "71-kai-mini" in d


# ── Codex #3: incomplete roster never GREEN ───────────────────────────────────

def test_incomplete_roster_is_red():
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        expected=["kai-worker", "71-kai-mini", "mac-mini"]), NOW + 60)
    assert ok is False and "INCOMPLETE" in d and "71-kai-mini" in d and "mac-mini" in d


def test_empty_expected_roster_is_red():
    ok, d = fe.fleet_verdict(
        {"schema": "kai.fleet_state.v1", "updated_epoch": NOW, "transport_loaded": True,
         "expected_hosts": [], "hosts": {}}, NOW)
    assert ok is False and "roster" in d


# ── New-A: transport inventory unreadable/unconfirmed never GREEN ─────────────

def test_transport_not_loaded_is_red():
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        transport_loaded=False), NOW + 60)
    assert ok is False and "transport" in d


def test_transport_flag_absent_is_red():
    # An old/truncated state with no affirmative flag must not read GREEN.
    ok, d = fe.fleet_verdict(
        {"schema": "kai.fleet_state.v1", "updated_epoch": NOW, "expected_hosts": ["kai-worker"],
         "hosts": {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}}}, NOW)
    assert ok is False and "transport" in d


# ── New-B: malformed host metadata never GREEN ────────────────────────────────

def test_partial_host_entry_is_red():
    # {"reachable": true} with no ssh_ok/ssh_expected must not sneak through.
    ok, d = fe.fleet_verdict(_state({"kai-worker": {"reachable": True}}), NOW + 60)
    assert ok is False and "not a bool" in d


def test_string_bool_is_rejected():
    # "false" is truthy in Python — must be rejected as non-bool, not read as up.
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": "false", "ssh_ok": "true", "ssh_expected": "true"}}), NOW + 60)
    assert ok is False and "not a bool" in d


def test_host_not_object_is_red():
    ok, d = fe.fleet_verdict(_state({"kai-worker": "up"},
                                    expected=["kai-worker"]), NOW + 60)
    assert ok is False and "not an object" in d


# ── Codex #5: freshness — stale AND future both RED ───────────────────────────

def test_stale_is_red():
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        updated=NOW), NOW + 10_000)
    assert ok is False and "STALE" in d


def test_future_timestamp_is_red():
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        updated=9_999_999_999), NOW)
    assert ok is False and "FUTURE" in d


def test_missing_file_is_red():
    ok, d = fe.fleet_verdict({}, NOW)
    assert ok is False and "missing" in d


def test_no_updated_epoch_is_red():
    ok, d = fe.fleet_verdict(
        {"schema": "kai.fleet_state.v1", "hosts": {"x": {"reachable": True}}, "expected_hosts": ["x"]}, NOW)
    assert ok is False and "updated_epoch" in d


# ── compute_reboots (Codex #6/#7/#8: durable, gap-safe, idempotent) ───────────

def test_first_observation_seeds_baseline_silently():
    fresh, updated = fe.compute_reboots({"m": {"boot_epoch": 100}}, seen={})
    assert fresh == [] and updated == {"m": 100}


def test_reboot_detected_on_increase():
    fresh, updated = fe.compute_reboots({"m": {"boot_epoch": 200, "last_boot": "T"}}, seen={"m": 100})
    assert len(fresh) == 1 and updated["m"] == 200


def test_dedup_idempotent_second_pass_silent():
    hosts = {"m": {"boot_epoch": 200, "last_boot": "T"}}
    fresh1, seen1 = fe.compute_reboots(hosts, seen={"m": 100})
    assert len(fresh1) == 1
    fresh2, seen2 = fe.compute_reboots(hosts, seen=seen1)
    assert fresh2 == [] and seen2 == seen1


def test_none_boot_epoch_does_not_erase_baseline():
    # Codex #7: an offline/ssh-failed probe (boot_epoch=None) must not wipe the
    # known baseline; recovery with a new epoch is then correctly a reboot.
    fresh, updated = fe.compute_reboots({"m": {"boot_epoch": None}}, seen={"m": 100})
    assert fresh == [] and updated == {"m": 100}
    fresh2, updated2 = fe.compute_reboots({"m": {"boot_epoch": 300, "last_boot": "T"}}, seen=updated)
    assert len(fresh2) == 1 and updated2["m"] == 300

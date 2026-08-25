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
        "kai-mini": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
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
        "kai-mini": {"reachable": False, "ssh_ok": False, "ssh_expected": True,
                        "tailscale_last_seen": "2026-08-06T12:00:00Z"},
    }), NOW + 60)
    assert ok is False and "OFFLINE" in d and "kai-mini" in d


# ── Codex #2: ssh-expected but down is blind -> RED ───────────────────────────

def test_online_ssh_expected_down_is_red():
    ok, d = fe.fleet_verdict(_state({
        "kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True},
        "kai-mini": {"reachable": True, "ssh_ok": False, "ssh_expected": True},
    }), NOW + 60)
    assert ok is False and "SSH-unreachable" in d and "kai-mini" in d


# ── Codex #3: incomplete roster never GREEN ───────────────────────────────────

def test_incomplete_roster_is_red():
    ok, d = fe.fleet_verdict(_state(
        {"kai-worker": {"reachable": True, "ssh_ok": True, "ssh_expected": True}},
        expected=["kai-worker", "kai-mini", "mac-mini"]), NOW + 60)
    assert ok is False and "INCOMPLETE" in d and "kai-mini" in d and "mac-mini" in d


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


# ── fleet_gate_verdict (green-baseline GATE severity) ─────────────────────────

def _H(reachable=True, ssh_ok=True, ssh_expected=True):
    return {"reachable": reachable, "ssh_ok": ssh_ok, "ssh_expected": ssh_expected}


def test_gate_all_healthy_is_ok():
    st = _state({"kai-worker": _H(), "kai-mini": _H()})
    ok, d = fe.fleet_gate_verdict(st, NOW + 60, "kai-worker")
    assert ok is True and "spine kai-worker OK" in d


def test_gate_spine_down_is_red():
    st = _state({"kai-worker": _H(reachable=False), "kai-mini": _H()})
    ok, d = fe.fleet_gate_verdict(st, NOW + 60, "kai-worker")
    assert ok is False and "SPINE" in d and "kai-worker" in d


def test_gate_spine_ssh_blind_is_red():
    st = _state({"kai-worker": _H(ssh_ok=False), "kai-mini": _H()})
    ok, d = fe.fleet_gate_verdict(st, NOW + 60, "kai-worker")
    assert ok is False and "SPINE" in d


def test_gate_peer_down_is_warn_not_red():
    # A flapping aux node must NOT fail the push gate — warn, stay ok.
    st = _state({"kai-worker": _H(), "kai-mini": _H(reachable=False)})
    ok, d = fe.fleet_gate_verdict(st, NOW + 60, "kai-worker")
    assert ok is True and "WARN offline" in d and "kai-mini" in d


def test_gate_peer_down_muted_reads_muted_not_paging():
    # During a cutover window the muted node must NOT read "watchdog paging"
    # (the watchdog suppresses its page); it reads "muted" instead.
    st = _state({"kai-worker": _H(), "kai-mini": _H(reachable=False)})
    ok, d = fe.fleet_gate_verdict(st, NOW + 60, "kai-worker", muted={"kai-mini"})
    assert ok is True
    assert "muted: maintenance window" in d and "watchdog paging" not in d


def test_gate_visibility_loss_is_red_even_with_spine_up():
    # Stale heartbeat = blind monitoring => gate fails regardless of spine.
    st = _state({"kai-worker": _H()}, updated=NOW)
    ok, d = fe.fleet_gate_verdict(st, NOW + 10_000, "kai-worker")
    assert ok is False and "STALE" in d


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


def test_warn_signals_carry_a_parenthetical_cause():
    """Findings Contract (KAI-1100 b): any WARN a fleet detail emits must carry
    its cause in-line as a parenthetical — a warn-class signal is never bare.
    This is the test-level teeth for the WARN-in-GREEN-detail gap: green_baseline
    surfaces this detail string to Leo, so an uncaused WARN would be dishonest."""
    # Craft a state that trips BOTH warn paths at once: one peer offline, one
    # peer ssh-blind. Roster derives from hosts.keys(); all bools supplied by _H.
    st = _state({
        "kai-worker": _H(),
        "kai-mini": _H(reachable=False),
        "72-kai-aux": _H(ssh_ok=False),
    })
    ok, d = fe.fleet_gate_verdict(st, NOW + 60, "kai-worker")
    assert ok is True, d
    warn_segments = [seg for seg in d.split(";") if "WARN" in seg]
    assert warn_segments, f"crafted state emitted no WARN signal: {d!r}"
    for seg in warn_segments:
        label = seg.split(":", 1)[0]  # the label before the host list
        assert "(" in label and ")" in label, f"bare WARN without cause: {seg!r}"


# ── KAI-1240: reachable-but-degraded health verdict ───────────────────────────

def _HH(reachable=True, ssh_ok=True, ssh_expected=True, services=None, health=None):
    h = _H(reachable=reachable, ssh_ok=ssh_ok, ssh_expected=ssh_expected)
    h["services"] = services if services is not None else {"ollama": True, "tailscaled": True}
    h["health"] = health if health is not None else {"disk_pct": 40, "mem_avail_pct": 60}
    return h


def test_host_degradations_healthy_is_empty():
    assert fe.host_degradations(_HH()) == []


def test_host_degradations_ollama_down():
    reasons = fe.host_degradations(_HH(services={"ollama": False, "tailscaled": True}))
    assert reasons == ["ollama :11434 down"]


def test_host_degradations_tailscaled_down():
    reasons = fe.host_degradations(_HH(services={"ollama": True, "tailscaled": False}))
    assert reasons == ["tailscaled daemon down"]


def test_host_degradations_disk_at_threshold_is_degraded():
    # >= threshold, so exactly the bound trips it.
    reasons = fe.host_degradations(_HH(health={"disk_pct": fe.DISK_DEGRADE_PCT, "mem_avail_pct": 60}))
    assert any("disk" in r for r in reasons)


def test_host_degradations_mem_low_is_degraded():
    reasons = fe.host_degradations(_HH(health={"disk_pct": 40, "mem_avail_pct": fe.MEM_AVAIL_DEGRADE_PCT}))
    assert any("mem avail" in r for r in reasons)


def test_host_degradations_disk_just_under_threshold_ok():
    reasons = fe.host_degradations(_HH(health={"disk_pct": fe.DISK_DEGRADE_PCT - 1, "mem_avail_pct": 60}))
    assert reasons == []


def test_missing_signal_on_probeable_node_is_unknown_not_green():
    # A blind health signal must never read healthy (KAI-1046 class).
    reasons = fe.host_degradations(_HH(services={}, health={}), expect_health=True)
    assert any("unknown" in r for r in reasons)
    assert len(reasons) == 4  # ollama, tailscaled, disk, mem all unknown


def test_missing_signal_when_not_probeable_is_silent():
    # A node we cannot ssh-probe: absence is expected, not a fault.
    assert fe.host_degradations(_HH(services={}, health={}), expect_health=False) == []


def test_pct_string_bool_is_ignored():
    # a bool sneaking into a *_pct slot must not be read as a number.
    reasons = fe.host_degradations(_HH(health={"disk_pct": True, "mem_avail_pct": 60}), expect_health=True)
    assert any("disk usage unknown" in r for r in reasons)


def test_fleet_degradations_skips_spine():
    st = _state({"kai-worker": _HH(services={}, health={}), "kai-mini": _HH()})
    st["self_host"] = "kai-worker"
    # spine has empty signals but is skipped; mini is healthy → no degradations.
    assert fe.fleet_degradations(st, NOW + 60) == {}


def test_fleet_degradations_reports_degraded_mini():
    st = _state({
        "kai-worker": _HH(),
        "kai-mini": _HH(services={"ollama": False, "tailscaled": True},
                        health={"disk_pct": 95, "mem_avail_pct": 60}),
    })
    st["self_host"] = "kai-worker"
    out = fe.fleet_degradations(st, NOW + 60)
    assert "kai-mini" in out
    assert any("ollama" in r for r in out["kai-mini"])
    assert any("disk" in r for r in out["kai-mini"])


def test_fleet_degradations_skips_unreachable_host():
    # an OFFLINE host is the reachability verdict's job, not the degrade layer.
    st = _state({
        "kai-worker": _HH(),
        "kai-mini": _HH(reachable=False, ssh_ok=False, services={}, health={}),
    })
    st["self_host"] = "kai-worker"
    assert fe.fleet_degradations(st, NOW + 60) == {}


def test_fleet_degradations_empty_on_lost_visibility():
    # stale state → no in-scope hosts (the strict/gate verdict owns the RED).
    st = _state({"kai-worker": _HH(), "kai-mini": _HH()}, updated=NOW - 10_000)
    st["self_host"] = "kai-worker"
    assert fe.fleet_degradations(st, NOW) == {}

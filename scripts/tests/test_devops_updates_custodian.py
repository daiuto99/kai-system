"""KAI-47 Phase 2 — updates/patching custodian decision logic."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_updates_custodian",
    Path(__file__).resolve().parent.parent / "devops_updates_custodian.py")
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)


def _by_check(specs):
    return {s["check"]: s for s in specs}


def test_clean_host_yields_nothing():
    # security=0, none applicable, no reboot, no zombies
    assert up.classify_hygiene(0, False, False, []) == []


def test_applicable_security_updates_are_auto():
    specs = _by_check(up.classify_hygiene(3, True, False, []))
    assert specs["security_updates"]["disposition"] == "auto"
    assert specs["security_updates"]["detail"]["security"] == 3


def test_held_back_security_updates_are_structural_not_fake_auto():
    # the live case: 3 pending but unattended-upgrade can apply none → STRUCTURAL,
    # never a no-op AUTO that reports success while the count stays 3
    specs = _by_check(up.classify_hygiene(3, False, False, []))
    assert "security_updates" not in specs
    assert specs["security_updates_held"]["disposition"] == "structural"


def test_zombies_are_auto():
    specs = _by_check(up.classify_hygiene(0, False, False, [2340, 3148]))
    assert specs["zombies"]["disposition"] == "auto"
    assert specs["zombies"]["detail"]["ppids"] == [2340, 3148]


def test_reboot_required_is_structural_never_a_gate():
    # a non-urgent pending reboot must be QUEUED (deduped), never a blocking decision gate
    specs = _by_check(up.classify_hygiene(0, False, True, []))
    assert specs["reboot_required"]["disposition"] == "structural"


def test_no_dispositions_are_decision():
    # updates never raises a blocking decision gate (would page Leo every sweep)
    specs = up.classify_hygiene(9, True, True, [2340])
    assert all(s["disposition"] != "decision" for s in specs)


def test_unknown_security_count_yields_no_apply():
    # apt-check unavailable (None) must not trigger a phantom apply
    specs = _by_check(up.classify_hygiene(None, False, False, []))
    assert "security_updates" not in specs and "security_updates_held" not in specs


def test_all_three_signals_produce_three_findings():
    specs = up.classify_hygiene(3, True, True, [2340, 3148])
    assert len(specs) == 3

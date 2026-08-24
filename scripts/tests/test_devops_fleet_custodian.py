"""KAI-53 Phase 2 — fleet custodian decision logic."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_fleet_custodian",
    Path(__file__).resolve().parent.parent / "devops_fleet_custodian.py")
fl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fl)


def test_healthy_fleet_no_finding():
    assert fl.fleet_severity("3/3 hosts reachable; spine kai-worker OK", False) is None


def test_unhealthy_fleet_raises_to_crit():
    assert fl.fleet_severity(None, True) == "crit"


def test_muted_or_warn_note_is_warn():
    assert fl.fleet_severity("WARN aux node muted", False) == "warn"


def test_empty_is_not_a_finding():
    assert fl.fleet_severity("", False) is None

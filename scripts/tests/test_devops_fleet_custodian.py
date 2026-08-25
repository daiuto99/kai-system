"""KAI-53 Phase 2 — fleet custodian decision logic."""
import importlib.util
import sys
import time
import types
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


# ── KAI-1240: assess() also files reachable-but-degraded node-health findings ──

def _install_stubs(check_fleet_ret="3/3 reachable; spine kai-worker OK", raises=False):
    """Stub the heavy local imports assess() pulls (green_baseline, devops_ownership).
    fleet_eval stays REAL — we want the true degrade verdict exercised."""
    gb = types.ModuleType("green_baseline")

    def check_fleet():
        if raises:
            raise RuntimeError("fleet blind")
        return check_fleet_ret
    gb.check_fleet = check_fleet
    sys.modules["green_baseline"] = gb

    do = types.ModuleType("devops_ownership")
    do.STRUCTURAL = "STRUCTURAL"

    class Finding:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    do.Finding = Finding
    sys.modules["devops_ownership"] = do


def _state(hosts):
    return {"schema": "kai.fleet_state.v1", "updated_epoch": int(time.time()),
            "transport_loaded": True, "self_host": "kai-worker",
            "expected_hosts": list(hosts.keys()), "hosts": hosts}


def _host(reachable=True, ssh_ok=True, services=None, health=None):
    return {"reachable": reachable, "ssh_ok": ssh_ok, "ssh_expected": True,
            "services": services if services is not None else {"ollama": True, "tailscaled": True},
            "health": health if health is not None else {"disk_pct": 40, "mem_avail_pct": 60}}


def test_assess_reports_degraded_mini(monkeypatch):
    _install_stubs()  # reachability healthy
    monkeypatch.setattr(fl, "_read_fleet_state", lambda: _state({
        "kai-worker": _host(),
        "kai-mini": _host(services={"ollama": False, "tailscaled": True},
                          health={"disk_pct": 95, "mem_avail_pct": 60}),
    }))
    findings = fl.FleetCustodian().assess()
    node = [f for f in findings if f.check == "node_health"]
    assert len(node) == 1
    assert node[0].dedup_key == "fleet-node-health-kai-mini"
    assert node[0].disposition == "STRUCTURAL" and node[0].severity == "warn"
    assert "ollama" in node[0].diagnosis and "disk" in node[0].diagnosis


def test_assess_clean_fleet_no_findings(monkeypatch):
    _install_stubs()
    monkeypatch.setattr(fl, "_read_fleet_state", lambda: _state({
        "kai-worker": _host(), "kai-mini": _host()}))
    assert fl.FleetCustodian().assess() == []


def test_assess_degrade_read_error_is_warn(monkeypatch):
    _install_stubs()

    def _boom():
        raise RuntimeError("cannot read state")
    monkeypatch.setattr(fl, "_read_fleet_state", _boom)
    findings = fl.FleetCustodian().assess()
    err = [f for f in findings if f.dedup_key == "fleet-node-health-error"]
    assert len(err) == 1 and err[0].severity == "warn"

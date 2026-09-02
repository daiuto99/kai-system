"""CUR-6 — fleet currency (extend the readers to other hosts over SSH).

Pins the honesty + non-breaking invariants of the fleet expansion:
  - a fleet node reports ONLY its own host-local layers (os_apt, container_images)
    via emit_layers(), with no state write and no fleet recursion;
  - read_fleet_nodes() folds a reachable node's layers under its name with
    reachable=True, and fails CLOSED to reachable=False WITH a reason on a
    non-zero ssh exit, unparseable output, or a raised exception — never a
    faked pass and never a crash that takes down the worker scan;
  - the node list comes from the optional JSON override when present and valid,
    else the built-in default.
"""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared"))

import currency_scan  # noqa: E402


def _result(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# --- emit_layers: node self-report is host-local only --------------------
def test_emit_layers_is_host_local_only(monkeypatch):
    monkeypatch.setattr(currency_scan, "_LAYER_READERS", {
        "os_apt": lambda: {"status": "stale", "detail": "1 security"},
        "container_images": lambda: {"status": "fresh", "detail": "ok"},
    })
    out = currency_scan.emit_layers()
    assert set(out["layers"]) == {"os_apt", "container_images"}
    assert "host" in out and "generated_at" in out
    # no worker-only layers leak into a node self-report
    assert "wp_fleet" not in out["layers"] and "py_deps" not in out["layers"]


# --- read_fleet_nodes: reachable node folds its layers -------------------
def test_read_fleet_nodes_reachable(monkeypatch):
    monkeypatch.setattr(currency_scan, "_fleet_nodes",
                        lambda: [{"name": "kai-mini", "ssh": "kai-mini"}])
    payload = {"host": "kai-mini",
               "layers": {"os_apt": {"status": "stale"},
                          "container_images": {"status": "not-checked"}}}

    def runner(argv, stdin=None, timeout=180):
        assert "--emit-layers" in argv and stdin  # the script IS streamed to the node
        return _result(stdout=json.dumps(payload))

    out = currency_scan.read_fleet_nodes(script_text="x", runner=runner)
    node = out["kai-mini"]
    assert node["reachable"] is True
    assert node["host"] == "kai-mini"
    assert node["layers"]["os_apt"]["status"] == "stale"


# --- read_fleet_nodes: fail-closed on ssh error / bad output / exception --
def test_read_fleet_nodes_ssh_failure_is_unreachable(monkeypatch):
    monkeypatch.setattr(currency_scan, "_fleet_nodes",
                        lambda: [{"name": "kai-mini", "ssh": "kai-mini"}])
    out = currency_scan.read_fleet_nodes(
        script_text="x",
        runner=lambda argv, stdin=None, timeout=180: _result(returncode=255, stderr="Connection refused"))
    node = out["kai-mini"]
    assert node["reachable"] is False
    assert "Connection refused" in node["detail"]
    assert "layers" not in node  # no faked layers when unreachable


def test_read_fleet_nodes_bad_json_is_unreachable(monkeypatch):
    monkeypatch.setattr(currency_scan, "_fleet_nodes",
                        lambda: [{"name": "kai-mini", "ssh": "kai-mini"}])
    out = currency_scan.read_fleet_nodes(
        script_text="x",
        runner=lambda argv, stdin=None, timeout=180: _result(stdout="not json"))
    assert out["kai-mini"]["reachable"] is False


def test_read_fleet_nodes_exception_is_unreachable(monkeypatch):
    monkeypatch.setattr(currency_scan, "_fleet_nodes",
                        lambda: [{"name": "kai-mini", "ssh": "kai-mini"}])

    def boom(argv, stdin=None, timeout=180):
        raise TimeoutError("timed out")

    out = currency_scan.read_fleet_nodes(script_text="x", runner=boom)
    assert out["kai-mini"]["reachable"] is False
    assert "TimeoutError" in out["kai-mini"]["detail"]


# --- _fleet_nodes: override honored when valid, else default -------------
def test_fleet_nodes_default_and_override(monkeypatch, tmp_path):
    monkeypatch.setattr(currency_scan, "FLEET_NODES_CONFIG", tmp_path / "absent.json")
    assert currency_scan._fleet_nodes() == currency_scan.FLEET_NODES_DEFAULT

    cfg = tmp_path / "fleet_nodes.json"
    cfg.write_text(json.dumps([{"name": "m4", "ssh": "m4-host"}]))
    monkeypatch.setattr(currency_scan, "FLEET_NODES_CONFIG", cfg)
    assert currency_scan._fleet_nodes() == [{"name": "m4", "ssh": "m4-host"}]

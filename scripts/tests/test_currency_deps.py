"""CUR-2 — Python + npm dependency currency readers (report-only).

Pins the honesty invariants: outdated deps read STALE with a cause (satisfying
the Findings Contract), a non-python container is silently skipped (never a
faked "current"), the CVE dimension is not-checked when no offline OSV feed is
present (never a live CVE SaaS in the hot path), and nothing here applies a bump.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared"))

import currency_scan  # noqa: E402
import findings  # noqa: E402


def _outdated(pkgs):
    """A runner that reports every named container as a python service with `pkgs` outdated."""
    payload = json.dumps([{"name": p, "version": "1.0", "latest_version": "2.0"} for p in pkgs])
    return lambda argv: (0, payload, "")


# ── py_deps ───────────────────────────────────────────────────────────────────
def test_py_deps_current_service_is_fresh_and_uncaused():
    layer = currency_scan.read_py_deps(names=["kai-worker-api"], runner=_outdated([]))
    assert layer["status"] == "fresh"
    comp = layer["components"][0]
    assert comp["current"] is True and comp["outdated_count"] == 0
    assert "cause" not in comp and "cause" not in layer  # a good state carries no cause


def test_py_deps_outdated_is_stale_with_cause_and_satisfies_contract():
    layer = currency_scan.read_py_deps(names=["kai-worker-api"], runner=_outdated(["anthropic", "fastapi"]))
    comp = layer["components"][0]
    assert layer["status"] == "stale"
    assert comp["outdated_count"] == 2 and comp["current"] is False
    assert comp["cause"] and layer["cause"]
    findings.assert_contract({"py_deps": layer})  # the contract has teeth and passes


def test_py_deps_non_python_container_is_skipped_never_faked():
    layer = currency_scan.read_py_deps(names=["buzz-postgres"], runner=lambda argv: (1, "", "no python"))
    assert layer["status"] == "not-checked"        # nothing scanned -> honest not-checked
    assert layer["components"] == []               # no faked "current"


def test_py_deps_cve_is_always_not_checked_never_faked():
    # CUR-2 builds no OSV matcher, so CVE must read not-checked — never a faked "checked".
    comp = currency_scan.read_py_deps(names=["kai-worker-api"], runner=_outdated([]))["components"][0]
    assert comp["cve_check"] == "not-checked"
    assert "OSV" in comp["cve_note"] and "not yet implemented" in comp["cve_note"]


def test_py_deps_malformed_pip_json_is_not_checked_never_crashes():
    # non-list JSON, or a list containing non-objects, is malformed -> not-checked (never a bogus count)
    for payload in ('{"oops": 1}', '[1, 2, "x"]', '"scalar"', '42'):
        layer = currency_scan.read_py_deps(names=["kai-worker-api"], runner=lambda argv, p=payload: (0, p, ""))
        comp = layer["components"][0]
        assert comp["status"] == "not-checked" and "outdated_count" not in comp


def test_py_deps_hostile_container_name_never_reaches_docker_as_flag():
    seen = {}

    def spy(argv):
        seen["argv"] = argv
        return (1, "", "")

    currency_scan.read_py_deps(names=["--privileged"], runner=spy)
    assert "argv" not in seen  # invalid name skipped before any docker exec
    currency_scan.read_py_deps(names=["kai-worker-api"], runner=spy)
    assert seen["argv"][:3] == ["docker", "exec", "--"]  # option terminator guards the name


# ── npm_deps ──────────────────────────────────────────────────────────────────
def test_npm_deps_inventory_is_not_checked_never_faked(tmp_path):
    lock = tmp_path / "package-lock.json"
    lock.write_text(json.dumps({"packages": {"": {}, "node_modules/react": {}, "node_modules/next": {}}}))
    layer = currency_scan.read_npm_deps(lockfile=str(lock))
    comp = layer["components"][0]
    assert layer["status"] == "not-checked"        # honest: no npm runtime / no feed
    assert comp["locked_deps"] == 2 and comp["current"] is None  # inventory real, currency unknown
    assert comp["audit_check"] == "not-checked"


def test_npm_deps_missing_lockfile_is_not_checked(tmp_path):
    layer = currency_scan.read_npm_deps(lockfile=str(tmp_path / "nope.json"))
    assert layer["status"] == "not-checked" and layer["components"] == []


# ── whole-set contract ────────────────────────────────────────────────────────
def test_both_layers_satisfy_the_findings_contract():
    layers = {
        "py_deps": currency_scan.read_py_deps(names=["kai-worker-api"], runner=_outdated(["urllib3"])),
        "npm_deps": currency_scan.read_npm_deps(lockfile="/does/not/exist.json"),
    }
    findings.enforce_causes(layers)
    findings.assert_contract(layers)  # no bad-status finding lacks a cause

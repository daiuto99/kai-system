"""CUR-3 — WordPress fleet currency reader (report-only).

Pins the honesty invariants: updates-available reads STALE with a cause (so it
satisfies the Findings Contract), an unreadable site is NOT_CHECKED and never a
faked "current", and nothing here can apply an update.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "shared"))

import currency_scan  # noqa: E402
import findings  # noqa: E402

_KEY = "/tmp/fake-cloudways-key"  # injected; never touched (runner is mocked)


class _R:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode


def _ok(core, plugins):
    return lambda argv: _R(f"\n===CORE===\n{core}\n===PLUGINS===\n{plugins}\n===DONE===\n")


def _site(sysuser="aaa111"):
    return {"s": {"cloudways_sys_user": sysuser}}


def test_current_site_is_fresh_and_uncaused():
    layer = currency_scan.read_wp_fleet(sites=_site(), runner=_ok(0, 0), key=_KEY)
    assert layer["status"] == "fresh"
    assert layer["components"][0]["current"] is True
    assert "cause" not in layer  # a good state carries no cause


def test_updates_available_is_stale_with_cause_and_satisfies_contract():
    layer = currency_scan.read_wp_fleet(sites=_site(), runner=_ok(1, 3), key=_KEY)
    comp = layer["components"][0]
    assert layer["status"] == "stale"
    assert comp["core_updates"] == 1 and comp["plugin_updates"] == 3 and comp["current"] is False
    assert comp["cause"] and layer["cause"]
    findings.assert_contract({"wp_fleet": layer})  # the contract has teeth and passes


def test_unscannable_site_is_not_checked_never_faked_current():
    layer = currency_scan.read_wp_fleet(
        sites=_site(), runner=lambda argv: _R("__CURERR__:cd_failed\n", 3), key=_KEY)
    comp = layer["components"][0]
    assert comp["status"] == "not-checked"
    assert "current" not in comp  # honest: no faked "current"


def test_incomplete_output_is_not_checked():
    layer = currency_scan.read_wp_fleet(
        sites=_site(), runner=lambda argv: _R("\n===CORE===\n0\n"), key=_KEY)  # no ===DONE===
    assert layer["components"][0]["status"] == "not-checked"


def test_invalid_sysuser_is_not_checked_no_ssh_attempted():
    calls = []
    layer = currency_scan.read_wp_fleet(
        sites=_site("BAD USER!"), runner=lambda argv: calls.append(argv) or _ok(0, 0)(argv), key=_KEY)
    assert layer["components"][0]["status"] == "not-checked"
    assert calls == []  # a malformed sys user never reaches the shell


def test_missing_config_is_not_checked_not_crash(monkeypatch):
    # No sites -> not-checked (never a faked green).
    assert currency_scan.read_wp_fleet(sites={}, runner=_ok(0, 0), key=_KEY)["status"] == "not-checked"
    # No resolvable SSH key -> not-checked, no crash, runner never invoked.
    monkeypatch.setattr(currency_scan, "_cloudways_key", lambda: None)
    calls = []
    layer = currency_scan.read_wp_fleet(
        sites=_site(), runner=lambda argv: calls.append(argv) or _ok(0, 0)(argv))
    assert layer["status"] == "not-checked" and calls == []


def test_mixed_fleet_rolls_up_to_stale_with_readable_current_count():
    sites = {"a": {"cloudways_sys_user": "aaa111"}, "b": {"cloudways_sys_user": "bbb222"}}
    outs = {"aaa111": _R("\n===CORE===\n0\n===PLUGINS===\n0\n===DONE===\n"),
            "bbb222": _R("\n===CORE===\n0\n===PLUGINS===\n2\n===DONE===\n")}

    def runner(argv):
        return outs["aaa111"] if "aaa111" in argv[-1] else outs["bbb222"]

    layer = currency_scan.read_wp_fleet(sites=sites, runner=runner, key=_KEY)
    assert layer["status"] == "stale"
    assert "1/2 sites current" in layer["detail"] and "2 update(s)" in layer["detail"]
    findings.assert_contract({"wp_fleet": layer})

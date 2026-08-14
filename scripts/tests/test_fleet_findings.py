"""fleet_heartbeat routes host findings through the Findings Contract: a host
marked offline/degraded can never be published without a cause. Proves both the
happy path (existing `degraded` reason becomes the cause) and the guard (a bad
host with no reason is stamped not-yet-diagnosed, never shipped bare)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))            # scripts/ (fleet_heartbeat)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "shared"))  # shared/ (findings)

import findings  # noqa: E402
from fleet_heartbeat import _apply_status  # noqa: E402


def test_offline_host_with_reason_carries_cause():
    hosts = {"h": {"reachable": False, "degraded": "offline (tailnet unreachable)"}}
    _apply_status(hosts)
    assert hosts["h"]["status"] == "offline"
    assert hosts["h"]["cause"] == "offline (tailnet unreachable)"
    assert findings.enforce_causes(hosts) == 0
    findings.assert_contract(hosts)  # must not raise


def test_offline_host_without_reason_is_stamped_undiagnosed():
    hosts = {"h": {"reachable": False}}  # no `degraded` reason set
    _apply_status(hosts)
    assert hosts["h"]["status"] == "offline"
    assert findings.enforce_causes(hosts) == 1
    assert hosts["h"]["cause"] == findings.NOT_YET_DIAGNOSED
    findings.assert_contract(hosts)  # now satisfied — never a bare alarm


def test_ssh_intentionally_off_is_ok_not_a_fault():
    hosts = {"h": {"reachable": True, "ssh_ok": False, "ssh_expected": False,
                   "degraded": "online, ssh intentionally off"}}
    _apply_status(hosts)
    assert hosts["h"]["status"] == "ok"
    assert findings.enforce_causes(hosts) == 0


def test_ssh_expected_but_down_is_degraded_with_cause():
    hosts = {"h": {"reachable": True, "ssh_ok": False, "ssh_expected": True,
                   "degraded": "online but ssh-unreachable"}}
    _apply_status(hosts)
    assert hosts["h"]["status"] == "degraded"
    assert hosts["h"]["cause"] == "online but ssh-unreachable"
    assert findings.enforce_causes(hosts) == 0

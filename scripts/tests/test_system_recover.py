"""Buzz emergency recovery (POST /system/recover core) — the on-demand twin of
the autonomous services-custodian sweep.

Pins the invariants: the default target IS the Buzz app tier; every targeted
container is restarted and reports before→after; only_if_down leaves a running
container alone; an UNKNOWN container name is dropped (never restarted — no
arbitrary-container restart from a phone); and a per-service docker failure is
reported as an error on that service WITHOUT aborting recovery of the others.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

import buzz_recovery  # noqa: E402


class _Container:
    def __init__(self, status):
        self.status = status
        self.restarted = False

    def restart(self):
        self.restarted = True
        self.status = "running"

    def reload(self):
        pass


class _Boom(_Container):
    def restart(self):
        raise RuntimeError("docker socket refused")


class _Client:
    """Fake docker SDK client. mapping: name -> _Container (missing -> NotFound)."""
    def __init__(self, mapping):
        self._m = mapping

    @property
    def containers(self):
        m = self._m

        class _C:
            def get(self, name):
                if name not in m:
                    raise KeyError(name)  # stands in for docker NotFound
                return m[name]
        return _C()


def test_default_targets_are_the_buzz_app_tier():
    m = {n: _Container("running") for n in buzz_recovery.BUZZ_RECOVERY_SERVICES}
    actions = buzz_recovery.recover_buzz(_Client(m))
    assert [a["service"] for a in actions] == list(buzz_recovery.BUZZ_RECOVERY_SERVICES)
    assert all(m[a["service"]].restarted for a in actions)
    assert all(a["action"] == "restarted" and a["after"] == "running" for a in actions)


def test_only_if_down_leaves_running_alone():
    m = {"kai-buzz": _Container("running"), "kai-buzz-shim": _Container("exited")}
    actions = buzz_recovery.recover_buzz(
        _Client(m), targets=["kai-buzz", "kai-buzz-shim"], only_if_down=True)
    by = {a["service"]: a for a in actions}
    assert m["kai-buzz"].restarted is False
    assert by["kai-buzz"]["action"] == "skipped (already running)"
    assert m["kai-buzz-shim"].restarted is True
    assert by["kai-buzz-shim"]["action"] == "restarted"


def test_unknown_container_is_dropped_not_restarted():
    m = {"kai-buzz": _Container("running")}
    actions = buzz_recovery.recover_buzz(
        _Client(m), targets=["kai-buzz", "kai-worker-api", "buzz-postgres"])
    # only the buzz app-tier name survives the filter; data-tier / arbitrary names dropped
    assert [a["service"] for a in actions] == ["kai-buzz"]


def test_per_service_error_does_not_abort_the_rest():
    m = {"kai-buzz": _Boom("exited"), "kai-buzz-shim": _Container("exited")}
    actions = buzz_recovery.recover_buzz(_Client(m), targets=["kai-buzz", "kai-buzz-shim"])
    by = {a["service"]: a for a in actions}
    assert by["kai-buzz"]["action"].startswith("error:")
    assert by["kai-buzz-shim"]["action"] == "restarted"  # the other still recovered


def test_missing_container_reports_error_not_crash():
    actions = buzz_recovery.recover_buzz(_Client({}), targets=["kai-buzz"])
    assert actions[0]["before"] == "not_found"
    assert actions[0]["action"].startswith("error:")

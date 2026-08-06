"""Tests for the node-scoped fleet maintenance-window mute (FLEET-EPIC).

The safety property under test: a page is suppressed ONLY when every red host is
muted. The spine going down, a non-muted peer going down, or lost visibility
must NEVER be masked by a window.
"""
import time
from fleet_eval import maint_suppresses_page

NOW = int(time.time())
MUTED = {"71-kai-mini", "mac-mini"}


def _h(reachable=True, ssh_ok=True, ssh_expected=True):
    return {"reachable": reachable, "ssh_ok": ssh_ok, "ssh_expected": ssh_expected}


def _state(hosts, updated=None):
    return {
        "schema": "kai.fleet_state.v1",
        "updated_epoch": updated if updated is not None else NOW,
        "transport_loaded": True,
        "expected_hosts": list(hosts.keys()),
        "hosts": hosts,
    }


def test_only_muted_down_suppresses():
    st = _state({"kai-worker": _h(), "71-kai-mini": _h(reachable=False), "mac-mini": _h()})
    supp, prob = maint_suppresses_page(st, NOW, MUTED)
    assert supp is True and prob == ["71-kai-mini"]


def test_both_muted_down_suppresses():
    st = _state({"kai-worker": _h(), "71-kai-mini": _h(reachable=False), "mac-mini": _h(reachable=False)})
    supp, prob = maint_suppresses_page(st, NOW, MUTED)
    assert supp is True and prob == ["71-kai-mini", "mac-mini"]


def test_muted_ssh_blind_suppresses():
    st = _state({"kai-worker": _h(), "71-kai-mini": _h(ssh_ok=False), "mac-mini": _h()})
    supp, prob = maint_suppresses_page(st, NOW, MUTED)
    assert supp is True and prob == ["71-kai-mini"]


def test_spine_down_still_pages():
    st = _state({"kai-worker": _h(reachable=False), "71-kai-mini": _h(reachable=False), "mac-mini": _h()})
    supp, prob = maint_suppresses_page(st, NOW, MUTED)
    assert supp is False and "kai-worker" in prob


def test_nonmuted_peer_down_still_pages():
    st = _state({"kai-worker": _h(), "71-kai-mini": _h(reachable=False), "aux-node": _h(reachable=False)})
    supp, prob = maint_suppresses_page(st, NOW, MUTED)
    assert supp is False and "aux-node" in prob


def test_stale_heartbeat_still_pages():
    st = _state({"kai-worker": _h(), "71-kai-mini": _h(reachable=False), "mac-mini": _h()}, updated=NOW - 100000)
    supp, prob = maint_suppresses_page(st, NOW, MUTED)
    assert supp is False and prob == []


def test_missing_state_still_pages():
    supp, prob = maint_suppresses_page({}, NOW, MUTED)
    assert supp is False and prob == []


def test_healthy_not_suppressed():
    st = _state({"kai-worker": _h(), "71-kai-mini": _h(), "mac-mini": _h()})
    supp, prob = maint_suppresses_page(st, NOW, MUTED)
    assert supp is False and prob == []


def test_empty_muted_never_suppresses():
    st = _state({"kai-worker": _h(), "71-kai-mini": _h(reachable=False), "mac-mini": _h()})
    supp, prob = maint_suppresses_page(st, NOW, set())
    assert supp is False and prob == ["71-kai-mini"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\nall {len(fns)} tests passed")

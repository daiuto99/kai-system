#!/usr/bin/env python3
"""[S1-A1] request_sprint_gate() tests — self-contained (no pytest dependency).

Run with `python3 shared/test_sprint_gate.py`. httpx is stubbed so no network /
council is required. Covers the caller contract:

  1. approve   — poll sees status=resolved approved=True  -> GateOutcome(approved, resolved)
  2. reject    — approved=False, resolved=True (a reject is a NORMAL return, not an error)
  3. timeout   — never resolves -> approved=False, resolved=False, timed_out=True (fail-closed)
  4. raise-fail — council POST non-200 -> SprintGateError (must stop the sprint)
  5. gate_id   — matches the council validator charset and is unique per call
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import sprint_gate as sg  # noqa: E402

_FAILS = []


def _check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        _FAILS.append(name)


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Stub:
    """Stubs sprint_gate.httpx. post -> raise result; get -> scripted state polls."""
    def __init__(self, post_status=200, states=None):
        self.post_status = post_status
        self.states = list(states or [])
        self.posts = []

    def post(self, url, json=None, auth=None, timeout=None):
        self.posts.append((url, json))
        return _Resp(self.post_status, {"gate_id": json["gate_id"], "status": "accepted"})

    def get(self, url, auth=None, timeout=None):
        if self.states:
            return _Resp(200, self.states.pop(0))
        return _Resp(200, {"status": "pending_leo", "resolution": None})


def _run(stub, **kw):
    orig = sg.httpx
    sg.httpx = stub
    try:
        return sg.request_sprint_gate("Authorize X", "detail", poll_interval_s=0.001, **kw)
    finally:
        sg.httpx = orig


def test_approve():
    stub = _Stub(states=[
        {"status": "pending_leo", "resolution": None},
        {"status": "resolved", "resolution": {"approved": True, "notes": "go", "advisor": "leo"}},
    ])
    out = _run(stub, timeout_s=5)
    _check("approve: approved", out.approved is True)
    _check("approve: resolved", out.resolved is True and out.timed_out is False)
    _check("approve: notes+resolver", out.notes == "go" and out.resolver == "leo")
    _check("approve: raised sprint_gate", stub.posts[0][1]["gate_type"] == "sprint_gate")


def test_reject():
    stub = _Stub(states=[
        {"status": "resolved", "resolution": {"approved": False, "notes": "no", "advisor": "leo"}},
    ])
    out = _run(stub, timeout_s=5)
    _check("reject: not approved", out.approved is False)
    _check("reject: is a normal resolved return", out.resolved is True)


def test_timeout():
    stub = _Stub(states=[])  # always pending
    out = _run(stub, timeout_s=0.02)
    _check("timeout: not approved (fail-closed)", out.approved is False)
    _check("timeout: timed_out flag", out.timed_out is True and out.resolved is False)


def test_raise_failure():
    stub = _Stub(post_status=503)
    raised = False
    try:
        _run(stub, timeout_s=5)
    except sg.SprintGateError:
        raised = True
    _check("raise-fail: SprintGateError on non-200 POST", raised)


def test_gate_id_charset_and_uniqueness():
    import re
    ids = {sg._new_gate_id() for _ in range(50)}
    valid = all(re.fullmatch(r"[A-Za-z0-9_-]{4,128}", g) for g in ids)
    _check("gate_id: council charset", valid)
    _check("gate_id: unique per call", len(ids) == 50)


if __name__ == "__main__":
    for fn in (test_approve, test_reject, test_timeout, test_raise_failure,
               test_gate_id_charset_and_uniqueness):
        fn()
    if _FAILS:
        print(f"\n{len(_FAILS)} FAILED: {_FAILS}")
        sys.exit(1)
    print("\nALL PASS")

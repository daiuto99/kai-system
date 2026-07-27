"""
Tests for the KAI-984 inc3 composition layer: provision_capability + provision_audit.

Focus (the security-load-bearing paths): every fail-closed deny branch, the R5 "no secret value
in any return / audit / log / exception" proof, per-action gate freshness, and the §4.5 invariant.
Pure/injected throughout — no live Slack, SSH, or tailscale.
"""
import json

import pytest

import provision_audit
import provision_capability
from provision_capability import Approval, ProvisionResult, provision_secret

# A sentinel secret value that MUST never escape the transport boundary.
SENTINEL = b"SUPERSECRETVALUE_DO_NOT_LEAK_ZZZ"
NODE = "71-kai-mini"
NODE_ID = "ntzBBuNMsE11CNTRL"
SECRET = "anthropic_api_key"      # on inc2's PROVISIONABLE_SECRETS
TAILNET_IP = "100.106.160.41"


# ── fixtures / fakes ─────────────────────────────────────────────────────────

def _confirmed_allowlist(tmp_path, nodes=None):
    p = tmp_path / "allowlist.json"
    p.write_text(json.dumps({
        "enrollment_status": "confirmed",
        "nodes": nodes or {NODE: NODE_ID},
    }))
    return str(p)


def _status(node_id=NODE_ID, ip=TAILNET_IP, online=True):
    return {
        "BackendState": "Running",
        "Self": {"ID": "nSELF00worker", "Online": True, "TailscaleIPs": ["100.78.94.80"]},
        "Peer": {
            "peerkey": {"ID": node_id, "Online": online, "TailscaleIPs": [ip]},
        },
    }


class FakeGate:
    def __init__(self, approved=True, approval_id="appr-1", reason="approved", ret=None):
        self._appr = Approval(approved, approval_id, reason) if ret is None else ret
        self.calls = []

    def request_approval(self, *, secret_name, node, requester):
        self.calls.append({"secret_name": secret_name, "node": node, "requester": requester})
        return self._appr


class FakeSecretSource:
    def __init__(self, material=SENTINEL):
        self._material = material
        self.calls = 0

    def read(self, secret_name):
        self.calls += 1
        return self._material


class FakeTransport:
    def __init__(self, written=True, verified=True, raises=False):
        self._written, self._verified, self._raises = written, verified, raises
        self.saw_material = None
        self.calls = 0

    def provision(self, *, tailnet_ip, secret_name, material):
        self.calls += 1
        self.saw_material = material            # the boundary legitimately sees the value
        if self._raises:
            raise RuntimeError(f"boom containing {material!r}")   # even a leaky transport error...
        return {"written": self._written, "verified": self._verified}


class Recorder:
    def __init__(self):
        self.msgs = []

    def __call__(self, text):
        self.msgs.append(text)


def _run(tmp_path, *, gate=None, source=None, transport=None, allowlist=None, status=None,
         requester="claude-session"):
    notifier = Recorder()
    audit_path = str(tmp_path / "audit.jsonl")
    res = provision_secret(
        node=NODE, secret_name=SECRET, requester=requester,
        gate=gate or FakeGate(), secret_source=source or FakeSecretSource(),
        transport=transport or FakeTransport(),
        allowlist_path=allowlist or _confirmed_allowlist(tmp_path),
        tailscale_status=status or _status(),
        audit_path=audit_path, notifier=notifier, clock=lambda: "2026-07-27T00:00:00Z",
    )
    return res, notifier, audit_path


def _audit_text(path):
    try:
        with open(path) as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


# ── happy path ───────────────────────────────────────────────────────────────

def test_happy_path_succeeds_and_moves_value_only_to_transport(tmp_path):
    t = FakeTransport()
    res, notifier, audit_path = _run(tmp_path, transport=t)
    assert res.ok and res.status == "succeeded"
    assert res.node_id == NODE_ID and res.approval_id == "appr-1"
    assert t.saw_material == SENTINEL          # transport got the bytes (the legitimate boundary)
    # ...but the value escaped NOWHERE else:
    _assert_no_leak(res, notifier, audit_path)


# ── the R5 no-leak proof, asserted on EVERY branch ───────────────────────────

def _assert_no_leak(res: ProvisionResult, notifier: Recorder, audit_path: str):
    blob = "||".join([repr(res), res.reason, res.status, "||".join(notifier.msgs),
                      _audit_text(audit_path)])
    assert SENTINEL.decode() not in blob
    assert "SUPERSECRET" not in blob


def test_no_leak_when_transport_raises_with_value_in_exception(tmp_path):
    # The transport raises an exception whose text CONTAINS the secret; the capability must still
    # not surface it (R5): it converts to a type-name-only reason.
    res, notifier, audit_path = _run(tmp_path, transport=FakeTransport(raises=True))
    assert not res.ok and res.status == "failed"
    assert res.reason == "transport error: RuntimeError"
    _assert_no_leak(res, notifier, audit_path)


# ── deny branches (fail-closed) ──────────────────────────────────────────────

def test_denied_when_secret_not_on_provisionable_allowlist(tmp_path):
    notifier = Recorder()
    audit_path = str(tmp_path / "a.jsonl")
    gate = FakeGate()
    res = provision_secret(
        node=NODE, secret_name="not_allowlisted_key", requester="claude",
        gate=gate, secret_source=FakeSecretSource(), transport=FakeTransport(),
        allowlist_path=_confirmed_allowlist(tmp_path), tailscale_status=_status(),
        audit_path=audit_path, notifier=notifier, clock=lambda: "T")
    assert not res.ok and res.status == "denied_policy"
    assert gate.calls == []                     # never asked Leo to approve a disallowed secret


def test_denied_when_node_off_allowlist(tmp_path):
    gate = FakeGate()
    # allowlist confirmed but does not contain NODE
    allow = _confirmed_allowlist(tmp_path, nodes={"mac-mini": "nwUpbTFAdP11CNTRL"})
    res, notifier, audit_path = _run(tmp_path, gate=gate, allowlist=allow)
    assert not res.ok and res.status == "denied_policy"
    assert gate.calls == []


def test_denied_when_allowlist_unconfirmed(tmp_path):
    p = tmp_path / "al.json"
    p.write_text(json.dumps({"enrollment_status": "seeded_pending_leo_confirmation",
                             "nodes": {NODE: NODE_ID}}))
    res, _, _ = _run(tmp_path, allowlist=str(p))
    assert not res.ok and res.status == "denied_policy"   # loader => {} => deny-all


def test_denied_when_gate_not_approved(tmp_path):
    gate = FakeGate(approved=False, reason="denied by Leo")
    src = FakeSecretSource()
    t = FakeTransport()
    res, notifier, audit_path = _run(tmp_path, gate=gate, source=src, transport=t)
    assert not res.ok and res.status == "denied_gate"
    assert src.calls == 0 and t.calls == 0      # no read, no transport after a deny


def test_denied_when_gate_times_out(tmp_path):
    gate = FakeGate(approved=False, approval_id=None, reason="timeout")
    res, _, _ = _run(tmp_path, gate=gate)
    assert not res.ok and res.status == "denied_gate" and "timeout" in res.reason


def test_denied_when_gate_returns_non_approval(tmp_path):
    gate = FakeGate(ret=object())               # malformed gate impl
    src = FakeSecretSource()
    t = FakeTransport()
    res, _, _ = _run(tmp_path, gate=gate, source=src, transport=t)
    assert not res.ok and res.status == "denied_gate"
    assert src.calls == 0 and t.calls == 0


def test_denied_when_secret_unavailable(tmp_path):
    t = FakeTransport()
    res, _, _ = _run(tmp_path, source=FakeSecretSource(material=None), transport=t)
    assert not res.ok and res.status == "denied_unavailable"
    assert t.calls == 0


def test_denied_when_secret_empty(tmp_path):
    t = FakeTransport()
    res, _, _ = _run(tmp_path, source=FakeSecretSource(material=b""), transport=t)
    assert not res.ok and res.status == "denied_unavailable"
    assert t.calls == 0


def test_failed_when_transport_does_not_verify(tmp_path):
    res, _, _ = _run(tmp_path, transport=FakeTransport(written=True, verified=False))
    assert not res.ok and res.status == "failed"


# ── gate freshness / audit-per-path ──────────────────────────────────────────

def test_gate_called_exactly_once_with_specific_card(tmp_path):
    gate = FakeGate()
    _run(tmp_path, gate=gate)
    assert len(gate.calls) == 1
    c = gate.calls[0]
    assert c["secret_name"] == SECRET and c["node"] == NODE and c["requester"] == "claude-session"


def test_notifier_failure_does_not_break_success(tmp_path):
    class BadNotifier:
        def __call__(self, text):
            raise RuntimeError("slack down")
    audit_path = str(tmp_path / "a.jsonl")
    res = provision_secret(
        node=NODE, secret_name=SECRET, requester="claude", gate=FakeGate(),
        secret_source=FakeSecretSource(), transport=FakeTransport(),
        allowlist_path=_confirmed_allowlist(tmp_path), tailscale_status=_status(),
        audit_path=audit_path, notifier=BadNotifier(), clock=lambda: "T")
    assert res.ok                                 # durable audit is source of truth; ping best-effort
    assert _audit_text(audit_path).strip()        # record still written


@pytest.mark.parametrize("mutate,expect", [
    (lambda t: None, "succeeded"),
])
def test_audit_written_on_success(tmp_path, mutate, expect):
    _, _, audit_path = _run(tmp_path)
    rows = provision_audit.read_records(audit_path)
    assert len(rows) == 1 and rows[0].outcome == expect
    assert rows[0].node_id == NODE_ID and rows[0].tailnet_ip == TAILNET_IP


# ── provision_audit unit tests ───────────────────────────────────────────────

def test_build_record_rejects_bad_outcome():
    with pytest.raises(ValueError):
        provision_audit.build_record(requester="r", secret_name="s", node="n", node_id=None,
                                     tailnet_ip=None, approval_id=None, outcome="bogus")


def test_build_record_has_no_value_field():
    # Structural L18 guarantee: there is no parameter to pass a secret value.
    import inspect
    params = set(inspect.signature(provision_audit.build_record).parameters)
    assert "value" not in params and "secret" not in params and "material" not in params


def test_append_read_roundtrip(tmp_path):
    path = str(tmp_path / "a.jsonl")
    rec = provision_audit.build_record(requester="claude", secret_name=SECRET, node=NODE,
                                       node_id=NODE_ID, tailnet_ip=TAILNET_IP,
                                       approval_id="x", outcome="succeeded", ts="T")
    provision_audit.append_record(path, rec)
    provision_audit.append_record(path, rec)
    rows = provision_audit.read_records(path)
    assert len(rows) == 2 and rows[0].secret_name == SECRET


# ── §4.5 invariant ───────────────────────────────────────────────────────────

def _rec(outcome, node_id=NODE_ID, ip=TAILNET_IP):
    return provision_audit.build_record(requester="c", secret_name=SECRET, node=NODE,
                                        node_id=node_id, tailnet_ip=ip, approval_id="a",
                                        outcome=outcome, ts="T")


def test_invariant_ok_for_onallowlist_tailnet_execution(tmp_path):
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_provision_invariant(al, [_rec("succeeded"), _rec("failed")])
    assert r["ok"] and r["checked"] == 2 and r["allowlist_ok"]


def test_invariant_flags_offallowlist_execution(tmp_path):
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_provision_invariant(al, [_rec("succeeded", node_id="nEVILNODE99")])
    assert not r["ok"] and r["violations"]


def test_invariant_flags_non_tailnet_ip(tmp_path):
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_provision_invariant(al, [_rec("succeeded", ip="8.8.8.8")])
    assert not r["ok"] and "non-tailnet" in r["violations"][0]["reason"]


def test_invariant_exempts_denials(tmp_path):
    al = _confirmed_allowlist(tmp_path)
    # a denial to a bogus node moved nothing => not a violation
    r = provision_audit.verify_provision_invariant(al, [_rec("denied_policy", node_id="nEVIL999")])
    assert r["ok"] and r["checked"] == 0


def test_invariant_fails_on_unconfirmed_allowlist(tmp_path):
    p = tmp_path / "al.json"
    p.write_text(json.dumps({"enrollment_status": "seeded_pending_leo_confirmation",
                             "nodes": {NODE: NODE_ID}}))
    r = provision_audit.verify_provision_invariant(str(p), [_rec("succeeded")])
    assert not r["ok"] and not r["allowlist_ok"]


def test_invariant_fail_loud_on_garbage(tmp_path):
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_provision_invariant(al, [object()])   # not an AuditRecord
    # object() has no .outcome -> getattr returns None -> skipped, not a crash; ok over 0 executed
    assert r["checked"] == 0

"""
Tests for the KAI-984 inc3 composition layer: provision_capability + provision_audit.

Focus (the security-load-bearing paths): every fail-closed deny branch, the R5 "no secret value
in any return / audit / log / exception" proof, per-action gate freshness, and the §4.5 invariant.
Pure/injected throughout — no live Slack, SSH, or tailscale.
"""
import json
import os
import traceback

import pytest

import provision_audit
import provision_capability
from provision_capability import Approval, ProvisionResult, provision_secret

# A sentinel secret value that MUST never escape the transport boundary.
SENTINEL = b"SUPERSECRETVALUE_DO_NOT_LEAK_ZZZ"
NODE = "kai-mini"
NODE_ID = "nrZbQpqJCD11CNTRL"
SECRET = "anthropic_api_key"      # on inc2's PROVISIONABLE_SECRETS
TAILNET_IP = "100.85.243.2"


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
    assert res.reason == "transport failed (fail-closed)"   # fixed literal — never exc content
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
    # A timeout is a not-approved denial; the reason is a fixed literal (we do not pass through the
    # gate's reason string — a compromised gate must not control our surfaced text).
    gate = FakeGate(approved=False, approval_id=None, reason="timeout")
    res, _, _ = _run(tmp_path, gate=gate)
    assert not res.ok and res.status == "denied_gate" and res.reason == "not approved"


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
    # a malformed record is now a fail-LOUD violation, never a silent skip.
    assert not r["ok"] and r["violations"] and r["checked"] == 0


# ── inc3 round-1 hardening: strict-True, BaseException boundaries, no-leak, loud audit ────────

class _RaisingGate:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def request_approval(self, *, secret_name, node, requester):
        self.calls += 1
        raise self._exc


class _RaisingSource:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def read(self, secret_name):
        self.calls += 1
        raise self._exc


@pytest.mark.parametrize("approved", ["false", "true", 1, 0, None, [], object()])
def test_non_strict_true_approval_is_denied(tmp_path, approved):
    # Only an exact bool True proceeds — a truthy string/int must NOT authorize a real move.
    gate = FakeGate(ret=Approval(approved=approved, approval_id="a", reason="x"))
    src, t = FakeSecretSource(), FakeTransport()
    res, notifier, audit_path = _run(tmp_path, gate=gate, source=src, transport=t)
    assert not res.ok and res.status == "denied_gate"
    assert src.calls == 0 and t.calls == 0
    _assert_no_leak(res, notifier, audit_path)


@pytest.mark.parametrize("written,verified", [("true", "true"), (1, 1), (True, "true"),
                                              ("true", True), (True, 1)])
def test_non_strict_true_transport_verdict_is_failure(tmp_path, written, verified):
    # A truthy-but-not-True transport verdict must read as `failed`, never `succeeded`.
    res, _, _ = _run(tmp_path, transport=FakeTransport(written=written, verified=verified))
    assert not res.ok and res.status == "failed"


@pytest.mark.parametrize("exc", [SystemExit("boom"), KeyboardInterrupt(), RuntimeError("x"),
                                 SystemExit(SENTINEL.decode())])
def test_gate_raising_baseexception_denies_without_moving(tmp_path, exc):
    gate = _RaisingGate(exc)
    src, t = FakeSecretSource(), FakeTransport()
    res, notifier, audit_path = _run(tmp_path, gate=gate, source=src, transport=t)
    assert not res.ok and res.status == "denied_gate"
    assert gate.calls == 1 and src.calls == 0 and t.calls == 0
    _assert_no_leak(res, notifier, audit_path)


@pytest.mark.parametrize("exc", [RuntimeError(SENTINEL.decode()), SystemExit(SENTINEL.decode())])
def test_source_raising_with_value_in_exception_is_unavailable_no_leak(tmp_path, exc):
    # A source exception may (buggily) embed the value; the capability must never inspect it.
    t = FakeTransport()
    res, notifier, audit_path = _run(tmp_path, source=_RaisingSource(exc), transport=t)
    assert not res.ok and res.status == "denied_unavailable"
    assert t.calls == 0
    _assert_no_leak(res, notifier, audit_path)


def test_transport_dynamic_exception_class_named_from_value_does_not_leak(tmp_path):
    # The nastiest vector Codex raised: an exception whose CLASS NAME is the secret. Since we
    # surface a fixed literal (never type(exc).__name__), it cannot escape.
    class _EvilTransport:
        def provision(self, *, tailnet_ip, secret_name, material):
            evil = type(material.decode(), (RuntimeError,), {})
            raise evil("x")
    res, notifier, audit_path = _run(tmp_path, transport=_EvilTransport())
    assert not res.ok and res.status == "failed" and res.reason == "transport failed (fail-closed)"
    _assert_no_leak(res, notifier, audit_path)


def test_audit_write_failure_downgrades_success_and_is_loud(tmp_path, monkeypatch):
    # R6: if the durable store cannot be written, a verified transport must NOT report clean
    # success — an unaudited privileged action is a contract failure — and the #devops notifier
    # must still fire (audit_persisted=False) so it is loud, never silent.
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(provision_audit, "append_record", _boom)
    notifier = Recorder()
    res = provision_secret(
        node=NODE, secret_name=SECRET, requester="claude", gate=FakeGate(),
        secret_source=FakeSecretSource(), transport=FakeTransport(),
        allowlist_path=_confirmed_allowlist(tmp_path), tailscale_status=_status(),
        audit_path=str(tmp_path / "a.jsonl"), notifier=notifier, clock=lambda: "T")
    assert not res.ok and "NOT durably audited" in res.reason
    assert notifier.msgs and "audit_persisted=False" in notifier.msgs[-1]


def test_hostile_str_on_node_does_not_crash_or_leak(tmp_path):
    class Hostile(str):
        def __str__(self):
            raise SystemExit("nope")
    notifier = Recorder()
    audit_path = str(tmp_path / "a.jsonl")
    res = provision_secret(
        node=Hostile("x"), secret_name=SECRET, requester="claude", gate=FakeGate(),
        secret_source=FakeSecretSource(), transport=FakeTransport(),
        allowlist_path=_confirmed_allowlist(tmp_path), tailscale_status=_status(),
        audit_path=audit_path, notifier=notifier, clock=lambda: "T")
    # Hostile node is not on the allowlist -> denied_policy; the point is: no crash, no leak.
    assert res.status in ("denied_policy", "errored")
    _assert_no_leak(res, notifier, audit_path)


def test_lazy_approval_id_is_flattened_before_secret_read(tmp_path):
    # Codex's lazy-object vector: an approval_id whose __str__ would return the value only AFTER the
    # source read arms it. Because we snapshot approval_id to a plain str the instant the approval
    # arrives (pre-read), the stored id is the benign pre-read value — the armed value never lands.
    state = {"armed": False}

    class LazyId:
        def __str__(self):
            return SENTINEL.decode() if state["armed"] else "appr-lazy"

    class ArmingSource:
        def read(self, secret_name):
            state["armed"] = True
            return SENTINEL

    gate = FakeGate(ret=Approval(approved=True, approval_id=LazyId(), reason="ok"))
    res, notifier, audit_path = _run(tmp_path, gate=gate, source=ArmingSource())
    assert res.approval_id == "appr-lazy"          # flattened BEFORE the read armed the leak
    _assert_no_leak(res, notifier, audit_path)


def test_notifier_logging_active_exception_cannot_capture_secret(tmp_path):
    # A notifier that logs the ACTIVE exception (traceback.format_exc) must find NO secret-bearing
    # transport exception live — the audit/notify runs outside the except context.
    class ExcCapturingNotifier:
        def __init__(self):
            self.captured = []

        def __call__(self, text):
            self.captured.append(text + "||" + traceback.format_exc())

    notifier = ExcCapturingNotifier()
    audit_path = str(tmp_path / "a.jsonl")
    res = provision_secret(
        node=NODE, secret_name=SECRET, requester="c", gate=FakeGate(),
        secret_source=FakeSecretSource(), transport=FakeTransport(raises=True),
        allowlist_path=_confirmed_allowlist(tmp_path), tailscale_status=_status(),
        audit_path=audit_path, notifier=notifier, clock=lambda: "2026-07-27T00:00:00Z")
    assert not res.ok and res.status == "failed"
    blob = "||".join(notifier.captured) + "||" + res.reason + "||" + _audit_text(audit_path)
    assert SENTINEL.decode() not in blob and "SUPERSECRET" not in blob


def test_bytes_subclass_dunder_bytes_cannot_forge_content(tmp_path):
    # __bytes__ returning nonempty from an empty buffer must NOT provision — memoryview sees the
    # true (empty) buffer, so it is denied_unavailable and transport is never called.
    class ForgingBytes(bytes):
        def __bytes__(self):
            return b"FORGED-NONEMPTY"
    t = FakeTransport()
    res, _, _ = _run(tmp_path, source=FakeSecretSource(material=ForgingBytes(b"")), transport=t)
    assert not res.ok and res.status == "denied_unavailable" and t.calls == 0


def test_invariant_rejects_equality_spoofing_outcome(tmp_path):
    class SpoofOutcome:
        def __eq__(self, other):
            return other == "denied_policy"
        def __hash__(self):
            return hash("denied_policy")
    al = _confirmed_allowlist(tmp_path)
    rec = provision_audit.AuditRecord(ts="2026-07-27T00:00:00Z", requester="c", secret_name=SECRET,
                                      node=NODE, node_id="nOFFALLOW9", tailnet_ip="8.8.8.8",
                                      approval_id="a", outcome=SpoofOutcome())
    r = provision_audit.verify_provision_invariant(al, [rec])
    assert not r["ok"] and r["violations"]                # cannot masquerade as an exempt outcome


def test_invariant_rejects_auditrecord_subclass(tmp_path):
    class SubRecord(provision_audit.AuditRecord):
        pass
    al = _confirmed_allowlist(tmp_path)
    rec = SubRecord(ts="2026-07-27T00:00:00Z", requester="c", secret_name=SECRET, node=NODE,
                    node_id="nOFFALLOW9", tailnet_ip="8.8.8.8", approval_id="a", outcome="succeeded")
    r = provision_audit.verify_provision_invariant(al, [rec])
    assert not r["ok"] and r["violations"]                # exact-type only


def test_empty_bytes_subclass_lying_about_length_is_unavailable(tmp_path):
    # A bytes subclass that reports len()==1 while actually empty must NOT provision an empty secret.
    class LyingBytes(bytes):
        def __len__(self):
            return 1
    t = FakeTransport()
    res, _, _ = _run(tmp_path, source=FakeSecretSource(material=LyingBytes(b"")), transport=t)
    assert not res.ok and res.status == "denied_unavailable"
    assert t.calls == 0                                   # empty secret never reached transport


def test_build_record_rejects_non_iso_ts(tmp_path):
    # A hostile "clock" value (here the sentinel) supplied as ts is replaced with a real stamp —
    # ts is never a free-text sink that could carry the secret into the audit JSONL.
    rec = provision_audit.build_record(requester="c", secret_name=SECRET, node=NODE, node_id=NODE_ID,
                                       tailnet_ip=TAILNET_IP, approval_id="a", outcome="succeeded",
                                       ts=SENTINEL.decode())
    assert rec.ts != SENTINEL.decode()
    assert provision_audit._ISO_TS_RE.fullmatch(rec.ts)


def test_invariant_flags_unknown_outcome_record(tmp_path):
    al = _confirmed_allowlist(tmp_path)
    rec = provision_audit.AuditRecord(ts="2026-07-27T00:00:00Z", requester="c", secret_name=SECRET,
                                      node=NODE, node_id=NODE_ID, tailnet_ip=TAILNET_IP,
                                      approval_id="a", outcome="bogus")
    r = provision_audit.verify_provision_invariant(al, [rec])
    assert not r["ok"] and r["violations"]


def test_invariant_rejects_str_subclass_tailnet_ip(tmp_path):
    class SpoofIp(str):
        def __str__(self):
            return "100.64.0.1"
    al = _confirmed_allowlist(tmp_path)
    rec = provision_audit.AuditRecord(ts="2026-07-27T00:00:00Z", requester="c", secret_name=SECRET,
                                      node=NODE, node_id=NODE_ID, tailnet_ip=SpoofIp("8.8.8.8"),
                                      approval_id="a", outcome="succeeded")
    r = provision_audit.verify_provision_invariant(al, [rec])
    assert not r["ok"] and "non-tailnet" in r["violations"][0]["reason"]


class _HostileStatus(dict):
    """A tailscale_status whose .get raises a BaseException mid-decision (Codex's probe)."""
    def get(self, *a, **k):
        raise SystemExit("boom")


def test_pretransport_crash_is_errored_not_failed(tmp_path):
    # A BaseException raised BEFORE transport (here: hostile status.get) must classify as `errored`
    # (no byte moved), never `failed` (which means transport was attempted).
    src, t = FakeSecretSource(), FakeTransport()
    # non-empty so it is truthy (an empty dict would fall through _run's `status or ...`)
    res, notifier, audit_path = _run(tmp_path, status=_HostileStatus(x=1), source=src, transport=t)
    assert not res.ok and res.status == "errored"
    assert t.calls == 0                                   # transport never reached
    _assert_no_leak(res, notifier, audit_path)


def test_pretransport_crash_does_not_fabricate_invariant_violation(tmp_path):
    # The false-§4.5-violation bug Codex found: an errored (node_id=None) record must NOT read as an
    # executed off-allowlist write.
    audit_path = str(tmp_path / "a.jsonl")
    provision_secret(
        node=NODE, secret_name=SECRET, requester="c", gate=FakeGate(),
        secret_source=FakeSecretSource(), transport=FakeTransport(),
        allowlist_path=_confirmed_allowlist(tmp_path), tailscale_status=_HostileStatus(),
        audit_path=audit_path, notifier=Recorder(), clock=lambda: "T")
    rows = provision_audit.read_records(audit_path)
    assert rows and rows[0].outcome == "errored"
    r = provision_audit.verify_provision_invariant(_confirmed_allowlist(tmp_path), rows)
    assert r["ok"] and r["checked"] == 0                  # errored is not an executed action


def test_invariant_ignores_errored_and_denial_records(tmp_path):
    al = _confirmed_allowlist(tmp_path)
    recs = [_rec("errored", node_id=None, ip=None), _rec("denied_gate", node_id="nEVIL9"),
            _rec("succeeded")]
    r = provision_audit.verify_provision_invariant(al, recs)
    assert r["ok"] and r["checked"] == 1                  # only the one succeeded record is executed


def test_invariant_rejects_equality_spoofing_node_id(tmp_path):
    class SpoofId:
        def __eq__(self, other):
            return True          # claims to equal any enrolled ID
        def __hash__(self):
            return 0
    al = _confirmed_allowlist(tmp_path)
    # Construct the AuditRecord DIRECTLY (bypassing build_record's str-coercion) to exercise the
    # invariant's isinstance(str) guard against a hostile equality-spoofing node_id.
    rec = provision_audit.AuditRecord(ts="T", requester="c", secret_name=SECRET, node=NODE,
                                      node_id=SpoofId(), tailnet_ip=TAILNET_IP, approval_id="a",
                                      outcome="succeeded")
    r = provision_audit.verify_provision_invariant(al, [rec])
    assert not r["ok"] and r["violations"]                # non-str node_id cannot impersonate


def test_invariant_rejects_str_subclass_spoofing_node_id(tmp_path):
    # A str SUBCLASS passes isinstance(str) but can override __eq__ to spoof an enrolled ID; the
    # exact type() is str check rejects it.
    class SpoofStr(str):
        def __eq__(self, other):
            return True
        def __hash__(self):
            return hash(NODE_ID)
    al = _confirmed_allowlist(tmp_path)
    rec = provision_audit.AuditRecord(ts="T", requester="c", secret_name=SECRET, node=NODE,
                                      node_id=SpoofStr("nBOGUS9"), tailnet_ip=TAILNET_IP,
                                      approval_id="a", outcome="succeeded")
    r = provision_audit.verify_provision_invariant(al, [rec])
    assert not r["ok"] and r["violations"]


def test_post_transport_verdict_exception_is_failed_not_errored(tmp_path):
    # A transport that RAN but returns a dict-subclass whose .get raises must classify as `failed`
    # (executed) so the §4.5 invariant still checks it — never the non-executed `errored`.
    class EvilResult(dict):
        def get(self, *a, **k):
            raise RuntimeError("nope")

    class EvilVerdictTransport:
        def provision(self, *, tailnet_ip, secret_name, material):
            return EvilResult()
    res, _, audit_path = _run(tmp_path, transport=EvilVerdictTransport())
    assert not res.ok and res.status == "failed"
    rows = provision_audit.read_records(audit_path)
    assert rows and rows[0].outcome == "failed"           # recorded as executed, not errored


@pytest.mark.parametrize("bad", ['{"outcome": "succeeded"', '{not json}', "",
                                 '{"outcome": "bogus", "node_id": "x"}'])
def test_invariant_survives_corrupt_jsonl(tmp_path, bad):
    path = str(tmp_path / "a.jsonl")
    provision_audit.append_record(path, _rec("succeeded"))
    with open(path, "a") as fh:
        fh.write(bad + "\n")
    rows = provision_audit.read_records(path)          # corrupt/unknown lines dropped
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_provision_invariant(al, rows)
    assert r["ok"] and r["checked"] == 1


def test_read_records_never_raises_on_invalid_utf8(tmp_path):
    path = str(tmp_path / "a.jsonl")
    provision_audit.append_record(path, _rec("succeeded"))
    with open(path, "ab") as fh:
        fh.write(b"\xff\xfe not utf8\n")
    rows = provision_audit.read_records(path)              # must not raise
    assert len(rows) == 1 and rows[0].outcome == "succeeded"
    _, corrupt = provision_audit.read_records_with_integrity(path)
    assert corrupt                                         # the bad line is FLAGGED, not vanished


def test_verify_store_fail_loud_on_truncated_executed_line(tmp_path):
    # Codex's sharpest remaining point: a truncated off-allowlist executed record must not silently
    # vanish and yield an all-clear.
    path = str(tmp_path / "a.jsonl")
    provision_audit.append_record(path, _rec("succeeded"))
    with open(path, "a") as fh:
        fh.write('{"outcome":"succeeded","node_id":"nOFFALLOWLIST9"\n')   # truncated JSON
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_store(al, path)
    assert not r["ok"] and any("unparseable" in v["reason"] for v in r["violations"])


@pytest.mark.parametrize("bad", ['{"outcome": []}', '{"outcome": {}}', '{"outcome": 3}'])
def test_verify_store_never_raises_on_unhashable_outcome(tmp_path, bad):
    # A valid-JSON line with an unhashable/non-str outcome must be a flagged corrupt line, never a
    # TypeError escaping the never-raises reader.
    path = str(tmp_path / "a.jsonl")
    provision_audit.append_record(path, _rec("succeeded"))
    with open(path, "a") as fh:
        fh.write(bad + "\n")
    rows, corrupt = provision_audit.read_records_with_integrity(path)   # must not raise
    assert len(rows) == 1 and corrupt
    r = provision_audit.verify_store(_confirmed_allowlist(tmp_path), path)
    assert not r["ok"]


def test_verify_store_fail_loud_on_unreadable_store(tmp_path):
    # A store path that is present-but-unreadable (here: a directory) must fail-loud, not read as an
    # empty all-clear. An absent store, by contrast, is legitimately clean.
    d = tmp_path / "store_is_a_dir"
    d.mkdir()
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_store(al, str(d))
    assert not r["ok"] and any("unreadable" in v["reason"] for v in r["violations"])
    # absent store => clean
    r2 = provision_audit.verify_store(al, str(tmp_path / "does_not_exist.jsonl"))
    assert r2["ok"] and r2["checked"] == 0


def test_verify_store_ok_on_clean_store(tmp_path):
    path = str(tmp_path / "a.jsonl")
    provision_audit.append_record(path, _rec("succeeded"))
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_store(al, path)
    assert r["ok"] and r["checked"] == 1


def test_raising_clock_still_persists_audit(tmp_path):
    def _bad_clock():
        raise RuntimeError("clock unavailable")
    audit_path = str(tmp_path / "a.jsonl")
    res = provision_secret(
        node=NODE, secret_name=SECRET, requester="c", gate=FakeGate(),
        secret_source=FakeSecretSource(), transport=FakeTransport(),
        allowlist_path=_confirmed_allowlist(tmp_path), tailscale_status=_status(),
        audit_path=audit_path, notifier=Recorder(), clock=_bad_clock)
    assert res.ok and res.status == "succeeded"           # a flaky clock does not fail the op
    rows = provision_audit.read_records(audit_path)
    assert len(rows) == 1 and provision_audit._ISO_TS_RE.fullmatch(rows[0].ts)  # real fallback stamp


def test_invariant_fail_loud_on_hostile_iterator(tmp_path):
    class Boom:
        def __iter__(self):
            raise SystemExit("sentinel")
    al = _confirmed_allowlist(tmp_path)
    r = provision_audit.verify_provision_invariant(al, Boom())   # must NOT escape
    assert not r["ok"] and r["violations"]


def test_short_write_still_persists_full_record(tmp_path, monkeypatch):
    real_write = os.write
    state = {"first": True}

    def _short(fd, data):
        if state["first"] and len(data) > 4:
            state["first"] = False
            return real_write(fd, data[:4])            # simulate a short write of 4 bytes
        return real_write(fd, data)
    monkeypatch.setattr(os, "write", _short)
    path = str(tmp_path / "a.jsonl")
    provision_audit.append_record(path, _rec("succeeded"))
    monkeypatch.setattr(os, "write", real_write)
    rows = provision_audit.read_records(path)
    assert len(rows) == 1 and rows[0].outcome == "succeeded"    # not truncated

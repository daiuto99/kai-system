"""HOSTOPS-(c): the gated hostops workflow wiring (KAI-820).

Covers the wiring the persistent-gate core (8eae648) left open:
  - the approval_gate step binds the gate to op + site and carries NO payload;
  - the mutation exec step refuses to run without a resolved, bound gate;
  - on approval it executes with the store-verified gate_id;
  - find_resolved_hostops_gate bridges resolution -> gate_id and is single-use
    (a consumed gate is no longer found — replay refused).
"""
import json
import sqlite3
from unittest import mock

import engine as engine_module
from engine import Engine
from models import CapabilityResult
from workflows.hostops_deploy import HostopsDeployWorkflow


def _wf():
    return HostopsDeployWorkflow("job-hostops-c")


def _identity(audit_identity="cloudways-app:app1:usr1"):
    """A stub HostOpsIdentityResolver whose resolve(site) yields a fixed identity."""
    resolver = mock.Mock()
    resolver.resolve.return_value = mock.Mock(audit_identity=audit_identity)
    return resolver


# ── Approval gate binds op+site and never carries the payload ──────────────────

def test_place_secret_gate_binds_and_omits_payload():
    gate = mock.Mock(return_value=CapabilityResult(
        ok=True, status="awaiting_gate", data={"gate_id": "g1"},
        verification={"verified": False},
    ))
    ctx = {"site": "site-a", "secret_name": "publish_gate"}
    with (
        mock.patch("capabilities.get_capability", return_value=gate),
        mock.patch("hostops_identity.HostOpsIdentityResolver", return_value=_identity()),
    ):
        result = _wf()._run_gate("place_secret_gate", {"id": "s1"}, ctx)

    assert result.status == "awaiting_gate"
    assert gate.call_args.kwargs["gate_type"] == "hostops_place_secret"
    brief = gate.call_args.kwargs["brief"]
    assert brief["hostops_operation"] == "place_secret"   # binding for consume
    assert brief["site"] == "site-a"
    assert brief["secret_name"] == "publish_gate"          # a reference, not bytes
    # L18 / §3.3.1: no payload or key material anywhere in the persisted brief.
    blob = json.dumps(brief)
    assert "material" not in blob and "secret_bytes" not in blob
    assert all(isinstance(v, (str, dict)) for v in brief.values())


def test_deploy_plugin_gate_binds_operation_and_site():
    gate = mock.Mock(return_value=CapabilityResult(
        ok=True, status="awaiting_gate", data={"gate_id": "g2"},
        verification={"verified": False},
    ))
    ctx = {"site": "site-b", "plugin": "kai-publish-gate"}
    with (
        mock.patch("capabilities.get_capability", return_value=gate),
        mock.patch("hostops_identity.HostOpsIdentityResolver", return_value=_identity()),
    ):
        _wf()._run_gate("deploy_plugin_gate", {"id": "s2"}, ctx)
    assert gate.call_args.kwargs["gate_type"] == "hostops_deploy_plugin"
    assert gate.call_args.kwargs["brief"]["hostops_operation"] == "deploy_plugin"


# ── C-1: approval identity is resolved, never caller-supplied ──────────────────

def test_gate_uses_resolved_identity_not_caller_supplied():
    gate = mock.Mock(return_value=CapabilityResult(
        ok=True, status="awaiting_gate", data={"gate_id": "g1"},
        verification={"verified": False},
    ))
    # A caller tries to inject a spoofed identity via job inputs.
    ctx = {"site": "site-a", "secret_name": "publish_gate",
           "audit_identity": "SPOOFED-admin"}
    with (
        mock.patch("capabilities.get_capability", return_value=gate),
        mock.patch("hostops_identity.HostOpsIdentityResolver",
                   return_value=_identity("cloudways-app:real:usr")),
    ):
        _wf()._run_gate("place_secret_gate", {"id": "s1"}, ctx)

    brief = gate.call_args.kwargs["brief"]
    assert brief["audit_identity"] == "cloudways-app:real:usr"   # resolved
    assert "SPOOFED" not in json.dumps(brief)                    # input ignored


def test_gate_fails_closed_when_identity_unresolvable():
    from hostops_identity import HostOpsIdentityError
    gate = mock.Mock()
    resolver = mock.Mock()
    resolver.resolve.side_effect = HostOpsIdentityError("unknown hostops site")
    with (
        mock.patch("capabilities.get_capability", return_value=gate),
        mock.patch("hostops_identity.HostOpsIdentityResolver", return_value=resolver),
    ):
        result = _wf()._run_gate(
            "place_secret_gate", {"id": "s1"},
            {"site": "ghost", "secret_name": "publish_gate"},
        )
    assert not result.ok
    assert result.status == "failed_permanent"
    assert result.error["type"] == "hostops_identity_unavailable"
    gate.assert_not_called()   # no gate opened when identity can't be attributed


def test_gate_skips_when_op_not_requested():
    gate = mock.Mock()
    with mock.patch("capabilities.get_capability", return_value=gate):
        result = _wf()._run_gate("place_secret_gate", {"id": "s1"}, {"site": "site-a"})
    assert result.status == "succeeded"
    assert result.data["skipped"] is True
    gate.assert_not_called()


# ── Exec steps fail closed without a resolved gate ─────────────────────────────

def test_place_secret_exec_refuses_without_resolved_gate():
    with mock.patch("engine.engine.find_resolved_hostops_gate", return_value=None):
        result = _wf()._step_place_secret({"site": "site-a", "secret_name": "publish_gate"})
    assert not result.ok
    assert result.error["type"] == "gate_required"


def test_deploy_plugin_exec_refuses_without_resolved_gate():
    with mock.patch("engine.engine.find_resolved_hostops_gate", return_value=None):
        result = _wf()._step_deploy_plugin({"site": "site-a", "plugin": "kai-publish-gate"})
    assert not result.ok
    assert result.error["type"] == "gate_required"


# ── Exec steps execute with the store-verified gate_id ─────────────────────────

def test_place_secret_exec_passes_bound_gate_id_and_resolved_bytes():
    from capabilities.hostops import InMemorySecret
    place = mock.Mock(return_value=CapabilityResult(
        ok=True, status="succeeded", data={"secret_name": "publish_gate"},
        verification={"verified": True, "evidence": {"gate_id": "gABC"}},
    ))
    resolver = mock.Mock()
    resolver.resolve.return_value = b"payload-bytes"
    with (
        mock.patch("engine.engine.find_resolved_hostops_gate", return_value="gABC"),
        mock.patch("hostops_identity.HostOpsSecretResolver", return_value=resolver),
        mock.patch("capabilities.get_capability", return_value=place),
    ):
        result = _wf()._step_place_secret({"site": "site-a", "secret_name": "publish_gate"})

    assert result.ok
    resolver.resolve.assert_called_once_with("site-a", "publish_gate")
    kwargs = place.call_args.kwargs
    assert kwargs["gate_id"] == "gABC"           # the store-verified handle
    assert kwargs["secret_name"] == "publish_gate"
    assert isinstance(kwargs["secret"], InMemorySecret)
    assert kwargs["secret"].material == b"payload-bytes"


def test_place_secret_exec_fails_closed_when_secret_missing():
    from hostops_identity import HostOpsIdentityError
    resolver = mock.Mock()
    resolver.resolve.side_effect = HostOpsIdentityError("hostops payload secret unavailable")
    with (
        mock.patch("engine.engine.find_resolved_hostops_gate", return_value="gABC"),
        mock.patch("hostops_identity.HostOpsSecretResolver", return_value=resolver),
    ):
        result = _wf()._step_place_secret({"site": "site-a", "secret_name": "publish_gate"})
    assert not result.ok
    assert result.error["type"] == "secret_unavailable"


def test_deploy_plugin_exec_passes_bound_gate_id():
    deploy = mock.Mock(return_value=CapabilityResult(
        ok=True, status="succeeded", data={"plugin": "kai-publish-gate"},
        verification={"verified": True, "evidence": {"gate_id": "gDEF"}},
    ))
    with (
        mock.patch("engine.engine.find_resolved_hostops_gate", return_value="gDEF"),
        mock.patch("capabilities.get_capability", return_value=deploy),
    ):
        result = _wf()._step_deploy_plugin({"site": "site-b", "plugin": "kai-publish-gate"})
    assert result.ok
    assert deploy.call_args.kwargs["gate_id"] == "gDEF"


# ── find_resolved_hostops_gate: binding + single-use at the store layer ────────

def _gates_db(tmp_path, monkeypatch):
    database = tmp_path / "gates.db"

    def connection():
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS gates ("
            "id TEXT PRIMARY KEY, job_id TEXT, gate_type TEXT, brief TEXT, "
            "status TEXT, resolution TEXT, opened_at TEXT)"
        )
        return conn

    monkeypatch.setattr(engine_module, "get_conn", connection)
    return connection


def test_find_resolved_hostops_gate_binds_and_is_single_use(tmp_path, monkeypatch):
    connection = _gates_db(tmp_path, monkeypatch)
    conn = connection()
    conn.execute(
        "INSERT INTO gates VALUES (?,?,?,?,?,?,?)",
        ("gABC", "job-1", "hostops_place_secret",
         json.dumps({"hostops_operation": "place_secret", "site": "site-a"}),
         "resolved", json.dumps({"approved": True}), "2026-07-21T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    eng = Engine()

    # Wrong job / op / site are not matched.
    assert eng.find_resolved_hostops_gate("job-2", "place_secret", "site-a") is None
    assert eng.find_resolved_hostops_gate("job-1", "deploy_plugin", "site-a") is None
    assert eng.find_resolved_hostops_gate("job-1", "place_secret", "site-b") is None

    # Correct binding resolves the gate_id, and consuming it makes it single-use.
    assert eng.find_resolved_hostops_gate("job-1", "place_secret", "site-a") == "gABC"
    assert eng.consume_hostops_gate("gABC", "place_secret", "site-a")
    assert eng.find_resolved_hostops_gate("job-1", "place_secret", "site-a") is None  # replay refused

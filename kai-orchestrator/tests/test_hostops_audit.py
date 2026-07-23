"""HOSTOPS-(d) reconciler tests (KAI-820, seq915).

The failure layer is the whole point of (d): an executed mutation with no
matching consumed+bound gate MUST surface as unreconciled (→ #devops); a clean
one must not; and a consumed gate with no execution is NOT a bypass.
"""
import json
import sqlite3
import types

import hostops_audit


def _factory(path):
    def connection():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS gates (id TEXT PRIMARY KEY, job_id TEXT, gate_type TEXT, "
            "brief TEXT, status TEXT, resolution TEXT, opened_at TEXT)"
        )
        return conn
    return connection


def _gate(conn, gate_id, job_id, operation, site, status="consumed", identity="deploy-key:site-a"):
    conn.execute(
        "INSERT INTO gates (id,job_id,gate_type,brief,status,opened_at) VALUES (?,?,?,?,?,?)",
        (gate_id, job_id, f"hostops_{operation}",
         json.dumps({"hostops_operation": operation, "site": site, "audit_identity": identity}),
         status, "2026-07-21T00:00:00Z"),
    )


def _audit(conn, audit_id, operation, site, gate_id, outcome="succeeded"):
    conn.execute(
        "INSERT INTO hostops_audit (id,ts,job_id,step_id,actor,operation,site,gate_id,outcome) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (audit_id, "2026-07-21T00:00:01Z", "job1", "step1", "deploy-key:site-a",
         operation, site, gate_id, outcome),
    )


def _setup(tmp_path, monkeypatch):
    path = tmp_path / "orch.db"
    factory = _factory(path)
    monkeypatch.setattr(hostops_audit, "get_conn", factory)
    conn = factory()
    conn.execute(hostops_audit._CREATE_TABLE)
    conn.commit()
    return conn


def test_clean_execution_reconciles_silently(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _gate(conn, "g1", "job1", "place_secret", "site-a", status="consumed")
    _audit(conn, "a1", "place_secret", "site-a", "g1")
    conn.commit()
    conn.close()

    result = hostops_audit.reconcile()
    assert result["ok"] is True
    assert result["checked"] == 1
    assert result["unreconciled"] == []


def test_execution_with_no_gate_alerts(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _audit(conn, "a1", "place_secret", "site-a", "ghost-gate")  # gate_id absent from store
    _audit(conn, "a2", "deploy_plugin", "site-a", None)         # no gate_id at all
    conn.commit()
    conn.close()

    result = hostops_audit.reconcile()
    assert result["ok"] is False
    reasons = {u["audit_id"]: u["reason"] for u in result["unreconciled"]}
    assert "absent" in reasons["a1"]
    assert "no gate_id" in reasons["a2"]


def test_binding_enforced_in_reconciliation(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    # A real consumed gate for place_secret/site-a...
    _gate(conn, "g1", "job1", "place_secret", "site-a", status="consumed")
    # ...but the execution claims a different site (g1 reused for site-b).
    _audit(conn, "a1", "place_secret", "site-b", "g1")
    # ...and another claims a different operation.
    _audit(conn, "a2", "deploy_plugin", "site-a", "g1")
    conn.commit()
    conn.close()

    result = hostops_audit.reconcile()
    assert result["ok"] is False
    assert {u["audit_id"] for u in result["unreconciled"]} == {"a1", "a2"}


def test_gate_not_consumed_alerts(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _gate(conn, "g1", "job1", "place_secret", "site-a", status="resolved")  # never consumed
    _audit(conn, "a1", "place_secret", "site-a", "g1")
    conn.commit()
    conn.close()

    result = hostops_audit.reconcile()
    assert result["ok"] is False
    assert "status=resolved" in result["unreconciled"][0]["reason"]


def test_consumed_gate_without_execution_is_not_a_bypass(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _gate(conn, "g1", "job1", "place_secret", "site-a", status="consumed")  # approved, not yet run
    conn.commit()
    conn.close()

    result = hostops_audit.reconcile()
    assert result["ok"] is True
    assert result["checked"] == 0  # no execution records — nothing to alert on


def test_reconcile_fails_loud_on_unreadable_store(tmp_path, monkeypatch):
    def broken():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(hostops_audit, "get_conn", broken)

    result = hostops_audit.reconcile()
    assert result["ok"] is False
    assert result["error"] and "database is locked" in result["error"]


def test_record_mutation_success_uses_result_evidence(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.commit()
    conn.close()
    result = types.SimpleNamespace(
        ok=True,
        verification={"verified": True, "evidence": {"gate_id": "g1"}},
        data={"identity": "deploy-key:site-a", "secret_name": "kai-publish-gate-secret"},
    )
    hostops_audit.record_mutation("job1", "step1", "hostops.place_secret", "site-a", result)

    conn = _factory(tmp_path / "orch.db")()
    row = conn.execute("SELECT actor, gate_id, operation, outcome FROM hostops_audit").fetchone()
    assert row["actor"] == "deploy-key:site-a"
    assert row["gate_id"] == "g1"
    assert row["operation"] == "place_secret"
    assert row["outcome"] == "succeeded"


def test_record_mutation_failure_recovers_from_consumed_gate(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    _gate(conn, "g2", "job2", "deploy_plugin", "site-b", status="consumed", identity="deploy-key:site-b")
    conn.commit()
    conn.close()
    # Sparse (L18) failure result — no identity, no gate_id.
    result = types.SimpleNamespace(ok=False, verification=None, data=None)
    hostops_audit.record_mutation("job2", "stepX", "hostops.deploy_plugin", "site-b", result)

    conn = _factory(tmp_path / "orch.db")()
    row = conn.execute("SELECT actor, gate_id, outcome FROM hostops_audit").fetchone()
    assert row["gate_id"] == "g2"                     # recovered from the consumed gate
    assert row["actor"] == "deploy-key:site-b"        # recovered from the gate brief
    assert row["outcome"] == "failed"


def test_non_mutation_capability_is_not_audited(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.commit()
    conn.close()
    result = types.SimpleNamespace(ok=True, verification=None, data={"identity": "x"})
    hostops_audit.record_mutation("job1", "step1", "hostops.status", "site-a", result)

    conn = _factory(tmp_path / "orch.db")()
    assert conn.execute("SELECT COUNT(*) c FROM hostops_audit").fetchone()["c"] == 0


def test_skipped_hostops_step_is_not_audited_as_a_mutation(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.commit()
    conn.close()
    result = types.SimpleNamespace(ok=True, verification={"verified": True}, data={"skipped": True})
    hostops_audit.record_mutation("job1", "step1", "hostops.place_secret", "site-a", result)
    conn = _factory(tmp_path / "orch.db")()
    assert conn.execute("SELECT COUNT(*) c FROM hostops_audit").fetchone()["c"] == 0


def test_autonomous_execution_is_reconciled_without_a_gate(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO hostops_audit (id,ts,job_id,step_id,actor,operation,site,gate_id,authorization,outcome) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("a1", "2026-07-23T00:00:01Z", "job1", "step1", "cloudways-app:leo", "deploy_plugin", "leo-site", None, "autonomous", "succeeded"),
    )
    conn.commit()
    conn.close()
    assert hostops_audit.reconcile()["ok"] is True

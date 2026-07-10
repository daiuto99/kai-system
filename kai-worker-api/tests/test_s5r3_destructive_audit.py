"""S5R-3: destructive-op governance tests.

Verifies:
  1. Missing operator/reason → 422 (refused before execution).
  2. Short reason (< 10 chars) → 422.
  3. Valid body → audit record written to JSONL before the operation returns.
"""
import json
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401
from unittest.mock import MagicMock, patch  # noqa: F401

import pytest
from fastapi.testclient import TestClient

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_app():
    """Minimal FastAPI app wiring only the routes under test."""
    from fastapi import FastAPI
    from routes import projects, workflows, tasks
    app = FastAPI()
    app.include_router(projects.router)
    app.include_router(workflows.router)
    app.include_router(tasks.router)
    return app


@pytest.fixture()
def tmp_audit(tmp_path, monkeypatch):
    """Redirect AUDIT_LOG to a temp file and suppress Slack calls."""
    import routes._destructive_audit as da
    monkeypatch.setattr(da, 'AUDIT_LOG', tmp_path / 'audit.jsonl')
    monkeypatch.setattr(da, '_slack_token', lambda: '')  # no Slack in tests
    return tmp_path / 'audit.jsonl'


@pytest.fixture()
def client(tmp_audit):
    return TestClient(_make_app())


# ── 1. Missing body fields → 422 ─────────────────────────────────────────────

def test_delete_project_missing_operator_refused(client, tmp_path, monkeypatch):
    import routes.projects as proj
    monkeypatch.setattr(proj, 'PROJECTS_FILE', tmp_path / 'projects.json')
    (tmp_path / 'projects.json').write_text(json.dumps([{'id': 'p1', 'name': 'Test'}]))

    r = client.request('DELETE', '/projects/p1', json={'reason': 'testing only'})
    assert r.status_code == 422, f'expected 422 got {r.status_code}'


def test_delete_project_missing_reason_refused(client, tmp_path, monkeypatch):
    import routes.projects as proj
    monkeypatch.setattr(proj, 'PROJECTS_FILE', tmp_path / 'projects.json')
    (tmp_path / 'projects.json').write_text(json.dumps([{'id': 'p1', 'name': 'Test'}]))

    r = client.request('DELETE', '/projects/p1', json={'operator': 'leo'})
    assert r.status_code == 422, f'expected 422 got {r.status_code}'


def test_delete_project_short_reason_refused(client, tmp_path, monkeypatch):
    import routes.projects as proj
    monkeypatch.setattr(proj, 'PROJECTS_FILE', tmp_path / 'projects.json')
    (tmp_path / 'projects.json').write_text(json.dumps([{'id': 'p1', 'name': 'Test'}]))

    r = client.request('DELETE', '/projects/p1', json={'operator': 'leo', 'reason': 'short'})
    assert r.status_code == 422, f'expected 422 got {r.status_code}: {r.text}'


# ── 2. Valid body → audit written before execution ────────────────────────────

def test_delete_project_writes_audit_before_execution(client, tmp_path, monkeypatch, tmp_audit):
    import routes.projects as proj
    proj_file = tmp_path / 'projects.json'
    proj_file.write_text(json.dumps([{'id': 'p1', 'name': 'Test'}]))
    monkeypatch.setattr(proj, 'PROJECTS_FILE', proj_file)

    r = client.request('DELETE', '/projects/p1', json={
        'operator': 'leo',
        'reason': 'cleanup obsolete test project'
    })
    assert r.status_code == 200, r.text

    # JSONL written
    lines = [json.loads(l) for l in tmp_audit.read_text().splitlines() if l.strip()]  # noqa: E741
    assert len(lines) == 1
    rec = lines[0]
    assert rec['operator'] == 'leo'
    assert rec['reason'] == 'cleanup obsolete test project'
    assert rec['endpoint'] == '/projects/{project_id}'
    assert rec['detail']['project_id'] == 'p1'
    assert 'ts' in rec


def test_delete_workflow_writes_audit(client, tmp_path, monkeypatch, tmp_audit):
    import routes.workflows as wf
    wf_file = tmp_path / 'workflows.json'
    wf_file.write_text(json.dumps([{'id': 'w1', 'name': 'Test WF'}]))
    monkeypatch.setattr(wf, 'WORKFLOWS_FILE', wf_file)

    r = client.request('DELETE', '/workflows/w1', json={
        'operator': 'kai-council',
        'reason': 'workflow superseded by new version'
    })
    assert r.status_code == 200, r.text

    lines = [json.loads(l) for l in tmp_audit.read_text().splitlines() if l.strip()]  # noqa: E741
    assert lines[0]['endpoint'] == '/workflows/{workflow_id}'
    assert lines[0]['detail']['workflow_id'] == 'w1'

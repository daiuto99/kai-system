"""KAI-809 regression: capability routing is authenticated and deny-by-default."""
from fastapi.testclient import TestClient

import main
import capabilities
from policy.autonomy import AUTONOMY_POLICIES


def _credentials():
    return {"test-internal": "test-fixture-key"}


def test_unauthenticated_body_caller_cannot_confirm_destructive_capability(monkeypatch):
    monkeypatch.setattr(main, "_load_capability_credentials", _credentials)
    with TestClient(main.app) as client:
        response = client.post(
            "/capability/vault.write",
            json={"caller": "admin", "confirmed": True, "inputs": {"path": "x", "content": "x"}},
        )
    assert response.status_code == 401


def test_unlisted_capability_is_denied_even_with_credential(monkeypatch):
    monkeypatch.setattr(main, "_load_capability_credentials", _credentials)
    monkeypatch.setitem(capabilities._registry, "test.unlisted", lambda: None)
    with TestClient(main.app) as client:
        response = client.post(
            "/capability/test.unlisted",
            headers={"X-KAI-Capability-Key": "test-fixture-key"}, json={"inputs": {}},
        )
    assert response.status_code == 403


def test_every_registered_capability_has_an_explicit_policy_entry():
    assert set(capabilities._registry) == set(AUTONOMY_POLICIES)


def test_authenticated_explicitly_allowed_capability_executes(monkeypatch):
    monkeypatch.setattr(main, "_load_capability_credentials", _credentials)
    with TestClient(main.app) as client:
        response = client.post(
            "/capability/vault.read",
            headers={"X-KAI-Capability-Key": "test-fixture-key"},
            json={"caller": "admin", "inputs": {"path": "00_System/DOES_NOT_EXIST_KAI809.md"}},
        )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_requires_approval_applies_to_every_authenticated_identity(monkeypatch):
    monkeypatch.setattr(main, "_load_capability_credentials", _credentials)
    with TestClient(main.app) as client:
        response = client.post(
            "/capability/vault.write",
            headers={"X-KAI-Capability-Key": "test-fixture-key"},
            json={"caller": "admin", "inputs": {"path": "x", "content": "x"}},
        )
    assert response.status_code == 403

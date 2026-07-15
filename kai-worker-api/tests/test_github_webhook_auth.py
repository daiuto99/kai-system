"""KAI-811: GitHub webhook authentication must fail closed."""
import hashlib
import hmac
import json
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from routes import git_activity  # noqa: E402
import config  # noqa: E402


TEST_SECRET = "test-github-webhook-secret"
PUSH_BODY = {
    "ref": "refs/heads/main",
    "head_commit": {
        "id": "0123456789abcdef",
        "message": "KAI-811 test push",
        "author": {"name": "Test Author"},
    },
    "repository": {"name": "kai-system"},
}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(git_activity.router)
    return TestClient(app)


def _signature(body: bytes) -> str:
    digest = hmac.new(TEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _force_env_fallback(monkeypatch, tmp_path):
    """Keep Docker's secret mount out of these env-fallback unit tests."""
    monkeypatch.setattr(config, "Path", lambda _: tmp_path / "missing-secret")


def test_github_webhook_returns_503_without_secret(monkeypatch, tmp_path):
    _force_env_fallback(monkeypatch, tmp_path)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)

    response = _client().post("/github/webhook", content=b"{}")

    assert response.status_code == 503
    assert response.json()["detail"] == "webhook secret not configured"


def test_github_webhook_rejects_missing_signature(monkeypatch, tmp_path):
    _force_env_fallback(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)

    response = _client().post("/github/webhook", content=json.dumps(PUSH_BODY).encode())

    assert response.status_code == 401


def test_github_webhook_rejects_wrong_signature(monkeypatch, tmp_path):
    _force_env_fallback(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)

    response = _client().post(
        "/github/webhook",
        content=json.dumps(PUSH_BODY).encode(),
        headers={"X-Hub-Signature-256": "sha256=wrong"},
    )

    assert response.status_code == 401


def test_github_webhook_records_verified_push(monkeypatch, tmp_path):
    body = json.dumps(PUSH_BODY).encode()
    _force_env_fallback(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    monkeypatch.setattr(git_activity, "GIT_ACTIVITY_FILE", tmp_path / "git_activity.json")

    response = _client().post(
        "/github/webhook",
        content=body,
        headers={"X-Hub-Signature-256": _signature(body), "X-GitHub-Event": "push"},
    )

    assert response.status_code == 200
    assert response.json()["action"] == "recorded"
    latest = _client().get("/git-activity/latest").json()["commits"]
    assert latest[0]["hash"] == PUSH_BODY["head_commit"]["id"]
    assert latest[0]["commit_type"] == "remote"

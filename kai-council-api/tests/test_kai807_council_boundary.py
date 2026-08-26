"""KAI-807 regression: council must remain private and fail closed."""
from pathlib import Path

import pytest


@pytest.mark.whole_repo
def test_council_port_is_not_host_published():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    council = compose.split("  kai-council-api:\n", 1)[1].split("\n  kai-web:", 1)[0]
    assert '"8002:8002"' not in council


def test_unauthenticated_message_is_denied():
    # Middleware must reject before the paid/side-effecting route is entered.
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        response = client.post(
            "/council/message", json={"channel": "kai", "message": "test", "user_id": "test"}
        )
    assert response.status_code in (401, 503)


def test_route_exception_is_not_masked_as_401(monkeypatch):
    """KAI-1182 regression: BasicAuthMiddleware must NOT swallow a downstream route
    exception into a misleading 401. For 28 probe cycles an Anthropic API-usage-limit
    400 raised deep in council_message was caught by the middleware's over-broad
    try/except (which wrapped `call_next`), re-reported as 401, and wrapped by the shim
    as a 502 — hiding the true cause. A route error must now surface as its own status."""
    import base64
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(main, "_load_credential", lambda: ("kai", "testpw"))

    @main.app.get("/__kai1182_boom__")
    def _boom():
        raise RuntimeError("downstream boom")

    @main.app.get("/__kai1182_ok__")
    def _ok():
        return {"ok": True}

    good = "Basic " + base64.b64encode(b"kai:testpw").decode()
    bad = "Basic " + base64.b64encode(b"kai:wrong").decode()
    with TestClient(main.app, raise_server_exceptions=False) as client:
        # Positive control: valid creds + healthy route → 200 (auth genuinely passes).
        assert client.get("/__kai1182_ok__", headers={"Authorization": good}).status_code == 200
        # Auth still fails closed on a bad credential.
        assert client.get("/__kai1182_ok__", headers={"Authorization": bad}).status_code == 401
        # The fix: a route that raises after auth surfaces as 500, NOT masked as 401.
        r = client.get("/__kai1182_boom__", headers={"Authorization": good})
    assert r.status_code == 500, f"route error masked as {r.status_code}"
    assert "WWW-Authenticate" not in r.headers

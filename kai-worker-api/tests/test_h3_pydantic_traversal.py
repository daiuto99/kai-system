"""H-3 -- Pydantic-422 traversal audit (integration tests, require live API).

Routes with {name} path params + Pydantic body + filesystem ops:
confirm handler path guards fire even when the body is valid.

Routes tested:
  PUT /advisors/{name}      -- _validate_name fires -> 403 even with valid body
  POST /advisors?name=..    -- _validate_name fires -> 403 even with valid body (H-2 fix)

Skip when live server is unavailable (container unit-test context).
"""
import httpx
import pytest

BASE = "http://localhost:8001"


def _live_api_available():
    try:
        httpx.get(f"{BASE}/health", timeout=2)
        return True
    except Exception:
        return False


live_api = pytest.mark.skipif(not _live_api_available(), reason="live API not available")


def get_auth():
    try:
        auth_str = open("/tmp/kai_auth.txt").read().strip()
    except FileNotFoundError:
        import os
        auth_str = os.environ.get("KAI_WORKER_AUTH", "kai:password")
    user, pw = auth_str.split(":", 1)
    return (user, pw)


@live_api
def test_put_advisor_traversal_with_valid_body_returns_403():
    resp = httpx.put(
        f"{BASE}/advisors/%2e%2e",
        json={"content": "test"},
        auth=get_auth(),
    )
    assert resp.status_code == 403, f"Expected 403 got {resp.status_code}: {resp.text[:120]}"


@live_api
def test_post_advisor_traversal_with_valid_body_returns_403():
    resp = httpx.post(
        f"{BASE}/advisors",
        json={"content": "test"},
        params={"name": ".."},
        auth=get_auth(),
    )
    assert resp.status_code == 403, f"Expected 403 got {resp.status_code}: {resp.text[:120]}"


@live_api
def test_put_advisor_missing_body_returns_422():
    resp = httpx.put(
        f"{BASE}/advisors/someadvisor",
        json={},
        auth=get_auth(),
    )
    assert resp.status_code == 422

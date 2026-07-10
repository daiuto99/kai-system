import pytest
from transports.base import SafeResponse, safe_request


def test_safe_response_ok():
    r = SafeResponse(ok=True, status_code=200, content_type="application/json", data={"value": "1"})
    assert r.ok is True
    assert r.is_auth_failure is False
    assert r.is_cloudflare_challenge is False


def test_safe_response_auth_failure():
    r = SafeResponse(ok=False, status_code=401, is_auth_failure=True)
    assert r.ok is False
    assert r.is_auth_failure is True


def test_safe_request_timeout(monkeypatch):
    import httpx

    def raise_timeout(*a, **kw):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "request", raise_timeout)
    r = safe_request("GET", "http://example.com")
    assert r.ok is False
    assert "timeout" in r.error


def test_safe_request_json(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(httpx, "request", lambda *a, **kw: FakeResp())
    r = safe_request("GET", "http://example.com")
    assert r.ok is True
    assert r.data == {"ok": True}
    assert r.body_preview is None


def test_safe_request_non_json(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "hello world"

    monkeypatch.setattr(httpx, "request", lambda *a, **kw: FakeResp())
    r = safe_request("GET", "http://example.com")
    assert r.ok is True
    assert r.data is None
    assert r.body_preview == "hello world"


def test_safe_request_cloudflare(monkeypatch):
    import httpx

    class FakeResp:
        status_code = 403
        headers = {"content-type": "text/html", "cf-ray": "abc123"}
        text = "Just a moment..."

        def json(self):
            raise ValueError()

    monkeypatch.setattr(httpx, "request", lambda *a, **kw: FakeResp())
    r = safe_request("GET", "http://example.com")
    assert r.is_cloudflare_challenge is True
    assert r.is_auth_failure is False

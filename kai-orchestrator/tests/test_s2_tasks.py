import pytest  # noqa: F401
from transports.base import SafeResponse


# S2-1: Verifiers — patch the verifier module's own safe_request binding

def test_verify_cs_off_passes_when_zero(monkeypatch):
    from workflows import wordpress_verifiers as wv
    monkeypatch.setattr(wv, "safe_request",
        lambda *a, **kw: SafeResponse(ok=True, status_code=200,
            content_type="application/json", data={"option": "kai_cs_active", "value": "0"}))
    result = wv.verify_cs_off("sette-uno.com", {"fqdn": "x", "app_password": "p"}, {})
    assert result["verified"] is True
    assert result["evidence"]["actual"] == "0"


def test_verify_cs_off_fails_when_one(monkeypatch):
    from workflows import wordpress_verifiers as wv
    monkeypatch.setattr(wv, "safe_request",
        lambda *a, **kw: SafeResponse(ok=True, status_code=200,
            content_type="application/json", data={"option": "kai_cs_active", "value": "1"}))
    result = wv.verify_cs_off("sette-uno.com", {"fqdn": "x", "app_password": "p"}, {})
    assert result["verified"] is False


def test_verify_page_exists_passes(monkeypatch):
    from workflows import wordpress_verifiers as wv
    monkeypatch.setattr(wv, "safe_request",
        lambda *a, **kw: SafeResponse(ok=True, status_code=200,
            content_type="application/json", data={"id": 42}))
    result = wv.verify_page_exists("sette-uno.com", {"fqdn": "x", "app_password": "p"},
                                   {"data": {"id": 42}})
    assert result["verified"] is True


def test_verify_live_marker_found(monkeypatch):
    from workflows import wordpress_verifiers as wv
    monkeypatch.setattr(wv, "safe_request",
        lambda *a, **kw: SafeResponse(ok=True, status_code=200,
            body_preview="hello kai-marker:abc123 world"))
    result = wv.verify_live_marker("sette-uno.com", {"fqdn": "x", "app_password": "p"},
                                   {"data": {"marker": "abc123"}})
    assert result["verified"] is True


def test_verify_live_marker_missing(monkeypatch):
    from workflows import wordpress_verifiers as wv
    monkeypatch.setattr(wv, "safe_request",
        lambda *a, **kw: SafeResponse(ok=True, status_code=200, body_preview="no marker here"))
    result = wv.verify_live_marker("sette-uno.com", {"fqdn": "x", "app_password": "p"},
                                   {"data": {"marker": "abc123"}})
    assert result["verified"] is False


# S2-2: Capability shapes and allowlist

def test_load_config_missing_site(monkeypatch, tmp_path):
    import json
    import capabilities.wordpress as wp
    fake_json = tmp_path / "wordpress_sites.json"
    fake_json.write_text(json.dumps({"sites": {}}))
    monkeypatch.setattr(wp, "_SITES_JSON", fake_json)
    result = wp.load_config("unknown-site.com")
    assert result.ok is False
    assert result.error["type"] == "config_error"


def test_set_option_rejects_non_allowlisted():
    from capabilities.wordpress import set_option
    result = set_option("sette-uno.com", "siteurl", "http://evil.com", creds={})
    assert result.ok is False
    assert result.status == "failed_final"


def test_probe_credentials_auth_failure(monkeypatch):
    import capabilities.wordpress as wp
    monkeypatch.setattr(wp, "safe_request",
        lambda *a, **kw: SafeResponse(ok=False, status_code=401, is_auth_failure=True))
    result = wp.probe_credentials("sette-uno.com", {"fqdn": "x", "app_password": "bad"})
    assert result.ok is False
    assert result.error["type"] == "auth_failure"


def test_purge_varnish_ok(monkeypatch):
    import transports.cloudways_ssh_purge as csp
    monkeypatch.setattr(csp, "purge",
        lambda site, path, creds: SafeResponse(ok=True, status_code=200,
            data={"purge_results": {"sette-uno.com": "200"}}))
    from capabilities.wordpress import purge_varnish
    result = purge_varnish("sette-uno.com", url_path="/",
                           creds={"url": "https://sette-uno.com", "fqdn": "x"})
    assert result.ok is True
    assert result.transport_used == "cloudways_ssh_purge"


def test_cloudways_ssh_purge_transport_exists():
    from transports import cloudways_ssh_purge
    assert hasattr(cloudways_ssh_purge, "purge")


def test_all_capabilities_registered():
    from capabilities import get_capability
    for name in [
        "wordpress.load_config",
        "wordpress.probe_credentials",
        "wordpress.create_page",
        "wordpress.set_option",
        "wordpress.set_front_page",
        "wordpress.publish",
        "wordpress.purge_varnish",
        "wordpress.verify_live",
    ]:
        assert get_capability(name) is not None, f"{name} not registered"

"""KAI-41 — the WP write result must carry the page status so drafts-only is
confirmable from the result itself (the KAI-36 gap: create_page returned
id/link/marker/brand_drift but not status, forcing a second WP read to prove draft)."""
import capabilities.wordpress as wp
from workflows import wordpress_verifiers as wv


class _Resp:
    def __init__(self, data, ok=True, status_code=200):
        self.ok = ok
        self.data = data
        self.status_code = status_code
        self.body_preview = ""


def _patch_transport(monkeypatch, wp_status):
    # server echoes back the created/updated page object incl. its status
    resp = _Resp({"id": 99, "link": "https://x/?page_id=99", "status": wp_status})
    monkeypatch.setattr(wp, "safe_request", lambda *a, **k: resp)
    monkeypatch.setattr(wp, "wp_write_preflight", lambda *a, **k: None)
    # brand-drift reads a profile; stub it to a fixed report so the test is hermetic
    monkeypatch.setattr(wp, "_run_brand_drift",
                        lambda site, prop, content: {"slug": "x", "checked": False,
                                                     "drift": False, "governed": False})


def test_create_page_result_echoes_status(monkeypatch):
    _patch_transport(monkeypatch, "draft")
    r = wp.create_page(site="testsite", title="T", content="<p>x</p>", status="draft",
                       creds={"fqdn": "x", "app_password": "y"}, caller="test")
    assert r.ok
    assert r.data["status"] == "draft"
    assert r.data["requested_status"] == "draft"


def test_create_page_surfaces_server_status_override(monkeypatch):
    # if the server ignored our request and published, the result must SHOW it
    _patch_transport(monkeypatch, "publish")
    r = wp.create_page(site="testsite", title="T", content="<p>x</p>", status="draft",
                       creds={"fqdn": "x", "app_password": "y"}, caller="test")
    assert r.data["status"] == "publish"        # server's actual state, honestly surfaced
    assert r.data["requested_status"] == "draft"  # what we asked for — the mismatch is visible


def test_verify_page_exists_surfaces_wp_status(monkeypatch):
    resp = _Resp({"id": 99, "status": "draft"})
    monkeypatch.setattr(wv, "safe_request", lambda *a, **k: resp)
    out = wv.verify_page_exists("testsite", {"fqdn": "x", "app_password": "y"},
                                {"data": {"id": 99}})
    assert out["verified"] is True
    assert out["evidence"]["wp_status"] == "draft"
    assert out["evidence"]["status"] == 200   # HTTP code contract preserved

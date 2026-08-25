"""AR-1 publish-governance rail — hostops.publish_post.

The drafts-only floor (JARVIS §9) is enforced at two independent layers:
  1. the shared org-model `classify` chokepoint (the "publish" high-risk
     threshold forces approval even for a Leo-owned site), and
  2. a code-level fail-closed guard in the capability that refuses any
     authorization resolved as "autonomous".
These tests pin both, plus the plugin-gate-then-publish ordering and L18
(the payload secret never appears in a result).
"""
import json

import pytest

from autonomy_decisions import classify
from capabilities import get_capability
from capabilities.hostops import publish_post


_SECRET = "b" * 64  # mirrors hostops_provision.token_hex(32) -> 64 hex chars
CONSUME_CALLS = []  # records (gate_id, operation, site, resource) per fake consume


@pytest.fixture(autouse=True)
def configured(tmp_path, monkeypatch):
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": {"site": {
        "cloudways_app_id": "123",
        "cloudways_sys_user": "appuser",
        "cloudways_server_id": "456",
        "cloudways_fqdn": "host.example",
        "url": "https://example.com",
        "owner": "leo",
    }}}))
    monkeypatch.setattr("capabilities.hostops._SITES_JSON", sites)

    payload = tmp_path / "payload"
    (payload / "site").mkdir(parents=True)
    (payload / "site" / "kai_publish_gate_secret").write_text(_SECRET)
    monkeypatch.setattr("capabilities.hostops._PAYLOAD_DIR", payload)

    # An org-model whose high-risk threshold carries "publish" — the security spine.
    org = tmp_path / "org_model.json"
    org.write_text(json.dumps({"routing_rules": {"infrastructure_task": {
        "high_risk_threshold": ["structural change", "new service", "publish"]}}}))
    monkeypatch.setattr("autonomy_decisions._ORG_MODEL_PATH", org)

    CONSUME_CALLS.clear()

    def fake_consume(gate_id, operation, site, resource=None):
        CONSUME_CALLS.append({"gate_id": gate_id, "operation": operation,
                              "site": site, "resource": resource})
        return gate_id == "approved-gate" and operation == "publish_post" and site == "site"

    monkeypatch.setattr("engine.engine.consume_hostops_gate", fake_consume)


class FakeTransport:
    def __init__(self, published=True):
        self.published, self.calls = published, []

    def publish_post(self, target, post_id):
        self.calls.append(("publish_post", target.audit_identity, post_id))
        return {"published": self.published,
                "post_status": "publish" if self.published else "draft", "post_id": post_id}


def _opener(gate_open=True):
    seen = {}

    def opener(url, post_id, secret, resolver, gate_id):
        seen.update(url=url, post_id=post_id, secret=secret, resolver=resolver, gate_id=gate_id)
        return {"gate_open": gate_open, "status_code": 200 if gate_open else 403}

    opener.seen = seen
    return opener


def test_capability_is_registered():
    assert get_capability("hostops.publish_post")


def test_spine_leo_owned_publish_is_never_autonomous():
    # With "publish" in the threshold, a Leo-owned publish still needs approval.
    decision = classify({"op": "publish_post", "site": "site", "owner": "leo", "risk": ""})
    assert decision.mode == "approve"


def test_publish_fails_closed_without_a_verified_gate():
    assert publish_post("site", 5).error["type"] == "gate_required"
    assert publish_post("site", 5, gate_id="not-a-real-handle").error["type"] == "gate_required"


def test_autonomous_authorization_is_refused(monkeypatch):
    # Defence against org-model drift: even if _gate ever yields "autonomous", publish must not proceed.
    monkeypatch.setattr("capabilities.hostops._gate", lambda *a, **k: "autonomous")
    t = FakeTransport()
    result = publish_post("site", 5, gate_id="approved-gate", transport=t, gate_opener=_opener())
    assert result.error["type"] == "autonomous_publish_forbidden"
    assert t.calls == []  # never touched the site


def test_invalid_post_id_is_rejected():
    assert publish_post("site", "abc", gate_id="approved-gate").error["type"] == "input_not_allowed"
    assert publish_post("site", 0, gate_id="approved-gate").error["type"] == "input_not_allowed"
    assert publish_post("site", -3, gate_id="approved-gate").error["type"] == "input_not_allowed"


def test_gate_must_open_before_publish():
    t = FakeTransport()
    result = publish_post("site", 7, gate_id="approved-gate", transport=t, gate_opener=_opener(gate_open=False))
    assert not result.ok and result.error["type"] == "publish_gate_not_opened"
    assert t.calls == []  # publish never attempted when the plugin gate stays shut


def test_happy_path_opens_gate_then_publishes_and_hides_secret():
    opener = _opener()
    t = FakeTransport(published=True)
    result = publish_post("site", 42, gate_id="approved-gate", resolver="leo",
                          transport=t, gate_opener=opener)
    assert result.ok and result.data["post_id"] == 42
    assert result.verification["evidence"]["post_status"] == "publish"
    assert result.verification["evidence"]["gate_id"] == "approved-gate"
    # The gate was opened with the payload secret, and it was published exactly once.
    assert opener.seen["secret"] == _SECRET
    assert t.calls == [("publish_post", "cloudways-app:123:appuser", 42)]
    # The consumed gate was bound to THIS exact post_id (not just the site).
    assert CONSUME_CALLS[-1]["resource"] == "42"
    # L18: the secret never leaks into the returned result.
    assert _SECRET not in json.dumps(result.__dict__, default=str)


def test_gate_is_bound_to_the_exact_post_id(monkeypatch):
    # A gate resolved for post 42 must not authorize publishing a different post.
    def consume_only_post_42(gate_id, operation, site, resource=None):
        return (gate_id == "approved-gate" and operation == "publish_post"
                and site == "site" and resource == "42")

    monkeypatch.setattr("engine.engine.consume_hostops_gate", consume_only_post_42)
    t = FakeTransport()
    # post 99 presented with the post-42 gate -> no valid gate -> fail closed.
    result = publish_post("site", 99, gate_id="approved-gate", transport=t, gate_opener=_opener())
    assert not result.ok and result.error["type"] == "gate_required"
    assert t.calls == []


def test_publish_failure_is_reported_not_faked():
    t = FakeTransport(published=False)
    result = publish_post("site", 9, gate_id="approved-gate", transport=t, gate_opener=_opener())
    assert not result.ok and result.error["type"] == "publish_failed"

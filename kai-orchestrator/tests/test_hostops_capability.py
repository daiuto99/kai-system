import json
import logging
import subprocess
import pytest

from capabilities import get_capability
from capabilities.hostops import (
    HostOpsTarget,
    InMemorySecret,
    OpenSshTransport,
    deploy_plugin,
    place_secret,
    status,
    verify,
)


@pytest.fixture(autouse=True)
def configured_target(tmp_path, monkeypatch):
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": {"site": {
        "cloudways_app_id": "123",
        "cloudways_sys_user": "appuser",
        "cloudways_server_id": "456",
        "cloudways_fqdn": "host.example",
    }}}))
    monkeypatch.setattr("capabilities.hostops._SITES_JSON", sites)
    monkeypatch.setattr(
        "engine.engine.consume_hostops_gate",
        lambda gate_id, operation, site, resource=None: gate_id == "approved-gate" and operation == "place_secret" and site == "site",
    )


class FakeTransport:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def verify(self, target):
        self.calls.append(("verify", target))
        if self.fail:
            raise RuntimeError("auth failure")
        return {"authenticated": True, "writable": True, "probe": "master_ssh"}

    def place_secret(self, target, secret):
        self.calls.append(("place_secret", target, secret))
        return {"written": True, "read_back": True, "name": "KAI_PUBLISH_GATE_SECRET"}

    def deploy_plugin(self, target, plugin):
        self.calls.append(("deploy_plugin", target, plugin))
        return {"deployed": True, "read_back": True, "plugin": plugin}


def test_exactly_four_hostops_capabilities_are_registered():
    for name in ("hostops.status", "hostops.verify", "hostops.place_secret", "hostops.deploy_plugin"):
        assert get_capability(name)


def test_status_uses_master_transport_without_per_app_key_loader():
    result = status("site", transport=FakeTransport())
    assert result.ok and result.data["identity"] == "cloudways-app:123:appuser"


def test_mutations_fail_closed_without_verified_gate():
    assert place_secret("site", "publish_gate", InMemorySecret(b"x")).error["type"] == "gate_required"
    assert deploy_plugin("site", "kai-publish-gate").error["type"] == "gate_required"
    assert deploy_plugin("site", "kai-publish-gate", gate_id="not-a-verified-handle").error["type"] == "gate_required"


def test_allowlist_rejects_plugin_and_no_generic_hostops_op_exists():
    result = deploy_plugin("site", "anything-else", "approved-gate")
    assert result.error["type"] == "plugin_not_allowed"
    with pytest.raises(KeyError):
        get_capability("hostops.run_scoped")


def test_transport_never_leaks_payload_secret_on_success_or_error(caplog):
    secret = InMemorySecret(b"payload-secret")
    transport = FakeTransport()
    with caplog.at_level(logging.DEBUG):
        good = place_secret("site", "publish_gate", secret, "approved-gate", transport=transport)
        good_verify = verify("site", transport=transport)
        bad = verify("site", transport=FakeTransport(fail=True))
    transcript = repr(good) + repr(good_verify) + repr(bad) + caplog.text
    assert secret.material.decode() not in transcript
    assert good.verification["evidence"]["gate_id"] == "approved-gate"
    assert not bad.ok


def test_openssh_secret_uses_stdin_and_never_argv():
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "stdin-only-secret\n", "")

    transport = OpenSshTransport(runner)
    target = HostOpsTarget("host.example", "appuser", "123", "456")
    proof = transport.place_secret(target, b"stdin-only-secret")
    assert proof["read_back"] is True
    argv_text = " ".join(" ".join(map(str, argv)) for argv, _ in calls)
    assert "stdin-only-secret" not in argv_text
    assert calls[0][1]["input"] == "stdin-only-secret"


def test_forged_or_wrong_bound_gate_is_refused():
    forged = place_secret("site", "publish_gate", InMemorySecret(b"x"), "forged")
    wrong_operation = deploy_plugin("site", "kai-publish-gate", "approved-gate")
    assert forged.error["type"] == "gate_required"
    assert wrong_operation.error["type"] == "gate_required"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (subprocess.TimeoutExpired(["ssh"], 15), False),
        (subprocess.CompletedProcess(["ssh"], 255, "", ""), False),
        (subprocess.CompletedProcess(["ssh"], 0, "OK\n", ""), True),
    ],
)
def test_verify_never_treats_timeout_or_exit_255_as_authenticated(outcome, expected):
    target = HostOpsTarget("host.example", "appuser", "123", "456")

    def runner(argv, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    proof = OpenSshTransport(runner).verify(target)
    assert proof["authenticated"] is expected

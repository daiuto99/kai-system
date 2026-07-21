import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from capabilities import get_capability
from capabilities.hostops import (
    InMemorySecret,
    OpenSshTransport,
    VerifiedGate,
    deploy_plugin,
    place_secret,
    status,
    verify,
)
from hostops_identity import DeployKeyLoader, HostOpsIdentityResolver


@pytest.fixture(autouse=True)
def configured_target(monkeypatch):
    from capabilities.hostops import HostOpsTarget

    monkeypatch.setattr(
        "capabilities.hostops._target",
        lambda site, handle: HostOpsTarget(
            "host.example", handle.cloudways_sys_user, handle.cloudways_app_id
        ),
    )


class FakeTransport:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def verify(self, handle, target, key_material):
        self.calls.append(("verify", key_material))
        if self.fail:
            raise RuntimeError("auth failure")
        return {"authenticated": True, "probe": "ssh_no_command"}

    def place_secret(self, handle, target, name, secret, key_material):
        self.calls.append(("place_secret", key_material, secret))
        return {"written": True, "read_back": True, "name": name}

    def deploy_plugin(self, handle, target, plugin, key_material):
        self.calls.append(("deploy_plugin", key_material))
        return {"deployed": True, "read_back": True, "plugin": plugin}


def _context(tmp_path: Path):
    sites = tmp_path / "sites.json"
    store = tmp_path / "keys"
    store.mkdir()
    sites.write_text(json.dumps({"sites": {"site": {
        "cloudways_app_id": "123",
        "cloudways_sys_user": "appuser",
        "cloudways_fqdn": "host.example",
    }}}))
    handle = HostOpsIdentityResolver(sites, store).resolve("site")
    key = b"fixture-key-must-never-leak-into-argv-log-or-result"
    handle.secret_path.write_bytes(key)
    handle.secret_path.chmod(0o600)
    return HostOpsIdentityResolver(sites, store), DeployKeyLoader(store, runtime_uid=os.geteuid()), key


def test_exactly_four_hostops_capabilities_are_registered():
    for name in ("hostops.status", "hostops.verify", "hostops.place_secret", "hostops.deploy_plugin"):
        assert get_capability(name)


def test_status_validates_without_reading_private_key(tmp_path):
    resolver, loader, _ = _context(tmp_path)
    result = status("site", resolver=resolver, loader=loader)
    assert result.ok and result.data["key_present"]


def test_mutations_fail_closed_without_verified_gate(tmp_path):
    resolver, loader, _ = _context(tmp_path)
    assert place_secret("site", "publish_gate", InMemorySecret(b"x"), resolver=resolver, loader=loader).error["type"] == "gate_required"
    assert deploy_plugin("site", "kai-publish-gate", resolver=resolver, loader=loader).error["type"] == "gate_required"
    assert deploy_plugin("site", "kai-publish-gate", gate_id="not-a-verified-handle", resolver=resolver, loader=loader).error["type"] == "gate_required"


def test_allowlist_rejects_plugin_and_no_generic_hostops_op_exists(tmp_path):
    resolver, loader, _ = _context(tmp_path)
    result = deploy_plugin("site", "anything-else", VerifiedGate("gate-1"), resolver=resolver, loader=loader)
    assert result.error["type"] == "plugin_not_allowed"
    with pytest.raises(KeyError):
        get_capability("hostops.run_scoped")


def test_transport_never_leaks_private_key_on_success_or_error(tmp_path, caplog):
    resolver, loader, key = _context(tmp_path)
    secret = InMemorySecret(b"other-secret")
    transport = FakeTransport()
    with caplog.at_level(logging.DEBUG):
        good = place_secret("site", "publish_gate", secret, VerifiedGate("gate-1"), resolver=resolver, loader=loader, transport=transport)
        good_verify = verify("site", resolver=resolver, loader=loader, transport=transport)
        bad = verify("site", resolver=resolver, loader=loader, transport=FakeTransport(fail=True))
    transcript = repr(good) + repr(good_verify) + repr(bad) + caplog.text
    assert key.decode() not in transcript
    assert secret.material.decode() not in transcript
    assert good.verification["evidence"]["gate_id"] == "gate-1"
    assert not bad.ok


def test_openssh_argv_never_contains_key_or_secret(tmp_path):
    resolver, loader, key = _context(tmp_path)
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        if "-N" in argv:
            raise subprocess.TimeoutExpired(argv, 3)
        return subprocess.CompletedProcess(argv, 0)

    transport = OpenSshTransport(runner)
    placed = place_secret("site", "publish_gate", InMemorySecret(b"stdin-only-secret"), VerifiedGate("gate-1"), resolver=resolver, loader=loader, transport=transport)
    deployed = deploy_plugin("site", "kai-publish-gate", VerifiedGate("gate-1"), resolver=resolver, loader=loader, transport=transport)
    assert placed.ok and deployed.ok
    argv_text = " ".join(" ".join(map(str, argv)) for argv, _ in calls)
    assert key.decode() not in argv_text
    assert "stdin-only-secret" not in argv_text
    assert calls[0][1]["input"] == b"stdin-only-secret"

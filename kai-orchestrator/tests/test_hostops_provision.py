"""HOSTOPS provisioning tests (KAI-928/929).

The real SSH boundary is stubbed: these pin the *contract* — idempotency, L18
(no key/secret bytes in the returned metadata), correct file mode/owner on the
minted secret, and site-key validation. Network auth itself is proven live by the
deploy acceptance, not here.
"""
import json
import os
import subprocess

import pytest

import hostops_provision as hp


ALEXA = {
    "cloudways_app_id": "6412749",
    "cloudways_sys_user": "mnnzahcavz",
    "cloudways_server_id": "1623875",
    "cloudways_fqdn": "wordpress-1623875-6412749.cloudwaysapps.com",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    key_dir = tmp_path / "keys"
    payload_dir = tmp_path / "payload"
    key_dir.mkdir()
    payload_dir.mkdir()
    monkeypatch.setattr(hp, "_KEY_DIR", key_dir)
    monkeypatch.setattr(hp, "_PAYLOAD_DIR", payload_dir)
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({"sites": {"alexadaiuto": ALEXA}}))
    monkeypatch.setattr(hp, "_SITES_JSON", sites)
    return key_dir, payload_dir


def _fake_runner(monkeypatch, *, authed=True, install_ok=True):
    """Stub ssh-keygen (writes a fake keypair) + ssh probes/installs."""
    def run(cmd, timeout=30, **kw):
        if cmd[0] == "ssh-keygen":
            f = cmd[cmd.index("-f") + 1]
            with open(f, "w") as fh:
                fh.write("PRIVATE-KEY-BYTES\n")
            with open(f + ".pub", "w") as fh:
                fh.write("ssh-ed25519 AAAAFAKEPUB kai-hostops-alexadaiuto\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "ssh":
            remote = cmd[-1]
            if remote == "true":  # auth probe
                return subprocess.CompletedProcess(cmd, 0 if authed else 255, "", "")
            return subprocess.CompletedProcess(cmd, 0 if install_ok else 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(hp, "_run", run)


def test_provisions_key_and_secret(store, monkeypatch):
    key_dir, payload_dir = store
    _fake_runner(monkeypatch)
    out = hp.provision("alexadaiuto", uid=os.geteuid())
    assert out.deploy_key == "created"
    assert out.payload_secret == "created"
    assert out.key_authenticated is True
    assert out.audit_identity == "cloudways-app:6412749:mnnzahcavz"
    # Files landed at deterministic paths, mode 0600.
    key = key_dir / "cloudways-app-6412749-mnnzahcavz.ed25519"
    secret = payload_dir / "alexadaiuto" / "kai_publish_gate_secret"
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert oct(secret.stat().st_mode & 0o777) == "0o600"
    assert secret.stat().st_size == 64  # token_hex(32)


def test_l18_result_carries_no_key_or_secret_bytes(store, monkeypatch):
    key_dir, payload_dir = store
    _fake_runner(monkeypatch)
    out = hp.provision("alexadaiuto", uid=os.geteuid())
    blob = json.dumps(hp.asdict(out))
    assert "PRIVATE-KEY-BYTES" not in blob
    secret_bytes = (payload_dir / "alexadaiuto" / "kai_publish_gate_secret").read_text()
    assert secret_bytes not in blob


def test_idempotent_present_when_valid(store, monkeypatch):
    _fake_runner(monkeypatch)
    hp.provision("alexadaiuto", uid=os.geteuid())
    # Second run must not re-create.
    out2 = hp.provision("alexadaiuto", uid=os.geteuid())
    assert out2.deploy_key == "present"
    assert out2.payload_secret == "present"


def test_rotate_replaces_secret(store, monkeypatch):
    _, payload_dir = store
    _fake_runner(monkeypatch)
    hp.provision("alexadaiuto", uid=os.geteuid())
    secret = payload_dir / "alexadaiuto" / "kai_publish_gate_secret"
    first = secret.read_text()
    out = hp.provision("alexadaiuto", uid=os.geteuid(), rotate=True)
    assert out.payload_secret == "rotated"
    assert secret.read_text() != first


def test_auth_probe_failure_raises(store, monkeypatch):
    _fake_runner(monkeypatch, authed=False)
    with pytest.raises(hp.HostOpsProvisionError, match="authentication probe failed"):
        hp.provision("alexadaiuto", uid=os.geteuid())


def test_install_failure_raises(store, monkeypatch):
    _fake_runner(monkeypatch, install_ok=False)
    with pytest.raises(hp.HostOpsProvisionError, match="deploy-key install failed"):
        hp.provision("alexadaiuto", uid=os.geteuid())


@pytest.mark.parametrize("bad", ["../escape", "site a", "a/b", ""])
def test_rejects_unsafe_site(store, monkeypatch, bad):
    _fake_runner(monkeypatch)
    with pytest.raises(hp.HostOpsProvisionError):
        hp.provision(bad, uid=os.geteuid())

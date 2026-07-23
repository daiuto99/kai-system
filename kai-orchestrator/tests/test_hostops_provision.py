"""Master-operator payload-secret provisioning tests (KAI-928/929)."""
import json
import os

import pytest

import hostops_provision as hp


@pytest.fixture
def store(tmp_path, monkeypatch):
    payload_dir = tmp_path / "payload"
    payload_dir.mkdir()
    monkeypatch.setattr(hp, "_PAYLOAD_DIR", payload_dir)
    return payload_dir


def test_provisions_payload_secret_without_creating_a_deploy_key(store):
    out = hp.provision_secret("alexadaiuto", uid=os.geteuid())
    assert out == {"site": "alexadaiuto", "payload_secret": "created"}
    secret = store / "alexadaiuto" / "kai_publish_gate_secret"
    assert oct(secret.stat().st_mode & 0o777) == "0o600"
    assert secret.stat().st_size == 64
    assert not any("key" in path.name for path in store.rglob("*"))


def test_l18_result_carries_no_secret_bytes(store):
    out = hp.provision_secret("alexadaiuto", uid=os.geteuid())
    blob = json.dumps(out)
    secret_bytes = (store / "alexadaiuto" / "kai_publish_gate_secret").read_text()
    assert secret_bytes not in blob


def test_idempotent_present_when_valid(store):
    hp.provision_secret("alexadaiuto", uid=os.geteuid())
    assert hp.provision_secret("alexadaiuto", uid=os.geteuid())["payload_secret"] == "present"


def test_rotate_replaces_secret(store):
    hp.provision_secret("alexadaiuto", uid=os.geteuid())
    secret = store / "alexadaiuto" / "kai_publish_gate_secret"
    first = secret.read_text()
    out = hp.provision_secret("alexadaiuto", uid=os.geteuid(), rotate=True)
    assert out["payload_secret"] == "rotated"
    assert secret.read_text() != first


@pytest.mark.parametrize("bad", ["../escape", "site a", "a/b", ""])
def test_rejects_unsafe_site(store, bad):
    with pytest.raises(hp.HostOpsProvisionError):
        hp.provision_secret(bad, uid=os.geteuid())

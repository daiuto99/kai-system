import json
import logging
import os
from pathlib import Path

import pytest

from hostops_identity import DeployKeyLoader, HostOpsIdentityError, HostOpsIdentityResolver


def _sites(path: Path):
    path.write_text(json.dumps({"sites": {"sette-uno": {
        "cloudways_app_id": "6412608",
        "cloudways_sys_user": "eaxetezduh",
    }}}))


def test_resolver_returns_scoped_handle_not_private_key(tmp_path):
    sites = tmp_path / "wordpress_sites.json"
    store = tmp_path / "hostops-deploy-keys"
    _sites(sites)

    handle = HostOpsIdentityResolver(sites, store).resolve("sette-uno")

    assert handle.key_name == "cloudways-app-6412608-eaxetezduh.ed25519"
    assert handle.audit_identity == "cloudways-app:6412608:eaxetezduh"
    assert "PRIVATE" not in repr(handle)


def test_loader_reads_only_mode_600_runtime_owned_key_without_leaking_on_success(tmp_path):
    sites = tmp_path / "wordpress_sites.json"
    store = tmp_path / "hostops-deploy-keys"
    store.mkdir()
    _sites(sites)
    handle = HostOpsIdentityResolver(sites, store).resolve("sette-uno")
    private_key = b"fixture-private-key-material-must-not-appear-in-results"
    handle.secret_path.write_bytes(private_key)
    handle.secret_path.chmod(0o600)

    result = DeployKeyLoader(store, runtime_uid=os.geteuid()).with_material(
        handle, lambda material: {"bytes_seen_by_transport": len(material)}
    )

    assert result == {"bytes_seen_by_transport": len(private_key)}
    assert private_key.decode() not in repr(result)


def test_loader_error_never_returns_or_logs_private_key_material(tmp_path, caplog):
    sites = tmp_path / "wordpress_sites.json"
    store = tmp_path / "hostops-deploy-keys"
    store.mkdir()
    _sites(sites)
    handle = HostOpsIdentityResolver(sites, store).resolve("sette-uno")
    private_key = b"fixture-private-key-material-must-not-leak-on-error"
    handle.secret_path.write_bytes(private_key)
    handle.secret_path.chmod(0o644)

    with caplog.at_level(logging.DEBUG), pytest.raises(HostOpsIdentityError) as exc:
        DeployKeyLoader(store, runtime_uid=os.geteuid()).with_material(handle, lambda material: material)

    combined = str(exc.value) + caplog.text
    assert private_key.decode() not in combined
    assert "mode 0600" in str(exc.value)

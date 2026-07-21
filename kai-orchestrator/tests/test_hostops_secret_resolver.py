"""HOSTOPS-(c): the execution-time payload resolver (design §3.3.1).

The workflow carries only a (site, secret_name) reference; the bytes are read
here, post-approval, from a mode-0600 file owned by the runtime user. These
tests pin the failure layer: wrong mode, wrong owner, bad names, missing/empty,
and path escape are all refused before any byte is returned.
"""
import os

import pytest

from hostops_identity import HostOpsSecretResolver, HostOpsIdentityError


def _resolver(tmp_path):
    return HostOpsSecretResolver(secret_dir=tmp_path, runtime_uid=os.geteuid())


def _write_secret(tmp_path, site, name, data=b"payload-bytes", mode=0o600):
    d = tmp_path / site
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(data)
    os.chmod(p, mode)
    return p


def test_resolves_bytes_for_valid_reference(tmp_path):
    _write_secret(tmp_path, "site-a", "publish_gate")
    assert _resolver(tmp_path).resolve("site-a", "publish_gate") == b"payload-bytes"


def test_rejects_wrong_mode(tmp_path):
    _write_secret(tmp_path, "site-a", "publish_gate", mode=0o644)
    with pytest.raises(HostOpsIdentityError, match="mode 0600"):
        _resolver(tmp_path).resolve("site-a", "publish_gate")


def test_rejects_wrong_owner(tmp_path):
    _write_secret(tmp_path, "site-a", "publish_gate")
    resolver = HostOpsSecretResolver(secret_dir=tmp_path, runtime_uid=os.geteuid() + 12345)
    with pytest.raises(HostOpsIdentityError, match="owned by the runtime user"):
        resolver.resolve("site-a", "publish_gate")


def test_rejects_missing_file(tmp_path):
    with pytest.raises(HostOpsIdentityError, match="unavailable"):
        _resolver(tmp_path).resolve("site-a", "publish_gate")


def test_rejects_empty_secret(tmp_path):
    _write_secret(tmp_path, "site-a", "publish_gate", data=b"")
    with pytest.raises(HostOpsIdentityError, match="empty"):
        _resolver(tmp_path).resolve("site-a", "publish_gate")


@pytest.mark.parametrize("site,name", [
    ("../escape", "publish_gate"),
    ("site-a", "../../etc/shadow"),
    ("site a", "publish_gate"),
    ("site-a", "name with space"),
])
def test_rejects_unsafe_components(tmp_path, site, name):
    with pytest.raises(HostOpsIdentityError):
        _resolver(tmp_path).resolve(site, name)


def test_secret_dir_is_isolated_root(tmp_path):
    # A file directly under secret_dir (not under a <site>/ subdir) is out of the
    # one-deep store layout and must be refused even if names look safe.
    outside = tmp_path / "loose"
    outside.write_bytes(b"x")
    os.chmod(outside, 0o600)
    with pytest.raises(HostOpsIdentityError):
        _resolver(tmp_path).resolve("loose", "..")

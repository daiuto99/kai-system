"""
Tests for KAI-984 inc5 — node-aware transport wiring in provision_run.

Focus: `load_node_transport` fail-closed parsing, and `run()`'s two refuse gates
(enrollment R1, transport-config completeness) plus the flags-override-file merge and the
per-node params that reach the constructed OpenSshSecretTransport. No live SSH, Telegram,
tailscale, or real secret: the single composed call (`provision_capability.provision_secret`)
is stubbed, so we assert exactly which transport wiring run() would have used.
"""
import json

import pytest

import provision_run
from provision_capability import ProvisionResult


# ── fixtures ───────────────────────────────────────────────────────────────────

CONFIRMED_ALLOWLIST = {
    "enrollment_status": "confirmed",
    "nodes": {"kai-worker": "nzkpgsJk1M11CNTRL", "71-kai-mini": "ntzBBuNMsE11CNTRL"},
}
UNENROLLED_ALLOWLIST = {
    "enrollment_status": "seeded_pending_leo_confirmation",
    "nodes": {"71-kai-mini": "ntzBBuNMsE11CNTRL"},
}
TRANSPORT_MAP = {
    "_note": "ignored",
    "kai-worker": {"ssh_user": "leo", "ssh_key": "/home/leo/.ssh/id_ed25519",
                   "remote_secrets_dir": "/home/leo/kai-system/secrets"},
    "71-kai-mini": {"ssh_user": "leodaiuto", "ssh_key": "/home/leo/.ssh/id_ed25519",
                    "remote_secrets_dir": "/Users/leodaiuto/kai-system/secrets"},
}


def _write(p, obj):
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


@pytest.fixture
def allowlist(tmp_path):
    return _write(tmp_path / "allow.json", CONFIRMED_ALLOWLIST)


@pytest.fixture
def transport_map(tmp_path):
    return _write(tmp_path / "transport.json", TRANSPORT_MAP)


@pytest.fixture
def audit(tmp_path):
    return str(tmp_path / "logs" / "audit.jsonl")   # dir created by run()


@pytest.fixture
def captured_transport(monkeypatch):
    """Stub the composed capability so run() reaches provision_secret without live IO, capturing
    the transport it was handed. Returns a dict updated with the transport once run() proceeds."""
    box = {}

    def fake_provision_secret(*, transport, node, secret_name, **kw):
        box["transport"] = transport
        box["node"] = node
        return ProvisionResult(ok=True, status="provisioned", node=node, node_id="ntzBBuNMsE11CNTRL",
                               secret_name=secret_name, approval_id="appr-1", reason="ok")

    monkeypatch.setattr(provision_run, "_tailscale_status", lambda *a, **k: {})
    monkeypatch.setattr(provision_run.provision_capability, "provision_secret", fake_provision_secret)
    return box


# ── load_node_transport: fail-closed parsing ────────────────────────────────────

def test_load_node_transport_valid(transport_map):
    cfg = provision_run.load_node_transport(transport_map, "71-kai-mini")
    assert cfg == {"ssh_user": "leodaiuto", "ssh_key": "/home/leo/.ssh/id_ed25519",
                   "remote_secrets_dir": "/Users/leodaiuto/kai-system/secrets"}


def test_load_node_transport_missing_node(transport_map):
    assert provision_run.load_node_transport(transport_map, "no-such-node") == {}


def test_load_node_transport_note_key_is_not_a_node(transport_map):
    # `_note` is a str, not an object => not a valid node entry => {} (never a partial config)
    assert provision_run.load_node_transport(transport_map, "_note") == {}


def test_load_node_transport_missing_file(tmp_path):
    assert provision_run.load_node_transport(str(tmp_path / "nope.json"), "71-kai-mini") == {}


def test_load_node_transport_malformed_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert provision_run.load_node_transport(str(p), "71-kai-mini") == {}


def test_load_node_transport_partial_entry_fails_closed(tmp_path):
    p = _write(tmp_path / "partial.json",
               {"71-kai-mini": {"ssh_user": "leodaiuto", "ssh_key": "/home/leo/.ssh/id_ed25519"}})
    assert provision_run.load_node_transport(p, "71-kai-mini") == {}   # remote_secrets_dir absent


@pytest.mark.parametrize("bad", [{"ssh_user": "", "ssh_key": "k", "remote_secrets_dir": "d"},
                                 {"ssh_user": 5, "ssh_key": "k", "remote_secrets_dir": "d"},
                                 {"ssh_user": "u", "ssh_key": None, "remote_secrets_dir": "d"}])
def test_load_node_transport_bad_param_types_fail_closed(tmp_path, bad):
    p = _write(tmp_path / "bad.json", {"n": bad})
    assert provision_run.load_node_transport(p, "n") == {}


def test_load_node_transport_duplicate_node_key_fails_closed(tmp_path):
    # Raw JSON with a duplicate top-level node key — last-value-wins in stock json.loads. Our loader
    # must reject the ambiguity (parser-differential spoof) and return {}. (Codex inc5 finding #2.)
    p = tmp_path / "dupnode.json"
    p.write_text('{"n": {"ssh_user": "first", "ssh_key": "/k", "remote_secrets_dir": "/d"},'
                 ' "n": {"ssh_user": "last", "ssh_key": "/k", "remote_secrets_dir": "/d"}}',
                 encoding="utf-8")
    assert provision_run.load_node_transport(str(p), "n") == {}


def test_load_node_transport_duplicate_field_key_fails_closed(tmp_path):
    # Duplicate transport-FIELD key inside an entry must also fail closed.
    p = tmp_path / "dupfield.json"
    p.write_text('{"n": {"ssh_user": "first", "ssh_user": "last",'
                 ' "ssh_key": "/k", "remote_secrets_dir": "/d"}}', encoding="utf-8")
    assert provision_run.load_node_transport(str(p), "n") == {}


# ── run(): enrollment gate (R1) precedes everything ─────────────────────────────

def test_run_refuses_unenrolled(tmp_path, transport_map, audit, capsys):
    al = _write(tmp_path / "unen.json", UNENROLLED_ALLOWLIST)
    rc = provision_run.run(["--node", "71-kai-mini", "--secret", "anthropic_api_key",
                            "--allowlist", al, "--node-transport", transport_map, "--audit", audit])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"] == "refused_unenrolled"
    assert "anthropic_api_key" not in json.dumps(out) or out["secret_name"] == "anthropic_api_key"


# ── run(): transport-config completeness gate ───────────────────────────────────

def test_run_refuses_node_without_transport_config(allowlist, transport_map, audit, capsys):
    # kai-worker is enrolled but we point at a node absent from the transport map, no flags.
    rc = provision_run.run(["--node", "mac-mini", "--secret", "todoist_api_key",
                            "--allowlist", allowlist, "--node-transport", transport_map, "--audit", audit])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"] == "refused_no_transport_config"
    assert "remote_secrets_dir" in out["reason"]


def test_run_refuses_empty_flag_override(allowlist, transport_map, audit, capsys):
    # 71-kai-mini HAS valid file wiring, but an explicit empty --ssh-user "" overrides it. The gate
    # must reject the blank value (value-completeness, not presence) — never build a blank transport.
    # (Codex inc5 finding #1.)
    rc = provision_run.run(["--node", "71-kai-mini", "--secret", "anthropic_api_key",
                            "--allowlist", allowlist, "--node-transport", transport_map, "--audit", audit,
                            "--ssh-user", ""])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"] == "refused_no_transport_config"
    assert "ssh_user" in out["reason"]


def test_run_refuses_all_empty_flags(allowlist, transport_map, audit, capsys):
    # Three empty flags on a node absent from the map must NOT satisfy the gate.
    rc = provision_run.run(["--node", "mac-mini", "--secret", "anthropic_api_key",
                            "--allowlist", allowlist, "--node-transport", transport_map, "--audit", audit,
                            "--ssh-user", "", "--ssh-key", "", "--remote-secrets-dir", ""])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["status"] == "refused_no_transport_config"


def test_run_resolves_node_config_and_proceeds(allowlist, transport_map, audit,
                                                captured_transport, capsys):
    rc = provision_run.run(["--node", "71-kai-mini", "--secret", "anthropic_api_key",
                            "--allowlist", allowlist, "--node-transport", transport_map, "--audit", audit])
    capsys.readouterr()
    t = captured_transport["transport"]
    assert rc == 0
    assert (t._user, t._key, t._dir) == (
        "leodaiuto", "/home/leo/.ssh/id_ed25519", "/Users/leodaiuto/kai-system/secrets")


def test_run_flags_override_file_config(allowlist, transport_map, audit,
                                        captured_transport, capsys):
    rc = provision_run.run(["--node", "71-kai-mini", "--secret", "anthropic_api_key",
                            "--allowlist", allowlist, "--node-transport", transport_map, "--audit", audit,
                            "--ssh-user", "override-user", "--remote-secrets-dir", "/tmp/override"])
    capsys.readouterr()
    t = captured_transport["transport"]
    assert rc == 0
    # overridden params come from flags; the un-overridden ssh_key still comes from the file
    assert (t._user, t._dir, t._key) == (
        "override-user", "/tmp/override", "/home/leo/.ssh/id_ed25519")


def test_run_flags_only_satisfy_completeness_without_file_entry(allowlist, transport_map, audit,
                                                                captured_transport, capsys):
    # mac-mini not in the transport map, but all three flags supplied => gate satisfied.
    rc = provision_run.run(["--node", "mac-mini", "--secret", "anthropic_api_key",
                            "--allowlist", allowlist, "--node-transport", transport_map, "--audit", audit,
                            "--ssh-user", "u", "--ssh-key", "/k", "--remote-secrets-dir", "/d"])
    capsys.readouterr()
    t = captured_transport["transport"]
    assert rc == 0 and (t._user, t._key, t._dir) == ("u", "/k", "/d")

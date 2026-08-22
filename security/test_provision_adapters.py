"""
Tests for the KAI-984 live adapters: provision_gate (Telegram approval) + provision_source
(server-side secret read). Every path is exercised with injected fakes — no live worker-api,
Telegram, wall-clock, or real secret. Focus: every fail-closed deny branch, the two security
decisions (fresh per-action tap; only `approved_once` moves a secret), the R4 card specificity,
and byte-identical secret reads.
"""
import os
import stat

import pytest

from provision_capability import Approval
from provision_gate import TelegramApprovalGate, PROVISION_REQUESTER
from provision_source import FileSecretSource

SECRET = "anthropic_api_key"
NODE = "kai-mini"
REQUESTER = "kai-worker-session"


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, create, statuses):
        self._create = create
        self._statuses = list(statuses)
        self.posts = []
        self.gets = []

    def post(self, path, body):
        self.posts.append((path, body))
        if isinstance(self._create, BaseException):
            raise self._create
        return self._create

    def get(self, path):
        self.gets.append(path)
        if not self._statuses:
            return {"status": "pending"}
        item = self._statuses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    """monotonic advances only when sleep() is called — deterministic timeout."""
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _gate(create, statuses, *, timeout_s=5, poll=1):
    clock = FakeClock()
    client = FakeClient(create, statuses)
    gate = TelegramApprovalGate(
        client=client, poll_interval_s=poll, timeout_s=timeout_s,
        sleep=clock.sleep, monotonic=clock.monotonic,
    )
    return gate, client


def _ok_create(rid="rid123"):
    return {"request_id": rid, "status": "pending"}


# ── gate: approve path ───────────────────────────────────────────────────────

def test_approved_once_is_the_only_approval():
    gate, client = _gate(_ok_create(), [{"status": "pending"}, {"status": "approved_once"}])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert isinstance(a, Approval)
    assert a.approved is True
    assert a.approval_id == "rid123"


# ── gate: R4 card specificity + decision #1 (dedicated requester) ────────────

def test_card_is_specific_and_uses_dedicated_requester():
    gate, client = _gate(_ok_create(), [{"status": "approved_once"}])
    gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    path, body = client.posts[0]
    assert path == "/mode_lock/request_approval"
    assert body["tool"] == "provision_secret"
    assert SECRET in body["target"] and NODE in body["target"]
    assert REQUESTER in body["reason"]           # R4: real requester named on the card
    assert body["requester"] == PROVISION_REQUESTER  # decision #1: no write-unlock short-circuit


# ── gate: every deny branch ──────────────────────────────────────────────────

def test_create_post_raises_denies():
    gate, _ = _gate(RuntimeError("boom"), [])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False and a.approval_id is None


def test_create_non_dict_denies():
    gate, _ = _gate("not-a-dict", [])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False


def test_create_missing_request_id_denies():
    gate, _ = _gate({"status": "pending"}, [])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False and a.approval_id is None


def test_create_time_session_shortcircuit_denies():
    # decision #1 fail-closed: an immediate approved_session is not a fresh per-action tap.
    gate, client = _gate({"request_id": "rid", "status": "approved_session"}, [])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False
    assert a.approval_id == "rid"
    assert client.gets == []  # never polled — denied at create


def test_denied_status_denies():
    gate, _ = _gate(_ok_create(), [{"status": "denied"}])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False


def test_expired_status_denies():
    gate, _ = _gate(_ok_create(), [{"status": "expired"}])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False


def test_session_grant_tap_denies():
    # decision #2: tapping "Allow session (1h)" on THIS card does not authorize moving a secret.
    gate, _ = _gate(_ok_create(), [{"status": "approved_session"}])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False


def test_pending_until_timeout_denies():
    gate, client = _gate(_ok_create(), [], timeout_s=5, poll=1)
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False
    assert len(client.gets) >= 1  # polled, then timed out


def test_transient_poll_error_then_approved():
    gate, _ = _gate(_ok_create(), [ConnectionError("blip"), {"status": "approved_once"}])
    a = gate.request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is True


# ── source: byte-identical read + fail-closed ────────────────────────────────

def _write(tmp_path, name, data: bytes, mode=0o600):
    p = tmp_path / f"{name}.txt"
    p.write_bytes(data)
    os.chmod(p, mode)
    return p


def test_source_reads_exact_bytes(tmp_path):
    _write(tmp_path, SECRET, b"sk-ant-VALUE\n")  # trailing newline preserved (byte-identical)
    src = FileSecretSource(str(tmp_path))
    assert src.read(SECRET) == b"sk-ant-VALUE\n"


def test_source_rejects_group_or_world_readable(tmp_path):
    _write(tmp_path, SECRET, b"sk-ant-VALUE", mode=0o644)
    src = FileSecretSource(str(tmp_path))
    assert src.read(SECRET) is None


def test_source_missing_is_none(tmp_path):
    src = FileSecretSource(str(tmp_path))
    assert src.read("nope") is None


def test_source_rejects_path_separator(tmp_path):
    src = FileSecretSource(str(tmp_path))
    assert src.read("../etc/passwd") is None
    assert src.read("a/b") is None


def test_source_empty_file_is_none(tmp_path):
    _write(tmp_path, SECRET, b"")
    src = FileSecretSource(str(tmp_path))
    assert src.read(SECRET) is None


def test_source_non_str_is_none(tmp_path):
    src = FileSecretSource(str(tmp_path))
    assert src.read(None) is None
    assert src.read(b"anthropic_api_key") is None

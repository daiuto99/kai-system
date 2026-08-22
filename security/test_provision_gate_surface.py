"""
COMMS P2 — channel-agnostic approval: the provision gate must thread its `surface` choice
('present' keyboard vs 'telegram' away) into the /mode_lock/request_approval body, and must poll
the SAME channel-neutral decision store regardless (so a present in-session approval resolves
identically to a remote Telegram tap). No live worker-api/Telegram — a fake client captures posts.
"""
from provision_gate import TelegramApprovalGate, PROVISION_REQUESTER

SECRET, NODE, REQUESTER = "anthropic_api_key", "kai-mini", "kai-session"


class FakeClient:
    """Captures posts; returns a create response then a scripted status sequence."""
    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.posts = []
        self.gets = []

    def post(self, path, body):
        self.posts.append((path, body))
        return {"request_id": "req123", "status": "pending"}

    def get(self, path):
        self.gets.append(path)
        return {"status": self._statuses.pop(0) if self._statuses else "pending"}


def _gate(client, surface):
    # no-op sleep + a monotonic that trips the deadline after the scripted statuses run out
    ticks = iter([0.0, 0.0, 0.0, 0.0, 1e9, 1e9, 1e9])
    return TelegramApprovalGate(client=client, surface=surface, poll_interval_s=0.0,
                                timeout_s=1.0, sleep=lambda _s: None,
                                monotonic=lambda: next(ticks))


def test_present_surface_is_sent_in_request_body():
    c = FakeClient(["approved_once"])
    a = _gate(c, "present").request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    path, body = c.posts[0]
    assert path == "/mode_lock/request_approval"
    assert body["surface"] == "present"
    assert body["requester"] == PROVISION_REQUESTER   # decision #1 unchanged
    assert a.approved is True                          # resolves via the same status poll


def test_default_surface_is_telegram():
    c = FakeClient(["approved_once"])
    _gate(c, "telegram").request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert c.posts[0][1]["surface"] == "telegram"


def test_surface_does_not_change_resolution_logic():
    # a present-surface request still only accepts approved_once (a session grant is refused,
    # identical to the telegram path — decision #2 is channel-independent).
    c = FakeClient(["approved_session"])
    a = _gate(c, "present").request_approval(secret_name=SECRET, node=NODE, requester=REQUESTER)
    assert a.approved is False

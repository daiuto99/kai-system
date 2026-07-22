from unittest import mock

from routes import t2


def _entry():
    return {
        "id": "tap12345", "action": "safe hostops summary", "detail": "approve or reject",
        "status": "pending", "gate_id": "gate-123", "kind": "hostops_gate",
        "callback_url": "http://kai-orchestrator:8003/gates/gate-123/resolve",
    }


def test_hostops_tap_resolves_gate_and_never_uses_generic_execution(monkeypatch):
    entry = _entry()
    saved = []
    monkeypatch.setattr(t2, "_t2_load", lambda: [entry])
    monkeypatch.setattr(t2, "_t2_save", lambda queue: saved.append(queue[0].copy()))
    monkeypatch.setattr(t2, "_post_slack_thread", lambda *_: None)
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"resolved": True}
    post = mock.Mock(return_value=response)
    monkeypatch.setattr(t2.httpx, "post", post)

    result = t2.respond_t2_action(t2.T2RespondRequest(action_id="tap12345", approved=True, user_id="leo"))

    assert result["kind"] == "hostops_gate"
    assert result["executed"] is True
    assert entry["status"] == "approved"
    assert saved
    assert post.call_args.args[0] == "http://kai-orchestrator:8003/gates/gate-123/resolve"
    assert post.call_args.kwargs["json"] == {
        "approved": True, "notes": "Approved by Leo via T2 tap", "advisor": "leo",
    }


def test_hostops_rejection_resolves_gate_with_rejection_reason(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(t2, "_t2_load", lambda: [entry])
    monkeypatch.setattr(t2, "_t2_save", lambda _: None)
    monkeypatch.setattr(t2, "_post_slack_thread", lambda *_: None)
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"resolved": True}
    post = mock.Mock(return_value=response)
    monkeypatch.setattr(t2.httpx, "post", post)

    result = t2.respond_t2_action(t2.T2RespondRequest(action_id="tap12345", approved=False, user_id="leo", notes="Not now"))

    assert result["executed"] is True
    assert post.call_args.kwargs["json"] == {"approved": False, "notes": "Not now"}

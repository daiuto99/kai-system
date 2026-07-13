from council_config import ADVISOR_CHANNELS
from router import MessageRequest, _context_assemble_payload


KEY = {
    "advisor": "m1smoke",
    "device": "m1-scoping-test",
    "place": None,
    "thread": None,
}


def test_scoped_message_carries_project_and_task_type_unchanged():
    request = MessageRequest(
        channel="m1smoke",
        message="same message",
        project="alpha/exact",
        task_type="m1.scope:exact",
    )

    assert _context_assemble_payload(request, KEY, "m1smoke") == {
        "key": KEY,
        "message": "same message",
        "channel": "m1smoke",
        "project": "alpha/exact",
        "task_type": "m1.scope:exact",
    }


def test_unscoped_message_omits_scope_without_defaults():
    request = MessageRequest(channel="m1smoke", message="same message")
    payload = _context_assemble_payload(request, KEY, "m1smoke")

    assert payload == {"key": KEY, "message": "same message", "channel": "m1smoke"}
    assert "project" not in payload
    assert "task_type" not in payload


def test_m1_live_fixture_has_dedicated_chat_namespace():
    assert ADVISOR_CHANNELS["m1smoke"] == "m1smoke"

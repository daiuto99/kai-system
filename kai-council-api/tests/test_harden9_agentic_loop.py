"""Failure-layer tests for HARDEN-9's agentic-loop structural brakes."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import sys

sys.path.insert(0, "/app")
import router


def _response(*, tool_use: bool = False, input_tokens: int = 1, output_tokens: int = 1):
    content = [
        SimpleNamespace(type="tool_use", id="call-1", name="noop", input={})
    ] if tool_use else [SimpleNamespace(text="finished")]
    return SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="tool_use" if tool_use else "end_turn",
        content=content,
    )


def _ok_post(*args, **kwargs):
    response = MagicMock()
    response.raise_for_status.return_value = None
    return response


def test_agentic_loop_stops_cleanly_at_iteration_cap():
    client = MagicMock()
    client.messages.create.side_effect = [_response(tool_use=True) for _ in range(20)]
    with patch.object(router, "get_anthropic_client", return_value=client), \
         patch.object(router, "execute_tool", return_value={}), \
         patch.object(router.httpx, "post", side_effect=_ok_post):
        reply, *_ = router._run_agentic_loop([], [{"name": "noop"}], "model", "system", "kai")

    assert reply.startswith("over_budget:")
    assert client.messages.create.call_count == router.MAX_AGENTIC_ITERATIONS


def test_agentic_loop_applies_60_second_timeout_to_llm_call():
    client = MagicMock()
    client.messages.create.return_value = _response()
    with patch.object(router, "get_anthropic_client", return_value=client), \
         patch.object(router.httpx, "post", side_effect=_ok_post):
        router._run_agentic_loop([], [], "model", "system", "kai")

    assert client.messages.create.call_args.kwargs["timeout"] == router.LLM_CALL_TIMEOUT_SECONDS == 60.0


def test_agentic_loop_records_per_iteration_token_telemetry():
    client = MagicMock()
    client.messages.create.return_value = _response(input_tokens=13, output_tokens=21)
    with patch.object(router, "get_anthropic_client", return_value=client), \
         patch.object(router.httpx, "post", side_effect=_ok_post) as post:
        router._run_agentic_loop([], [], "model", "system", "kai")

    payload = post.call_args.kwargs["json"]
    assert payload["iteration"] == 1
    assert payload["input_tokens"] == 13
    assert payload["output_tokens"] == 21


def test_agentic_loop_stops_when_turn_token_budget_is_exceeded():
    client = MagicMock()
    client.messages.create.return_value = _response(input_tokens=router.TURN_TOKEN_BUDGET, output_tokens=1)
    with patch.object(router, "get_anthropic_client", return_value=client), \
         patch.object(router.httpx, "post", side_effect=_ok_post):
        reply, *_ = router._run_agentic_loop([], [], "model", "system", "kai")

    assert reply.startswith("over_budget:")

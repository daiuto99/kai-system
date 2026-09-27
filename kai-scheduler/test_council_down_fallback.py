"""KAI-1513: independent Telegram fallback.

When the council (KAI's brain) is unreachable, the Telegram delivery path must
return an INDEPENDENT status fallback — not a bare error, and not a second call
to the same dead brain. A 4xx (a request problem, e.g. bad channel) must NOT
dump system status. The independent status helper must never raise and always
return a non-empty string (it degrades worker-api -> invariants.json -> notice).

Runs under pytest or plain `python test_council_down_fallback.py`.
"""
import httpx
import scheduler


def _run_with(post_impl, sentinel="SENTINEL-STATUS"):
    captured = {}
    orig_post, orig_status = scheduler.httpx.post, scheduler._independent_status_reply
    scheduler.httpx.post = post_impl
    scheduler._independent_status_reply = lambda: sentinel
    try:
        scheduler.deliver_council_reply(
            "tok", 1, "kai", "hi", "leo", None,
            send=lambda t, c, text: captured.__setitem__("reply", text))
    finally:
        scheduler.httpx.post, scheduler._independent_status_reply = orig_post, orig_status
    return captured["reply"]


def _raise(exc):
    def _p(*a, **k):
        raise exc
    return _p


class _Resp:
    def __init__(self, code):
        self.status_code = code

    def raise_for_status(self):
        raise httpx.HTTPStatusError("e", request=None, response=self)

    def json(self):
        return {}


def test_council_unreachable_delivers_independent_status():
    reply = _run_with(_raise(httpx.ConnectError("down")))
    assert "SENTINEL-STATUS" in reply
    assert "unreachable" in reply.lower()


def test_council_timeout_delivers_independent_status():
    reply = _run_with(_raise(httpx.TimeoutException("slow")))
    assert "SENTINEL-STATUS" in reply


def test_council_5xx_delivers_independent_status():
    reply = _run_with(lambda *a, **k: _Resp(503))
    assert "SENTINEL-STATUS" in reply


def test_council_4xx_keeps_plain_error_without_status_dump():
    reply = _run_with(lambda *a, **k: _Resp(400))
    assert "SENTINEL-STATUS" not in reply
    assert "400" in reply


def test_independent_status_reply_is_council_free_and_nonempty():
    out = scheduler._independent_status_reply()
    assert isinstance(out, str) and out.strip()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("ALL PASS")

"""L18 remediation (Codex review c658b55): kai-scheduler twin of the worker
redaction tests — the scheduler watchdog's check_telegram must redact the bot
token (literal and URL-encoded) from both httpx exception text and reflected
response-body descriptions before they flow into transport status + alerts.

Runs under pytest or plain `python test_token_redaction.py`.
"""
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

FAKE_TOKEN = "1234567:FAKE-token-for-test"
ENCODED = quote(FAKE_TOKEN, safe="")
ENCODED_LOWER = ENCODED.replace("%3A", "%3a")


class DummyResp:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}

    def json(self):
        return self._json


def _assert_clean(s: str):
    assert FAKE_TOKEN not in s, f"literal token leaked: {s!r}"
    assert ENCODED not in s, f"encoded token leaked: {s!r}"
    assert ENCODED_LOWER not in s, f"lowercase-encoded token leaked: {s!r}"


def test_redact_all_token_forms():
    from redact import redact
    text = (f"url 'https://api.telegram.org/bot{FAKE_TOKEN}/getMe' "
            f"reflected as bot{ENCODED} and bot{ENCODED_LOWER}")
    out = redact(text, FAKE_TOKEN)
    _assert_clean(out)
    assert out.count("[REDACTED]") == 3
    assert redact("plain message", "") == "plain message"


def test_watchdog_exception_redacted():
    import watchdog as w
    orig_load, orig_get = w._load_secret, w.httpx.get

    def _boom(url, timeout=None):
        raise RuntimeError(f"boom for url '{url}'")

    w._load_secret = lambda name: FAKE_TOKEN
    w.httpx.get = _boom
    try:
        ok, detail = w.check_telegram()
        assert ok is False
        _assert_clean(detail)
        assert "[REDACTED]" in detail
    finally:
        w._load_secret, w.httpx.get = orig_load, orig_get


def test_watchdog_response_description_redacted():
    import watchdog as w
    orig_load, orig_get = w._load_secret, w.httpx.get
    w._load_secret = lambda name: FAKE_TOKEN
    w.httpx.get = lambda url, timeout=None: DummyResp(
        404, {"ok": False,
              "description": f"Not Found: https://api.telegram.org/bot{ENCODED}/getMe"}
    )
    try:
        ok, detail = w.check_telegram()
        assert ok is False
        _assert_clean(detail)
        assert "[REDACTED]" in detail
    finally:
        w._load_secret, w.httpx.get = orig_load, orig_get


TESTS = [
    test_redact_all_token_forms,
    test_watchdog_exception_redacted,
    test_watchdog_response_description_redacted,
]

if __name__ == "__main__":
    for t in TESTS:
        t()
    print(f"PASS: {len(TESTS)}/{len(TESTS)} scheduler token redaction tests")

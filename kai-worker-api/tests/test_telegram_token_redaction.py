"""KAI-791 / L18 remediation (Codex review c658b55): the Telegram bot token
must never appear in log lines or returned error strings — in literal OR
URL-encoded form, whether it arrives via httpx exception text (request URL)
or via an upstream response body reflecting that URL back.

These tests drive every boundary the review flagged: redact() encoded forms,
/telegram/status response text, register-webhook transport + body paths,
clarification-surface body path, and watchdog getMe description path.

Runs under pytest or plain `python tests/test_telegram_token_redaction.py`.
"""
import logging
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAKE_TOKEN = "1234567:FAKE-token-for-test"
ENCODED = quote(FAKE_TOKEN, safe="")          # 1234567%3AFAKE-token-for-test
ENCODED_LOWER = ENCODED.replace("%3A", "%3a")


class DummyResp:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._json


class _CaptureLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


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
    # Empty token: no-op, no crash
    assert redact("plain message", "") == "plain message"


def test_routes_telegram_redact():
    from routes import telegram as t
    orig = t._tg_token
    t._tg_token = lambda: FAKE_TOKEN
    try:
        err = Exception(
            f"ConnectError for url 'https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage'"
        )
        out = t._redact(err)
        _assert_clean(out)
        assert "[REDACTED]" in out
        # Encoded-only exception text is also caught
        _assert_clean(t._redact(f"reflected bot{ENCODED} in body"))
        # Empty token must not redact (and must not crash)
        t._tg_token = lambda: ""
        assert t._redact("plain message") == "plain message"
    finally:
        t._tg_token = orig


def test_status_response_body_redacted():
    from routes import telegram as t
    orig_token, orig_get = t._tg_token, t._tghttpx.get
    t._tg_token = lambda: FAKE_TOKEN
    t._tghttpx.get = lambda url, timeout=None: DummyResp(
        404, text=f"Not Found: https://api.telegram.org/bot{ENCODED}/getMe ({FAKE_TOKEN})"
    )
    try:
        out = t.telegram_status()
        assert out["configured"] is False
        _assert_clean(out["error"])
        assert "[REDACTED]" in out["error"]
    finally:
        t._tg_token, t._tghttpx.get = orig_token, orig_get


def test_register_webhook_transport_error_redacted():
    from fastapi import HTTPException
    from routes import telegram as t

    def _boom(url, json=None, timeout=None):
        raise RuntimeError(
            f"ConnectError for url 'https://api.telegram.org/bot{FAKE_TOKEN}/setWebhook'"
        )

    orig_token, orig_post = t._tg_token, t._tghttpx.post
    t._tg_token = lambda: FAKE_TOKEN
    t._tghttpx.post = _boom
    try:
        try:
            t.telegram_register_webhook({"url": "https://example.com/hook"})
            raise AssertionError("expected HTTPException")
        except HTTPException as e:
            _assert_clean(str(e.detail))
            assert "[REDACTED]" in str(e.detail)
    finally:
        t._tg_token, t._tghttpx.post = orig_token, orig_post


def test_register_webhook_response_body_redacted():
    from routes import telegram as t
    orig_token, orig_post = t._tg_token, t._tghttpx.post
    t._tg_token = lambda: FAKE_TOKEN
    t._tghttpx.post = lambda url, json=None, timeout=None: DummyResp(
        200, {"ok": False,
              "description": f"rejected: https://api.telegram.org/bot{ENCODED}/setWebhook"}
    )
    try:
        out = t.telegram_register_webhook({"url": "https://example.com/hook"})
        assert out["ok"] is False
        _assert_clean(str(out))
        assert "[REDACTED]" in out["description"]
    finally:
        t._tg_token, t._tghttpx.post = orig_token, orig_post


def test_clarification_body_redacted():
    import clarification_surface as cs
    entry = {"id": "test-pending", "origin_chat_id": "123"}
    clar = {"prompt": "Pick one:", "options": ["a", "b"]}
    orig_token, orig_post = cs._telegram_token, cs.httpx.post
    cs._telegram_token = lambda: FAKE_TOKEN
    cs.httpx.post = lambda url, json=None, timeout=None: DummyResp(
        200, {"ok": False,
              "description": f"reflected https://api.telegram.org/bot{ENCODED}/sendMessage"}
    )
    cap = _CaptureLog()
    cs.logger.addHandler(cap)
    try:
        out = cs._ask_telegram(entry, clar, Path("/tmp/does-not-exist"))
        assert out["ok"] is False
        _assert_clean(out["detail"])
        assert "[REDACTED]" in out["detail"]
        for line in cap.lines:
            _assert_clean(line)
    finally:
        cs.logger.removeHandler(cap)
        cs._telegram_token, cs.httpx.post = orig_token, orig_post


def test_watchdog_check_telegram_redacts():
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


def test_status_success_result_redacted():
    from routes import telegram as t
    orig_token, orig_get = t._tg_token, t._tghttpx.get
    t._tg_token = lambda: FAKE_TOKEN
    t._tghttpx.get = lambda url, timeout=None: DummyResp(
        200, {"ok": True,
              "result": {"username": f"kai_bot_{ENCODED}",
                         "notes": [f"reflected bot{FAKE_TOKEN}"]}}
    )
    try:
        out = t.telegram_status()
        assert out["configured"] is True
        _assert_clean(str(out["bot"]))
        assert "[REDACTED]" in str(out["bot"])
    finally:
        t._tg_token, t._tghttpx.get = orig_token, orig_get


def test_register_webhook_success_result_redacted():
    from routes import telegram as t
    orig_token, orig_post = t._tg_token, t._tghttpx.post
    t._tg_token = lambda: FAKE_TOKEN
    t._tghttpx.post = lambda url, json=None, timeout=None: DummyResp(
        200, {"ok": True,
              "result": f"webhook set for bot{ENCODED}",
              "description": "Webhook was set"}
    )
    try:
        out = t.telegram_register_webhook({"url": "https://example.com/hook"})
        assert out["ok"] is True
        _assert_clean(str(out))
        assert "[REDACTED]" in str(out["result"])
    finally:
        t._tg_token, t._tghttpx.post = orig_token, orig_post


def test_watchdog_success_result_redacted():
    import watchdog as w
    orig_load, orig_get = w._load_secret, w.httpx.get
    w._load_secret = lambda name: FAKE_TOKEN
    w.httpx.get = lambda url, timeout=None: DummyResp(
        200, {"ok": True, "result": {"username": f"kai_bot_{ENCODED}"}}
    )
    try:
        ok, detail = w.check_telegram()
        assert ok is True
        _assert_clean(detail)
        assert "[REDACTED]" in detail
    finally:
        w._load_secret, w.httpx.get = orig_load, orig_get


TESTS = [
    test_redact_all_token_forms,
    test_routes_telegram_redact,
    test_status_response_body_redacted,
    test_register_webhook_transport_error_redacted,
    test_register_webhook_response_body_redacted,
    test_clarification_body_redacted,
    test_watchdog_check_telegram_redacts,
    test_watchdog_response_description_redacted,
    test_status_success_result_redacted,
    test_register_webhook_success_result_redacted,
    test_watchdog_success_result_redacted,
]

if __name__ == "__main__":
    for t in TESTS:
        t()
    print(f"PASS: {len(TESTS)}/{len(TESTS)} telegram token redaction tests")

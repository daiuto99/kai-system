"""KAI-791 (L18): the Telegram bot token must never appear in log lines or
returned error strings. httpx exception text embeds the request URL, which
carries the token — these tests drive the redaction at the layer where the
leak lived (routes.telegram._redact, watchdog.check_telegram).

Runs under pytest or plain `python tests/test_telegram_token_redaction.py`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAKE_TOKEN = "1234567:FAKE-token-for-test"


def test_routes_telegram_redact():
    from routes import telegram as t
    orig = t._tg_token
    t._tg_token = lambda: FAKE_TOKEN
    try:
        err = Exception(
            f"ConnectError for url 'https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage'"
        )
        out = t._redact(err)
        assert FAKE_TOKEN not in out
        assert "[REDACTED]" in out
        # Empty token must not redact (and must not crash)
        t._tg_token = lambda: ""
        assert t._redact("plain message") == "plain message"
    finally:
        t._tg_token = orig


def test_watchdog_check_telegram_redacts():
    import watchdog as w
    orig_load, orig_get = w._load_secret, w.httpx.get
    w._load_secret = lambda name: FAKE_TOKEN

    def _boom(url, timeout=None):
        raise RuntimeError(f"boom for url '{url}'")

    w.httpx.get = _boom
    try:
        ok, detail = w.check_telegram()
        assert ok is False
        assert FAKE_TOKEN not in detail
        assert "[REDACTED]" in detail
    finally:
        w._load_secret, w.httpx.get = orig_load, orig_get


if __name__ == "__main__":
    test_routes_telegram_redact()
    test_watchdog_check_telegram_redacts()
    print("PASS: 2/2 telegram token redaction tests")

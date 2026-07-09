"""S5R-7: Slack HMAC + Telegram fail-closed auth tests."""
import hashlib
import hmac
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _compute_slack_sig(secret: str, ts: str, body: bytes) -> str:
    base = f"v0:{ts}:{body.decode('utf-8', errors='replace')}".encode()
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _verify_slack_sig(secret_override, raw_body: bytes, ts: str, sig: str) -> bool:
    if not secret_override:
        return False
    try:
        if abs(time.time() - float(ts)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    base = f"v0:{ts}:{raw_body.decode('utf-8', errors='replace')}".encode()
    expected = "v0=" + hmac.new(secret_override.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def test_slack_fail_closed_when_no_secret():
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _compute_slack_sig("real_secret", ts, body)
    assert _verify_slack_sig("", body, ts, sig) is False


def test_slack_valid_sig_passes():
    secret = "test_secret_abc"
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    sig = _compute_slack_sig(secret, ts, body)
    assert _verify_slack_sig(secret, body, ts, sig) is True


def test_slack_wrong_sig_rejected():
    secret = "test_secret_abc"
    ts = str(int(time.time()))
    body = b'{"type":"event_callback"}'
    assert _verify_slack_sig(secret, body, ts, "v0=badhash") is False


def test_slack_stale_timestamp_rejected():
    secret = "test_secret_abc"
    ts = str(int(time.time()) - 301)
    body = b'{"type":"event_callback"}'
    sig = _compute_slack_sig(secret, ts, body)
    assert _verify_slack_sig(secret, body, ts, sig) is False


def test_slack_future_timestamp_rejected():
    secret = "test_secret_abc"
    ts = str(int(time.time()) + 301)
    body = b'{"type":"event_callback"}'
    sig = _compute_slack_sig(secret, ts, body)
    assert _verify_slack_sig(secret, body, ts, sig) is False


def test_slack_malformed_timestamp_rejected():
    secret = "test_secret_abc"
    body = b'{"type":"event_callback"}'
    assert _verify_slack_sig(secret, body, "notanumber", "v0=anything") is False


def test_slack_empty_timestamp_rejected():
    secret = "test_secret_abc"
    body = b'{"type":"event_callback"}'
    assert _verify_slack_sig(secret, body, "", "v0=anything") is False


def _tg_guard(secret: str, presented: str) -> int:
    if not secret:
        return 503
    if presented != secret:
        return 403
    return 200


def test_telegram_fail_closed_no_secret():
    assert _tg_guard("", "anything") == 503


def test_telegram_wrong_token_rejected():
    assert _tg_guard("correct", "wrong") == 403


def test_telegram_correct_token_passes():
    assert _tg_guard("correct", "correct") == 200


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    exit(failed)

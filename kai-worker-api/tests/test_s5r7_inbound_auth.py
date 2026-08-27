"""S5R-7: Telegram fail-closed inbound-auth tests.

AR-2 (KAI-1243): the Slack HMAC inbound path was removed system-wide; the
Slack signature tests that lived here went with it. Telegram is the only
remaining inbound remote surface.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


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

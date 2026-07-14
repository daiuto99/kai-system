import logging
from pathlib import Path
import tempfile

import scheduler


def test_missing_or_empty_allowlist_denies_non_bootstrap():
    assert scheduler._allowed_telegram_chat_ids(Path("/does/not/exist")) == frozenset()
    assert not scheduler._telegram_sender_allowed(42, "hello", frozenset())
    assert scheduler._telegram_sender_allowed(42, "/chatid", frozenset())


def test_only_listed_chat_is_allowed():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "allowed"
        path.write_text("123\n")
        allowed = scheduler._allowed_telegram_chat_ids(path)
    assert scheduler._telegram_sender_allowed(123, "hello", allowed)
    assert not scheduler._telegram_sender_allowed(999, "hello", allowed)


def test_httpx_request_logging_is_suppressed_at_runtime():
    # httpx emits complete request URLs at INFO; Telegram embeds the bot token.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING

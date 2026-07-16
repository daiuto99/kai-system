"""L18 token-aware sanitizer — the single redaction boundary for secret-bearing
text (Telegram bot token in request URLs and reflected response bodies).

Covers the literal token and its URL-encoded spellings (uppercase and
lowercase percent-escapes): httpx exception text embeds the request URL, and
an upstream response body may reflect that URL back encoded. Apply at EVERY
log, returned-detail, and exception boundary that touches a token-bearing
request — never log or return upstream text raw.

kai-scheduler/redact.py mirrors this file (separate image build contexts,
same idiom as the twin watchdogs) — edit both in the same commit.
"""
import re
from urllib.parse import quote

PLACEHOLDER = "[REDACTED]"


def token_forms(token: str) -> set[str]:
    """Every spelling of `token` that can appear in URLs, exception text, or
    reflected response bodies: literal, %XX-encoded, %xx-encoded."""
    if not token:
        return set()
    encoded = quote(token, safe="")
    return {
        token,
        encoded,
        re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), encoded),
    }


def redact(obj: object, *tokens: str) -> str:
    """str(obj) with every known spelling of every token replaced."""
    s = str(obj)
    for token in tokens:
        for form in sorted(token_forms(token), key=len, reverse=True):
            s = s.replace(form, PLACEHOLDER)
    return s

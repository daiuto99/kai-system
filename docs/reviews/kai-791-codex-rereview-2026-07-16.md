# KAI-791 — Codex re-review (2026-07-16)

**Review ticket:** `28c47800-52a5-424d-afd4-b5bf090e14e3`
**Remediation ticket:** `a8dec8b6-5b9b-4bfa-b7dc-4b29931d40b6`
**Builder / reviewer independence:** Claude built `435f4a3`; Codex re-reviewed it.
**Verdict:** **FAIL** — most prior L18 boundaries are corrected, but token-bearing reflected values in successful Telegram `result` payloads still return unredacted.

## Scope and method

Reviewed worker-repository commit `435f4a3` in the authoritative deploy root and repeated the original response/exception probes inside the deployed `kai-worker-api` and `kai-scheduler` images. Performed a risk-class sweep of every Telegram URL construction across both services and reviewed its log/return boundary.

## Per-blocker disposition

### Prior blocker 1 — encoded exception text: FIXED

`kai-worker-api/redact.py:15-38` and its scheduler mirror enumerate literal, upper-case percent-encoded, and lower-case percent-encoded token forms. `routes/telegram.py:27-33` delegates `_redact()` to that shared function. The deployed independent probe reported:

```text
encoded_exception_redacted= True
```

The worker and scheduler tests also pin this sanitizer (`kai-worker-api/tests/test_telegram_token_redaction.py:50-76`; `kai-scheduler/test_token_redaction.py:34-41`). Deleting the shared redaction or the route delegation makes their placeholder/clean assertions fail.

### Prior blocker 2 — error response bodies: FIXED for the formerly leaking error paths

The following former raw-body paths now redact descriptions/text before a log or caller-visible detail:

- `/telegram/status` error text: `kai-worker-api/routes/telegram.py:253-257`
- clarification log/detail: `kai-worker-api/clarification_surface.py:211-217`
- worker watchdog description/exception: `kai-worker-api/watchdog.py:127-132`
- scheduler watchdog description/exception: `kai-scheduler/watchdog.py:238-243`

Independent deployed probes all passed:

```text
status_response_redacted= True
clarification_response_redacted= True
worker_watchdog_response_redacted= True
scheduler_watchdog_response_redacted= True
```

### Prior blocker 2 — still BLOCKING for successful reflected `result` bodies

The remediation sanitizes only error-oriented fields, then returns successful remote `result` data raw:

- `kai-worker-api/routes/telegram.py:250-252` returns `safe_json(r).get("result", {})` as `bot`.
- `kai-worker-api/routes/telegram.py:277-280` returns `resp.get("result")` unredacted from webhook registration.
- `kai-worker-api/watchdog.py:125-126` and `kai-scheduler/watchdog.py:236-237` interpolate `data["result"]["username"]` unredacted into transport details.

A deployed-image probe supplied a synthetic URL-encoded reflected value in each successful `result` shape. It reported:

```text
status_success_result_redacted= False
webhook_success_result_redacted= False
worker_watchdog_success_result_redacted= False
scheduler_watchdog_success_result_redacted= False
```

This keeps the original response-body risk class open: the boundary is not failure-impossible when a remote response reflects the token in a successful payload. The existing suites only model the error-body shapes, so they remain green despite these leaks.

### Prior blocker 3 — webhook transport exception can no longer escape unredacted: FIXED

`kai-worker-api/routes/telegram.py:266-276` catches the token-bearing `setWebhook` transport exception, logs a sanitized value, and raises an explicit HTTP 502 whose detail is sanitized. The deployed probe reported:

```text
webhook_transport_boundary= True
```

The corresponding regression test at `kai-worker-api/tests/test_telegram_token_redaction.py:97-117` fails if that transport boundary or its redaction is deleted, because it requires a sanitized `HTTPException` detail.

## Test adequacy

Both requested plain-Python runners execute successfully in their deployed images:

```text
telegram register-webhook: ConnectError for url 'https://api.telegram.org/bot[REDACTED]/setWebhook'
PASS: 8/8 telegram token redaction tests
PASS: 3/3 scheduler token redaction tests
```

The 11 tests meaningfully pin literal/encoded sanitizer behavior, route-level status error text, webhook transport/detail and error description, clarification log/detail, and both watchdog exception/error-description paths. Each would fail if the redaction it exercises were removed.

**Blocking test gap:** no test supplies a reflected token form in a successful `result` payload for status, webhook registration, or either watchdog; the deployed negative probes above prove that gap is real.

**Non-blocking test gaps:** `_tg_send` (`routes/telegram.py:36-47`), callback acknowledgement (`80-88`), and file-download failures (`112-124`, caught by the redacted attachment handlers) are not directly regression-tested. Static review finds their exception logs use `_redact()` or flow to an outer `_redact()` handler and they do not return Telegram response bodies, so this is coverage debt rather than an additional demonstrated leak.

## Deployed-code verification

Both relevant containers were rebuilt and restarted around 2026-07-16 14:41 UTC. SHA-256 comparisons between `git show 435f4a3:<path>` and the files in the running containers matched exactly:

```text
kai-worker-api/redact.py                              match
kai-worker-api/routes/telegram.py                    match
kai-worker-api/clarification_surface.py              match
kai-worker-api/watchdog.py                           match
kai-worker-api/tests/test_telegram_token_redaction.py match
kai-scheduler/redact.py                              match
kai-scheduler/watchdog.py                            match
kai-scheduler/test_token_redaction.py                match
```

The findings are against deployed code from `435f4a3`, not source/deploy drift.

## Required remediation before approval

Sanitize or strictly project each successful Telegram `result` before it crosses a log, transport-status, or HTTP response boundary. Add regression tests for reflected literal and encoded token forms in status success, webhook success, and both watchdog success payloads. Rebuild, rerun both image-local runners, and request a further independent re-review.

# KAI-791 — Codex second re-review (2026-07-16)

**Review ticket:** `28c47800-52a5-424d-afd4-b5bf090e14e3`
**Remediation ticket:** `a8dec8b6-5b9b-4bfa-b7dc-4b29931d40b6`
**Builder / reviewer independence:** Claude built `e9359d0`; Codex independently reviewed it.
**Verdict:** **PASS-WITH-FINDINGS** — all previously demonstrated L18 leaks are closed in deployed code. Remaining findings are non-blocking direct-test coverage gaps for already-sanitized exception paths.

## Scope and method

Reviewed worker-repository commit `e9359d0` in `/home/leo/kai-system`; reran the former success-result probes and the prior error/exception/transport probes inside deployed images; and swept all Telegram URL construction, response-body/result consumption, and exception log/return boundaries in `kai-worker-api` and `kai-scheduler`.

## Disposition

### A. Former success-result blocker — FIXED

`redact_obj()` recursively sanitizes strings in dictionaries and lists in both image-local sanitizer modules (`kai-worker-api/redact.py:41-51`, mirrored at `kai-scheduler/redact.py:41-51`). It is applied to both returned worker API result structures:

- `/telegram/status` bot result: `kai-worker-api/routes/telegram.py:249-256`
- webhook-registration result: `kai-worker-api/routes/telegram.py:279-284`

Both watchdogs redact the success `bot=@...` transport detail before it can reach status/alerts:

- `kai-worker-api/watchdog.py:123-133`
- `kai-scheduler/watchdog.py:234-244`

An independent deployed-image probe inserted literal, upper-case encoded, and lower-case encoded synthetic values into nested successful result payloads. All four probes passed:

```text
status_success_literal_encoded_redacted= True
webhook_success_literal_encoded_redacted= True
worker_watchdog_success_literal_encoded_redacted= True
scheduler_watchdog_success_literal_encoded_redacted= True
```

### B. Prior error, exception, and webhook-transport dispositions — still FIXED

Independent deployed probes reconfirmed redaction at every prior boundary:

```text
status_error_redacted= True
webhook_transport_redacted= True
clarification_error_redacted= True
worker_watchdog_error_redacted= True
scheduler_watchdog_error_redacted= True
worker_watchdog_exception_redacted= True
scheduler_watchdog_exception_redacted= True
```

The webhook transport error is contained at `kai-worker-api/routes/telegram.py:268-278`: it logs and raises only a sanitized HTTP 502 detail, preventing a token-bearing exception from reaching FastAPI/Uvicorn handling. Clarification error-body log/detail remains sanitized at `kai-worker-api/clarification_surface.py:211-217`.

### C. New regression tests — ADEQUATE for the former blocker

The four new success-payload tests are:

- worker status result: `kai-worker-api/tests/test_telegram_token_redaction.py:196-213`
- worker webhook result: `214-231`
- worker watchdog success detail: `234-250`
- scheduler watchdog success detail: `kai-scheduler/test_token_redaction.py:79-95`

Each mocks a reflected encoded value and asserts that it is absent and `[REDACTED]` is present. The status test additionally contains a nested list, exercising recursion. Deleting the applicable `redact_obj()`/`redact()` call causes the test that drives that boundary to fail. This closes the exact regression gap found in the first re-review.

### D. Deployed-code verification — PASS

Worker API and scheduler containers/images were rebuilt around 2026-07-16 14:57–14:58 UTC. SHA-256 comparisons between `git show e9359d0:<path>` and files in the running containers matched for both sanitizer modules, both watchdogs, worker Telegram route and clarification surface, and both test files. The review therefore concerns deployed `e9359d0` code, not source/deploy drift.

### E. Completeness sweep — PASS with one non-blocking test finding

The sweep enumerated every Telegram URL construction in the worker API and scheduler. All token-bearing exception paths reaching a log, transport-status value, or HTTP response use a sanitizer:

- send and callback errors: `kai-worker-api/routes/telegram.py:36-47`, `80-88`
- file-download errors: `112-124`, caught and sanitized at `172-206`
- status/webhook response and exception boundaries: `243-284`
- clarification response boundary: `199-217`
- both watchdog response and exception boundaries: locations above
- scheduler polling/send errors are type-only or do not expose Telegram response bodies.

No remaining unredacted Telegram result/body/exception path was found at a log, transport-status, or HTTP response boundary.

**NON-BLOCKING finding:** `_tg_send`, callback acknowledgement, and file-download exception paths are structurally sanitized but still lack direct regression tests. Add compact failure-layer tests for these boundaries in a future coverage task; no demonstrated secret leak remains.

## Test output

Both requested plain-Python runners executed inside their deployed images:

```text
telegram register-webhook: ConnectError for url 'https://api.telegram.org/bot[REDACTED]/setWebhook'
PASS: 11/11 telegram token redaction tests
PASS: 4/4 scheduler token redaction tests
```

## Conclusion

The original failure class — literal/encoded token propagation through exception text and reflected error or successful response bodies — is now structurally covered at all enumerated output boundaries and held by deployed regression tests. Approval is appropriate with the non-blocking direct-test coverage follow-up noted above.

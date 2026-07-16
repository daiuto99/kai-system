# KAI-791 — Codex independent review (2026-07-16)

**Review ticket:** `28c47800-52a5-424d-afd4-b5bf090e14e3`
**Builder / reviewer independence:** Claude built `41bf34b`; Codex reviewed it.
**Verdict:** **FAIL** — L18 is not complete. Token-shaped data still reaches log/returned-detail paths through URL-encoded and response-body variants.

## Scope and method

Reviewed worker-repository commit `41bf34b` and the requested services:

- `kai-scheduler/scheduler.py`, `kai-scheduler/watchdog.py`
- `kai-slack-bot/main.py`
- `kai-worker-api/routes/telegram.py`, `clarification_surface.py`, `scheduler.py`, `watchdog.py`
- `kai-worker-api/tests/test_telegram_token_redaction.py`

Performed a whole-worker-repository static sweep for `httpx` calls, exception logging, f-string exception interpolation, and every Telegram URL/token construction. Telegram request paths were found in both scheduler implementations, both watchdogs, the clarification surface, and the worker Telegram route. The route itself no longer has `logger.exception` call sites, but several uncovered paths bypass `_redact()` entirely.

## Findings

### BLOCKING — `_redact()` handles only the literal token form

`kai-worker-api/routes/telegram.py:26-33` converts the input to `str()` and calls only `s.replace(token, "[REDACTED]")`. It does not normalize or redact URL-encoded token forms. A runtime probe inside the deployed `kai-worker-api` image reported:

```text
encoded_placeholder_present= False
encoded_token_form_preserved= True
```

The same direct probe showed ordinary string and repr-shaped text are redacted, but that does not establish the encoded form required by the review question. The failure mode is therefore not demonstrably impossible.

### BLOCKING — Telegram response bodies bypass redaction and can flow to logs/callers

The commit redacts selected exception text but returns or logs raw remote response content in multiple Telegram paths:

- `kai-worker-api/routes/telegram.py:253` returns `r.text[:200]` from `/telegram/status` without `_redact()`.
- `kai-worker-api/clarification_surface.py:210-214` logs the complete Telegram response body and returns `body["description"]` in `detail` without redaction.
- `kai-worker-api/watchdog.py:125` and `kai-scheduler/watchdog.py:237` return Telegram `data["description"]` directly. Those details feed transport status and alert paths.

Deployed-image probes, using a non-secret synthetic value in a mocked Telegram response, reported:

```text
status_body_redacted= False
clarification_body_redacted= False
worker_watchdog_response_body_redacted= False
scheduler_watchdog_response_body_redacted= False
```

This is a direct response-body bypass. L18 must treat untrusted upstream response text as potentially reflective; relying on Telegram's normal error wording does not meet the stated failure-impossible bar.

### BLOCKING — webhook registration has no redaction boundary on its transport-error path

`kai-worker-api/routes/telegram.py:259-270` issues the token-bearing `setWebhook` request without a `try`/`except` redaction boundary. A transport exception can propagate to FastAPI/Uvicorn error handling rather than being reduced to a token-free type/detail. This path was not covered by the new test file and is contrary to the `_redact()` docstring's stated requirement that neither logs nor caller tracebacks repeat an unredacted request message.

### NON-BLOCKING — channel error replies are substantially more honest and token-free at reviewed council call sites

The new Slack and scheduler council handlers report a bounded timeout, HTTP status, or exception class rather than interpolating the exception (`kai-slack-bot/main.py:110-123`; `kai-scheduler/scheduler.py:240-253`; `kai-worker-api/routes/telegram.py:225-237`). These replies expose no bot token and only routine internal service information. This part is an improvement.

The 180-second timeout is bounded, so it is not an unbounded hang. However, its justification is only an inline assertion (`kai-slack-bot/main.py:15-17`, `kai-scheduler/scheduler.py:50-53`): this review found no regression test, duration evidence, or server-side job/status mechanism demonstrating that 180 seconds distinguishes legitimate council work from a stuck request. Treat the increase as unverified design rationale, not proof of correctness.

### NON-BLOCKING — the two tests pin only two raw exception paths

`tests/test_telegram_token_redaction.py` would fail if the route's `_redact()` or the worker API watchdog's exception replacement were deleted, so both tests provide useful direct regression coverage. They do **not** cover:

- URL-encoded token forms;
- response bodies returned by `/telegram/status`, clarification delivery, or either watchdog;
- the scheduler watchdog duplicate;
- `_tg_send`, callback, file-download, or webhook-registration transport paths;
- the new timeout/HTTP/unreachable user replies.

The test is executable as its own documented plain-Python script in the deployed image. `pytest` is not installed in that image, so a pytest invocation cannot run there.

### NON-BLOCKING — commit contains one out-of-scope log artifact

`git show --name-status 41bf34b` includes `logs/orchestrator_backup.log`, which is not among the stated KAI-791 source/test files. A content scan reported no Telegram-token-shaped or generic credential-marker match, but generated logs should not be mixed into a focused security fix without an explicit reason.

## Evidence

### Requested test

Host execution lacks the worker image dependencies (`fastapi` is absent), so the test was run in the deployed service image:

```text
$ docker compose exec -T kai-worker-api python tests/test_telegram_token_redaction.py
PASS: 2/2 telegram token redaction tests

$ docker compose exec -T kai-worker-api pytest -q tests/test_telegram_token_redaction.py
exec: "pytest": executable file not found in $PATH
```

### Deployed-code verification

`docker compose ps` showed `kai-scheduler`, `kai-slack-bot`, and `kai-worker-api` running. Their containers and images were created on 2026-07-16 around 12:49 UTC. SHA-256 comparisons matched exactly for all seven requested production source files and the test file between `git show 41bf34b:<path>` and the running containers:

```text
kai-scheduler/scheduler.py                 match
kai-scheduler/watchdog.py                  match
kai-slack-bot/main.py                      match
kai-worker-api/routes/telegram.py          match
kai-worker-api/clarification_surface.py    match
kai-worker-api/scheduler.py                match
kai-worker-api/watchdog.py                 match
kai-worker-api/tests/test_telegram_token_redaction.py  match
```

The deployed containers therefore run the reviewed code; this is not a mirror-only or deployed-but-uncommitted drift finding.

## Required remediation before approval

Use one shared, token-aware sanitizer at every log, returned-detail, and exception boundary for Telegram calls. It must redact literal and URL-encoded forms and be applied to response text/JSON before logging or returning it. Add failure-layer regression tests covering encoded exception text, reflected response bodies for every status/clarification/watchdog path, and the unhandled webhook-registration transport error. Re-run the test suite in an environment that includes its declared runner before re-review.

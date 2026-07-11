# Internal-auth class fix — Recovery Plan Step 1, trust repair #1

**Date:** 2026-07-11
**Tickets:** KAI-739 `48f85706` (BasicAuthMiddleware blocks internal /system/ops-state)
· `aec2d486` (persona_assembly 401 cross-service auth gap)
**Status:** Implemented + verified to the durability bar. Codex independent
review (rule #9, defensive security) is the next step before fully trusted.

## Design decision (LOCKED — do not re-ask)

**Option 1: callers send credentials.** Internal service-to-service callers
attach the worker Basic-auth credential they already hold as a Docker secret to
every call to `kai-worker-api`. Every worker route stays authenticated — no
network-origin bypass, no growing `_NO_AUTH` exempt list, no exempting
`/internal/*` route families. This supersedes the earlier musings in the two
tickets (ticket `aec2d486` had leaned toward "exempt /internal/invariants/*",
which is rejected).

## Root cause (confirmed)

`kai-worker-api/main.py::BasicAuthMiddleware` authenticates **every** route
except a tiny `_NO_AUTH` set (`/health`, `/github/webhook`, `/slack/events`,
`/telegram/webhook`, `/mode_lock/slack_callback`). The worker credential
(`secrets/kai_worker_auth.txt`) was introduced ~2026-07-09. **No internal
caller was ever updated to send it**, so the entire internal-caller class
started returning 401 at once. `/system/ops-state` (Tier 5 `system_state`) and
`persona_assembly` were merely the symptoms the invariant board surfaced.

Correction to ticket `aec2d486`'s stated root cause: `kai-council-api` has **no
auth middleware**. The `persona_assembly` 401 is **not** scheduler→council; it
is downstream — council `/internal/invariants/persona_check` → orchestrator
`/context/persona` → `context_service._tier5_system_state()` → worker
`/system/ops-state` **401** → degraded-empty block → warning → invariant FAIL.

## The whole class — call sites found and fixed (Pattern-5 guard)

Enumerated by AST scan (`kai-worker-api/tests/test_internal_auth_guard.py`):
**57 internal worker call sites verified authenticated, 0 violations.**

| Service | Mechanism | Call sites |
|---|---|---|
| kai-council-api `execute_tool.py` | shared `httpx.Client(auth=_worker_auth())` — all tool calls inherit | ~40 (`/tasks*`, `/projects*`, `/vault/*`, `/workspace/*`, `/calendar/*`, `/contacts*`, `/t2/queue`, `/assets/deliver`, `/workflows`, `/slack/channels`, `/system/ops-state`, `/system/run-backup`, `/system/restore-cron`, `/parking-lot/quick`, `/templates`, `/council/advisor/*/recent_dms`) |
| kai-council-api `router.py` | per-call auth | `/parking-lot/quick` |
| kai-council-api `tools/tasks_tools.py` | per-call auth | `/tasks` |
| kai-orchestrator `context_service.py` | per-call auth | `/system/ops-state` ← **aec2d486 root** (Tier 5 system_state) |
| kai-scheduler `scheduler.py` | per-call + authed client `hc` | `/system/health`, `/checkin/send`, `/inbox/scan`, `/sprint-a/expire-stale` |
| kai-scheduler `watchdog.py` | per-call auth | `/calendar/ics` |
| kai-slack-bot `main.py` | per-call auth | `/mode_lock/slack_action_internal`, `/t2/respond`, `/intake/scan`, `/intake/active/creative`, `/intake/reply/creative` |
| kai-mcp-api `main.py` | per-call auth at `_call_worker` choke point | `/calendar/events`, `/tasks`, `/vault/read`, `/knowledge/sessions` |
| kai-worker-api `watchdog.py` | per-call auth (self-check) | `/calendar/ics` |

`_NO_AUTH` endpoints (`/health` x2, `/slack/events` webhook) correctly left
unauthenticated — verified exempt by the guard.

### Credential availability
`kai_worker_auth` added as a top-level Docker secret and wired into all six
caller services' `secrets:` lists → uniform `/run/secrets/kai_worker_auth`.
Each service has a `_worker_auth()` / `worker_auth()` loader (reads
`/run/secrets` first, then bind-mount fallbacks). Scheduler needed a shared
`worker_auth.py` module (3 consumers) + `COPY worker_auth.py` in its Dockerfile.

## Regression guards (defense in depth — "can't silently un-fix")

1. **Runtime invariant** `inv_internal_worker_auth` (kai-scheduler): board goes
   RED if the worker stops enforcing auth (unauth ≠ 401) **or** the credential
   path breaks (authed ≠ 200). Catches middleware-disable / origin-bypass and
   lost-mount regressions.
2. **Static AST guard** `test_internal_auth_guard.py`: fails the build if any
   internal worker call omits auth (per-call `auth=` or authed client), with a
   `_NO_AUTH` allowlist. Catches new bare call sites at author time. Also
   asserts the council shared client is created authed (guards the ~40 inherited
   handler calls).

## Riders folded in (auth/secrets neighborhood)

- **L8**: 5 secret files `664 → 600` (`todoist_api_key`, `slack_signing_secret`,
  `oura_token`, `plane_api_token`, `litellm_master_key`).

## Evidence (live, post-deploy)

```
[GREEN] inv_persona_assembly: ok — 9 advisors, all required blocks present, no warnings
[GREEN] inv_secret_files_permissions: ok — 10 secret file(s) all mode 600
[GREEN] inv_internal_worker_auth: ok — worker enforces auth (401 unauth) and accepts the mounted credential (200)
static guard: VERIFIED 57 sites, VIOLATIONS 0, exit 0
Tier 5 assembly (kai): warnings: []  ·  <system_state> present and populated
```

Still-red (pre-existing, OUT OF SCOPE): `endpoint_contracts` (contract tests
721h stale), `no_secrets_in_vault_docs` (2 hits in a 2026-07-09 session doc),
`google_calendar` (deliberately deferred until S7-9).

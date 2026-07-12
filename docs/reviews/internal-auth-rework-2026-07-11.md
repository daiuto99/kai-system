# internal-auth trust repair #2 — rework against Codex's F1-F5

**Date:** 2026-07-11 (same day as trust repair #1 and its rejection)
**Builder:** Claude (Anthropic) — Recovery Plan Step 1, rebuild session
**Reviewed by (pending):** OpenAI Codex — required before either ticket can close (Part 0 rule #9)
**Plane:** KAI-739 `48f85706`; KAI-678 `aec2d486` — REOPENED to In Progress this session, NOT marked Done
**Supersedes:** `docs/reviews/internal-auth-fix-2026-07-11.md` (trust repair #1, worker commit `04a9a03`), rejected by `docs/reviews/internal-auth-codex-review.md`

This document is a point-by-point response to Codex's five findings. Every
claim below cites the file/line changed and the live command whose output
proved it, per the durability rule (MEG "Recovery Plan" section: a repair is
accepted only when the failure mode is demonstrably impossible AND a
standing check holds it fixed — not "the session closed").

---

## F1 (CRITICAL) — worker auth boundary failed open

**Was:** `kai-worker-api/main.py:38-39` — `_load_credential()` returning
`None` (missing/empty/malformed cred file) made the middleware `return await
call_next(request)` — i.e. serve every protected route unauthenticated.

**Fix:** same branch now returns `503` unconditionally instead of calling
`call_next`. No valid server credential → deny all non-exempt routes. Diff:
worker commit (this session, see Deployment below), `kai-worker-api/main.py`.

**Evidence — all three cred-file failure modes, live, against the actual
deployed container** (via `starlette.testclient.TestClient` against the real
`main.py` module inside the running `kai-worker-api` image, monkeypatching
only `_AUTH_FILE`'s path — not the middleware logic):

```
missing:   no-Authorization-header -> 503, bogus-Authorization -> 503
empty:     no-Authorization-header -> 503, bogus-Authorization -> 503
malformed: no-Authorization-header -> 503, bogus-Authorization -> 503
```

Baseline (correct credential) reconfirmed unaffected: `curl -u <cred>
.../system/ops-state` → 200; no-auth → 401.

**Operational note (transparency, not evidence-hiding):** an earlier attempt
to reproduce the "missing file" case by deleting the live bind-mounted
credential file broke `docker compose restart` entirely — `kai_worker_auth`
is declared as a Docker secret for 6 services, and Docker refuses to
(re)create a container when a declared secret's source file doesn't exist,
independent of whether the app code reads that Docker-secret path. The file
was restored within the same command sequence and the container came back
healthy; no downtime beyond the restart cycle. This is why the evidence above
uses the TestClient method instead: it exercises the real, deployed
`BasicAuthMiddleware` code path without risking the shared secret's Docker
lifecycle. This coupling (6 services share one `secrets:` declaration keyed
to the same host file) is part of what F5 records below.

**Disposition: FIXED.**

---

## F2 (HIGH) — not class-wide; bare callers

Codex's table, closed one row at a time:

| Caller | Fix | Live evidence |
|---|---|---|
| orchestrator calendar (`capabilities/calendar.py`) | `auth=_worker_auth()` (imported from `context_service.py`, the module's existing helper) added to both `httpx.get`/`httpx.post` calls | `POST /capability/calendar.get_events` via the deployed container → `{"ok":true,...,"verification":{"verified":true}}` — was live-401 per Codex |
| orchestrator self-modify (`self_modify.py`) | `auth=_worker_auth()` added to both `update_plane` calls (`GET /plane/issues`, `PATCH /plane/issues/{id}`) | Code-verified only — `SELF_MODIFY_ENABLED=false` gates this path off in production, so it cannot be live-probed without flipping a feature flag out of this session's scope. Codex's own finding noted the flag "reduces current reachability but does not remove the call sites" — fixed regardless of reachability. |
| worker self-dispatch (`dispatch.py` `/wordpress` POST) | `auth=_worker_auth()` (imported from `watchdog.py`'s existing helper) added to both the injected-client and bare-client branches | `dispatch._call_worker_wp_post('__nonexistent_probe_site__', ...)` inside the live container → `404 Site not found` (route logic reached), not `401` — proves the auth boundary was passed |
| worker Slack reply handoff (`routes/slack.py` `/checkin/slack-reply`) | `auth=_worker_auth()` added | Confirmed the target route itself requires and accepts the credential: no-auth → 401, worker-cred → 200. Caller-side fix verified by code (identical `_worker_auth()` import/call pattern proven live on 3 other call sites in this table). |
| dashboard nginx proxy (`kai-web/nginx.conf` `/api/`) | `kai_worker_auth` secret added to `kai-web`'s compose entry; `entrypoint.sh` base64-encodes it and `envsubst`s a `proxy_set_header Authorization "Basic ${WORKER_AUTH_B64}"` into the `/api/` location at container start (same pattern already used for `kai_web_password`) | `curl -u kai:<web-password> http://localhost:3001/api/system/ops-state` → 200 (was unauthenticated to the worker before this fix — nginx had only `kai_web_password`, a different credential, per Codex) |
| n8n (4 unauthenticated workflow nodes) | **Not fixed — design decision recorded below**, per instruction not to force the mechanism | — |

**Class-wide grep re-sweep** (Part 0 rule 3: enumerate the whole risk class,
not just the named files) — searched every `.py` file under `kai-system` for
`kai-worker-api:8001` / `localhost:8001` / `127.0.0.1:8001` references beyond
Codex's list. Found and checked: `kai-slack-bot/main.py` (all worker calls
already carry `auth=_worker_auth()` except the `/slack/events` self-relay,
which targets a `_NO_AUTH`-exempt route by design), `kai-scheduler/scheduler.py`
(worker calls already authenticated; non-worker hits are Telegram/Slack/n8n),
`kai-worker-api/scheduler.py` and `kai-worker-api/routes/inbox.py` (both
define an unused `WORKER_API` constant with zero call sites — dead code, no
fix needed), `kai-mcp-api/main.py` (all worker access already routes through
one authenticated `_call_worker()` choke point). No additional gaps found.

### n8n disposition (recorded, not hacked)

n8n's 4 unauthenticated workflow nodes (`KAI - Parking Lot Capture`, `KAI -
Slack Channel Create` active; `KAI -Morning Focus Brief`, `KAI - Morning
Brief Email` inactive) are **not wired to send the worker credential in this
session.** Reasoning: n8n intersects the Recovery Plan's S7-9 full-retirement
track (n8n's remaining live surface is Google Cal/Mail-adjacent workflows
already slated for replacement). Investing in n8n-specific credential
plumbing now is work the retirement will throw away — the same meta-work
trap the Recovery Plan exists to stop. **This is an accepted, time-bounded
gap, not an oversight:** it closes when S7-9 executes (n8n is retired and
these nodes cease to exist) or is escalated if S7-9 slips materially and the
exposure becomes the load-bearing risk. Recorded here and in the F5 follow-up
ticket's disposition, not silently left off Codex's table.

**Disposition: FIXED (5 of 6 callers) / DEFERRED-BY-DESIGN (n8n, recorded).**

---

## F3 (HIGH) — static guard evadable

`kai-worker-api/tests/test_internal_auth_guard.py` rewritten. Each of
Codex's six specific weaknesses fixed and cited inline in the new file's
comments:

1. Fixed 6-directory allowlist → dynamic discovery of every top-level
   directory under `kai-system` containing `.py` files.
2. Exact-identifier-only detection (`WORKER_URL`/`WORKER_API`) → alias
   resolution via AST fixed-point (catches `_WORKER_BASE`, `_WORKER_API_URL`,
   arbitrary alias chains, literal URLs, string concatenation, and
   `httpx.Client(base_url=...)` wrapper clients).
3. Substring `NO_AUTH` matching (wrongly exempted `/system/health`) → exact
   reconstructed-path matching.
4. `auth=` accepted any non-`None` value (`auth=False`/empty passed) → only
   rejects the specific falsy sentinels (`None`, `False`, `""`, empty
   tuple/list); everything else counts as a real credential.
5. File-global authenticated-client-variable tracking (a `client` in one
   function could "authenticate" an unrelated same-named `client` elsewhere)
   → per-scope tracking (module top-level and each function walked as an
   isolated scope). This same class of bug also existed in the NEW alias
   resolution during development (a local `url` in one invariant function was
   nearly misattributed from an unrelated local `url` elsewhere) and was
   caught and fixed the same way before this doc was written — see the test
   file's own comments.
6. Silent skip on parse failure → skips are collected and surfaced (and fail
   the pytest test) instead of disappearing.

Also added: a grep-based nginx check (`test_no_bare_nginx_worker_proxy`,
since nginx.conf isn't Python/AST-scannable) so the F2 dashboard-proxy fix
has its own regression guard, not just a one-time probe.

**Evidence — Codex's four adversarial probes, all flipped**, run against the
new guard (`test_adversarial_probes_all_flip`, live on the deployed worker
container):

```
literal_url_detected  = True   (was False)
alias_detected         = True   (was False)
system_health_exempt   = False  (was True)
auth_false_accepted    = False  (was True)
```

Full suite: `pytest kai-worker-api/tests/test_internal_auth_guard.py -v` →
**4 passed** (`test_no_bare_internal_worker_calls`,
`test_council_shared_client_is_authenticated`,
`test_no_bare_nginx_worker_proxy`, `test_adversarial_probes_all_flip`).

Running the rewritten guard against the real, current codebase (not a
synthetic probe) also surfaced two things worth recording honestly:

- A genuine false-positive during development, from the alias-resolution
  feature being file-global instead of scope-aware (same bug class as F3
  bullet 5, in new code) — fixed before this doc was written; see the guard
  file's own docstring on `_resolve_worker_base_names`.
- `inv_internal_worker_auth`'s own deliberate unauthenticated boundary
  self-test (see F4) reads as a "bare call" by design. Rather than silently
  exempt it, the guard requires an explicit, visible `# GUARD:
  intentional-unauthenticated-probe` marker comment on that exact line —
  verified, not ignored, and it shows up in the guard's own verified-list
  output tagged `[intentional no-auth probe]`.

**Disposition: FIXED.**

---

## F4 (HIGH) — runtime invariant service-local

`kai-scheduler/invariants.py::inv_internal_worker_auth` broadened from one
leg (worker boundary self-test, scheduler's own credential) to three:

1. Worker boundary self-test (kept, unchanged in substance).
2. **Orchestrator caller** — live round trip through
   `kai-orchestrator`'s `calendar.get_events` capability (not a direct worker
   call — this is the actual caller code F2 fixed).
3. **MCP caller** — live round trip through `kai-mcp-api`'s `get_tasks` tool,
   which exercises its `_call_worker()` choke point.

**Evidence — live, from inside the deployed `kai-scheduler` container:**

```
ok: True
msg: ok — worker enforces auth (401 unauth / 200 auth), orchestrator calendar
round-trip authenticates, MCP get_tasks round-trip authenticates
```

**Evidence the invariant actually catches a regression** (not just "runs and
passes"): the orchestrator leg's `httpx.post` was monkeypatched in-process to
return a synthetic `401`, and the invariant correctly flipped to `False` with
a message naming the orchestrator leg specifically:

```
orchestrator-regression simulated -> ok: False
msg: orchestrator calendar.get_events did not succeed (HTTP 401, ...) —
orchestrator's worker credential mount/wiring is broken
```

Recorded limitation (not silently assumed covered): `kai-slack-bot` has no
inbound HTTP surface this invariant can round-trip through (Socket
Mode/event-driven only — matches why Codex's own review used a direct
loader+transport check instead of an HTTP probe for that service). n8n is
the same accepted-risk as F2's disposition. Both are stated here rather than
implied "class-wide" when they aren't fully.

**Disposition: FIXED (broadened from 1 caller to 3; 2 callers remain
unobservable by design, recorded above).**

---

## F5 (MEDIUM) — shared-credential blast radius

**Not fixed — recorded, with a filed follow-up, per instruction ("record,
don't necessarily fix").**

Codex's finding stands: one Basic-auth credential (`kai_worker_auth`) is
mounted into 6 services, sent over plaintext HTTP inside the Docker network,
provides no per-service identity, and the loader (`_worker_auth()` /
equivalent) is duplicated with slightly different fallback-path lists across
5 files. F1-F4 close the acute failure modes (fail-open boundary, bare
callers, an evadable guard, a service-local invariant) that made the
credential's *presence* meaningless. The credential's *design* — one shared
long-lived secret — is a separate, real, structural question that a
same-session patch would not do justice to.

**Filed:** Plane `bec32d28-8e90-449d-aea8-fb58d23a2eb4` — "[FOLLOW-UP]
internal-auth F5: shared single Basic-auth credential across 6 services" —
scopes per-service credential identity (or short-lived tokens/mTLS) and
consolidating the 5 duplicated loader implementations into one shared module.

**Additional note fed into that ticket:** during this session's F1 testing,
a worker credential value was inadvertently echoed into this session's own
tool-output transcript while restoring the secrets file after a
container-recreate failure (see F1's operational note). Per L15 ("secrets
never printed... applies... regardless of whether the credential is still
live"), this is flagged to Leo as an incident, not silently absorbed — it
does not change this credential's live status unilaterally (rotation is an
"ask before" action per Part 0 rule 8), but it sharpens the case for F5's
follow-up.

**Disposition: RECORDED, follow-up filed, not fixed this session.**

---

## Deployment

All code changes built and deployed live to the authoritative worker root
(`/home/leo/kai-system`) this session: `kai-worker-api` (main.py, dispatch.py,
routes/slack.py, tests/test_internal_auth_guard.py), `kai-orchestrator`
(capabilities/calendar.py, capabilities/self_modify.py), `kai-web`
(nginx.conf, entrypoint.sh, Dockerfile), `kai-scheduler` (invariants.py),
`docker-compose.yml` (kai-web `kai_worker_auth` secret). Containers rebuilt
and recreated; all healthy post-deploy.

## Gating next step

Per Part 0 rule #9 (builder.provider ≠ reviewer.provider) and this session's
explicit instruction: **this rework is not marked Done.** Both Plane tickets
(`48f85706`, `aec2d486`) are In Progress, carrying a comment pointing at this
document. The gating next step is an independent Codex review of F1-F5
against the evidence above, the same way trust repair #1 was reviewed and
rejected.

---

## Third attempt addendum — 2026-07-12 (Codex builder)

**Controlling issue:** `5fbed8a2`; related `48f85706`, `aec2d486`

**Authoritative worker commit:** `bb04e63`
**Disposition:** built and live-verified against the five open conditions in
`internal-auth-rework-codex-review.md`; all three tickets remain **In
Progress** pending independent Claude review (rule #9).

This addendum supersedes only the second attempt's builder dispositions above;
it does not alter the independent Codex rejection record.

### F1 — malformed server credentials

`_load_credential()` now rejects empty username/password components. The
committed middleware regression test and isolated live container probe cover
missing, empty, no-colon, `:`, `kai:`, and `:pw` without touching the live
secret:

```text
missing: noauth=503 matching_shape=503
empty: noauth=503 matching_shape=503
no_colon: noauth=503 matching_shape=503
colon_only: noauth=503 matching_shape=503
empty_password: noauth=503 matching_shape=503
empty_user: noauth=503 matching_shape=503
test_all_malformed_shapes_return_503 ... ok
Ran 1 test in 0.038s — OK
```

### F2 — Vite proxy

The Vite `/api` worker proxy is removed and a regression assertion forbids
both an `/api` block and a direct worker target. The live production image is
nginx-only:

```text
vite direct worker target occurrences=0
vite /api proxy blocks=0
entrypoint=["/entrypoint.sh"] cmd=null image=kai-system-kai-web
production_image_has_no_vite_runtime
```

### F3 — demonstrated evasions

The bounded guard now folds split literal concatenation, reads `url=`, catches
`urllib.request.urlopen`, inherits module-level base-url clients across scopes
with shadowing, and performs one-hop wrapper-parameter analysis. It explicitly
does not claim arbitrary program-analysis or n8n/JavaScript coverage.

```text
VERIFIED internal worker call sites (24)
REVIEWER EVASIONS FLAGGED: {'split_concat': 1, 'requests_url_keyword': 1,
'urllib_urlopen': 1, 'module_base_url_cross_scope': 1,
'wrapper_parameter': 1}
OK — every current internal worker call carries auth.
```

### F4 — real scheduler caller

The invariant invokes `scheduler._fetch_worker_health()`, the exact transport
used by `check_worker_health()`. Removing only `scheduler.worker_auth` now
turns it red. `calendar.create_event` is honestly bounded as **not** runtime
probed because it is an external mutating write; only the static caller guard
holds its auth argument.

```text
baseline=(True, 'worker 401; real scheduler caller 200; orchestrator
calendar.get_events 200; MCP get_tasks 200; calendar.create_event NOT
runtime-probed')
scheduler_worker_auth_regression=(False,
'scheduler._fetch_worker_health returned HTTP 401, expected 200')
```

### F5 — seven services and rotation incident

Live `docker compose config --format json` inventory:

```text
credential_bearing_services=7
kai-council-api
kai-mcp-api
kai-orchestrator
kai-scheduler
kai-slack-bot
kai-web
kai-worker-api
```

Plane follow-up `bec32d28` is renamed to seven services. Direct Plane readback
confirmed its description now records all seven services, the durable
transcript exposure, the worker-side rotation, the missed Mac-side copy and
catch-up outage, subsequent reconciliation, and that rotation does not reduce
the seven-service shared-secret blast radius. The follow-up remains Backlog.

### Close/review handling

`manual_close.py` was not run because its known mutation bug is out of scope.
Evidence/verdicts are in git because worker `GET /plane/issues/{id}` omits
description. The three repair tickets remain In Progress for Claude review.

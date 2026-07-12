# Claude independent review (rule #9) — internal-auth trust repair, third attempt

**Date:** 2026-07-12
**Reviewed:** authoritative worker commits `bb04e63` (code/tests) and `66f15bf`
(F1–F5 live evidence), against the five closing conditions in
`docs/reviews/internal-auth-rework-codex-review.md`.
**Builder:** OpenAI Codex (governed-builder profile)
**Reviewer:** Claude (Anthropic) — independent of the builder, per Part 0 rule #9
(builder.provider ≠ reviewer.provider).
**Controlling Plane issue:** KAI-742 `5fbed8a2`; related repair tickets KAI-739
`48f85706` and KAI-678 `aec2d486`.
**Overall verdict:** **PASS.** All five closing conditions are independently
reproduced against the deployed containers. The three repair tickets may close.

## Method and elevated-risk note

The builder implemented its own review specification (Codex built against the
five conditions Codex itself wrote), so this review looked specifically for
self-lenience: every claim was **reproduced against the live deployed
containers on the worker**, not read from the repo or trusted from the
addendum. Probes used temporary files, in-process monkeypatches, read-only
calls, deliberately nonexistent identifiers, and hand-authored source snippets.
No production credential value was written into this document. One credential
was inadvertently surfaced into this review *session's* transcript during F2 —
see Observations (3); it is flagged to Leo, not absorbed.

## Verdict summary

| Condition | Verdict | Basis |
|---|---|---|
| F1 — all malformed server-credential shapes 503 | **PASS** | Six shapes independently 503 against deployed boundary; committed regression test present in image and green. |
| F2 — Vite proxy accounted for | **PASS** | Deployed `kai-web` image is nginx-only, no Vite runtime; `vite.config.js` has no `/api` worker proxy; regression assertion exists and runs. |
| F3 — guard closes the demonstrated evasions | **PASS** | All five demonstrated evasions independently flag; guard honestly disclaims arbitrary/JS/n8n coverage. |
| F4 — invariant exercises the real scheduler caller | **PASS** | Regressing `scheduler.worker_auth` flips the live invariant red naming `scheduler._fetch_worker_health`; `create_event` honestly bounded (static-guard-only, and that call site genuinely carries auth). |
| F5 — follow-up reflects seven services + rotation | **PASS** | Plane `bec32d28` renamed to seven services; description lists all seven and records the transcript incident, rotation, Mac-side miss, and that rotation does not reduce blast radius. Independently confirmed exactly seven services mount the secret. |

## F1 — malformed server credentials must all fail 503

`kai-worker-api/main.py:24` `_load_credential()` now returns `None` when either
component is empty (`if not user or not pw`), and the middleware returns `503`
on `None`. Reproduced against the **deployed `kai-worker-api` container** via
`starlette.testclient.TestClient` on the real `main.app`, redirecting only
`_AUTH_FILE` to temp files — the live secret was never read:

```text
missing:     noauth=503 matching_shape=503 OK
empty:       noauth=503 matching_shape=503 OK
no_colon:    noauth=503 matching_shape=503 OK
colon_only:  noauth=503 matching_shape=503 OK   (":")
kai_colon:   noauth=503 matching_shape=503 OK   ("kai:")
colon_pw:    noauth=503 matching_shape=503 OK   (":pw")
real_cred_noauth=401   (boundary still live with the real credential restored)
```

Committed regression test `kai-worker-api/tests/test_basic_auth_fail_closed.py`
is **present inside the deployed image** (`/app/tests/…`) and runs green:
`test_all_malformed_shapes_return_503 … ok` (covers the five colon-bearing
shapes as subtests plus the missing-file case). **F1: PASS.**

## F2 — Vite `/api` worker proxy

The deployed `kai-web` container is nginx-only: image `kai-system-kai-web`,
entrypoint `/entrypoint.sh`, empty cmd, and **zero `vite` binaries on the image
filesystem** (`vite_bins=0`). The repo `kai-web/vite.config.js` defines no `/api`
worker proxy (only a `/council` dev proxy to the separate, still-unauthenticated
council boundary, which is out of this rework's scope). The production `/api/`
path is the **authenticated nginx proxy** (it injects a worker Authorization
header at container start), not a bare caller. Regression assertion
`test_vite_worker_proxy_is_non_executable()` exists and forbids both a direct
worker target and an `/api` proxy block in `vite.config.js`; the guard's
`__main__` runner exits `0` with all test functions executed. **F2: PASS.**

## F3 — static guard evasions

Independently re-authored all five demonstrated evasions from scratch (different
variable names/paths) and fed them to the guard's real `_scan_python_source`
(not the builder's own `_reviewer_evasion_results` helper). All five flag:

```text
split_concat                 flagged  (violations=1)
requests_url_keyword         flagged  (violations=1)
urllib_urlopen               flagged  (violations=1)
module_base_url_cross_scope  flagged  (violations=1)
wrapper_parameter            flagged  (violations=1)
```

**New evasions Codex did not list** were also probed. Caught: f-string URL,
`httpx.AsyncClient`, `aiohttp`. Slipped: `str.format`, `%`-format,
dict-indirection, env-var base, two-hop wrapper, `http.client.HTTPConnection`.
The slipped cases are **consistent with the guard's explicit disclaimer** — the
addendum states it "does not claim arbitrary program-analysis or n8n/JavaScript
coverage" and performs only one-hop wrapper analysis. Codex's five demonstrated
evasions are all closed and the guard does not over-claim, which is what
condition #3 requires. The residual evasions are recorded as a bounded,
honestly-disclaimed gap (see Observations 2). **F3: PASS.**

## F4 — runtime invariant exercises the real scheduler caller

`kai-scheduler/invariants.py::inv_internal_worker_auth` now calls
`scheduler._fetch_worker_health()` — the exact `httpx.get("/system/health",
auth=worker_auth())` transport used by the scheduled health job. `/system/health`
is **not** in `_NO_AUTH` (a no-auth call returns 401), so the leg genuinely
requires the scheduler's credential. Reproduced inside the deployed
`kai-scheduler` container:

```text
BASELINE           ok=True   (worker 401 unauth / scheduler /system/health 200 / orch 200 / mcp 200)
                   create_event honestly bounded: "NOT runtime-probed"
SCHED_REGRESSION   ok=False  scheduler.worker_auth→None →
                   "scheduler._fetch_worker_health returned HTTP 401, expected 200 — scheduler caller auth is broken"
SCHED_WRONGCRED    ok=False  (wrong credential also flips red)
RESTORED           ok=True
```

HTTP logs confirm the regressed run actually hit `/system/health` and received
401 — this is the real caller, not a synthetic direct request from the invariant
module (Codex's original F4 objection). `calendar.create_event` is honestly
bounded as not-runtime-probed (mutating external write); its auth is held by the
static guard, and that claim is non-empty —
`kai-orchestrator/capabilities/calendar.py:56` carries `auth=_worker_auth()`.
**F4: PASS.**

## F5 — seven-service blast radius and rotation

Plane `bec32d28-8e90-449d-aea8-fb58d23a2eb4` is renamed to
"[FOLLOW-UP] internal-auth F5: shared single Basic-auth credential across 7
services" and its description (read directly from Plane, since the worker
endpoint omits it) records: all seven credential-bearing services
(kai-worker-api, kai-council-api, kai-slack-bot, kai-web, kai-mcp-api,
kai-scheduler, kai-orchestrator); the durable transcript capture during trust
repair #2; the worker-side rotation; the missed Mac-side copy that broke
catch-up for ~24h (reconciled 2026-07-12, Plane `3c223c87`); and the explicit
statement that rotation contains the exposed value but does **not** reduce the
seven-service shared-secret blast radius. Independently confirmed via
`docker compose config` that **exactly seven** services mount `kai_worker_auth`,
matching the list. Remains Backlog, not closed by the acute F1–F4 repair.
**F5: PASS.**

## Observations (non-blocking — do not gate the close)

1. **Guard is run-location sensitive.** `ROOT = Path(__file__).resolve().parents[2]`.
   From the authoritative host checkout `/home/leo/kai-system` this is correct
   (24 verified sites, 0 violations, exit 0). But run **inside** the deployed
   `kai-worker-api` container the file is at `/app/tests/…`, so `parents[2]` is
   `/`, and the guard walks the read-only `/workspace` mount (`/home/leo/sonicink`),
   which contains a stale `kai-system-mirror` tree with pre-fix code → **19 false
   violations**. Copying the test into the image therefore does not make it a
   runnable in-container check. Recommend anchoring `ROOT` to the directory
   containing `docker-compose.yml` (or pinning the standing check to the host
   checkout) so the static guard has a single unambiguous run context. Filed as
   a follow-up candidate, not a blocker.

2. **F3 residual evasions.** `str.format`, `%`-format, dict-indirection,
   env-var-derived base, two-hop wrapper, and `http.client` slip the guard.
   These are outside Codex's five demonstrated cases and inside the guard's
   stated disclaimer, so they do not fail condition #3 — but they confirm the
   guard is a known-evasion regression net, not a complete static proof. Worth
   folding into the F5 structural-hardening track.

3. **Credential surfaced into this review session's transcript.** While
   verifying F2's production auth header, a grep over the running nginx config
   surfaced the live worker Basic-auth value into this session's tool output —
   the same F5 transcript-leak failure mode (MEG L15/L18). Not decoded or
   repeated. Flagged to Leo; rotation is Leo's decision (Part 0 rule 8,
   ask-before). This strengthens, rather than changes, the F5 case.

## Disposition

All five conditions PASS on independent reproduction. Per the review
instruction, KAI-742 `5fbed8a2`, KAI-739 `48f85706`, and KAI-678 `aec2d486` are
closed **Done** with this verdict linked. Review only — no code was changed.

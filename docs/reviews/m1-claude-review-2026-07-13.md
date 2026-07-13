# M1 scoping wire — Claude rule-#9 review — VERDICT: PASS

**Date:** 2026-07-13 · **Reviewer:** Claude (LSE session, Mac) · **Builder:** Codex
**Plane issue:** KAI-738 `1459bfff-8ec4-4742-bec7-10abcdfdd7f8` (In Progress at review start; → Done on this PASS via manual API transition + readback; engine close not used per `cb1c348c`/D13)
**Scope reviewed:** worker `kai-system` commits `fe4dd31`, `f337602`; worker `sonicink` doc commits `086c582`, `8c4f879`, `d5e0bcb`. All five verified present, at/behind `origin/main` HEAD, tree clean.

Every claim below was reproduced live by the reviewer. Nothing in this verdict rests on `/tmp` or on builder transcripts. Reviewer gate capture: `/home/leo/kai-m1-claude-gate/` (transcript committed beside this file as `m1-claude-review-transcript-2026-07-13.txt`).

## C0 — Prior close landed (standing condition): PASS
Mac deferred M0 close commit `58b21c5` exists (2026-07-13 10:16 EDT); `Sprint_History.md` + `StateOfTheUnion.md` both record M0 (`0a46606b`) VERDICT PASS → Done. One drift note, not a C0 failure: Mac `docs/CONTEXT_SPEC.md` carries the M1 deviation-log content as an uncommitted working-tree modification (byte-identical to the worker-committed version, md5 `d7fcdd4e…`) — to be committed at this session close.

## C1 — Plane hygiene: PASS
- Un-park verified from the issue activity feed: exactly two mutations today, both 14:21:41Z — state Backlog → In Progress, label `parked-post-gate` removed. Live state re-verified In Progress at review start.
- Scope/decision comment `a725c4d5-70ea-4180-b0b7-213d79062c9b` (14:21:57Z) present; deliverables and non-goals match what shipped (verified against the diffs, C6).
- KAI-778 `f07245c1-d2d0-4d2f-b51c-70b70ca57517` ("Active-project stateful/inferred resolution (deferred from M1)") verified live: state Backlog, label `parked-post-gate`, **real parent link** `parent = 1459bfff…` (D9 satisfied — not a name-prefix).

## C2 — Decision before code: PASS
The §7.2 decision (explicit optional params; no state; no inference; deferral to one follow-up issue) is recorded in BOTH places with correct ordering: Plane scope comment 14:21:57Z → CONTEXT_SPEC v1.6 deviation entry (worker `sonicink` `086c582`, 14:24:06Z) → first code commit `fe4dd31` 14:37:27Z. Texts match each other and the shipped behavior.

## C3 — Gate reproduced fresh by reviewer: PASS
Ran the committed `scripts/fixtures/m1/run_gate.sh` exactly as written (only its designed `CAPTURE_DIR` parameter set, to a fresh non-/tmp dir). All calls traverse the deployed chat path `POST /council/message` (port 8002). Fresh reviewer package IDs:
- **alpha + task_type=m1-scope** `7fd3a280-8912-49f7-b3a1-03cb207c5e95` — t4.facts contained ONLY `m1-alpha-fact-001`; same-project decoy `m1-alpha-other-task-fact-001` (task_type `m1-other-scope`) excluded → task_type wire proven; beta fact excluded → project wire proven.
- **beta + task_type=m1-scope** `044627b2-5784-4191-8c1f-484d4177ca2c` — only `m1-beta-fact-001`.
- **unscoped** `c5a64879-0cd7-4718-8ac8-e87668ea0778` — all three fixture facts; no invented default; assembly shape (t1–t5, same conversation key tuple `["m1smoke","m1-smoke-gate",null,null]`) consistent with pre-M1 behavior.
`assert_gate.py` asserts on persisted assembly-log rows read back from the orchestrator — real output, not mocks. Final line: `M1 GATE INCLUDING CLEANUP: PASS`, exit 0. No improvisation required.

## C4 — Tier 3 claim verified in code: PASS
`kai-orchestrator/context_service.py:172` — `_tier3_recall(advisor: str, message: str)` has no project/task_type parameter; the Qdrant search body (`vector`/`limit`/`with_payload`, ~line 200) carries **no filter clause**; the sole call site `context_service.py:729` passes only advisor + message; prose ingest writes no project payload field. The builder claim ("t3 project scoping not implemented") is structural truth, not a fixture artifact. **Filed with readback: KAI-779 `f81560be-745c-4a7e-b6cc-7ec86666312f`** — Backlog, `parked-post-gate`, parent KAI-738, medium.

## C5 — Cleanup + registry safety: PASS
Reproduced after the reviewer gate run: registry SHA-256 `d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72` (byte-identical restore), all three M1 fact IDs removed, only the three pre-gate facts remain, `fact-kai-system-topology-001` intact and verified, `GET /collections/m1smoke` → 404, synthetic persona removed. Cleanup is fail-loud by construction: every cleanup step feeds `cleanup_rc`, the SHA compare and the 404 check are hard requirements, restore refuses to run if non-m1smoke registry data changed under it, and any failure exits 1.

## C6 — Non-goals held: PASS
`f337602` touches no runtime code (docs + fixtures only). Runtime delta in `fe4dd31` is exactly: two optional `MessageRequest` fields, `_context_assemble_payload()` (omits absent keys — cannot invent defaults), one call-site swap in `council_message`, and one `ADVISOR_CHANNELS` entry `m1smoke` (disclosed fixture retention for gate repeatability; inert without its persona, which cleanup removes). No assemble() internals, no tier logic, no `_VALID_COLLECTIONS`/allowlist change, no registry.py/ingest.py change, no new endpoints, no conversation state.

## C7 — Tests: PASS
Reviewer-run in an ephemeral container off the deployed image (pytest installed ephemerally — the image genuinely lacks it, consistent with KAI-776): focused M1 suite **3/3 passed**; full council suite **24 passed** (compose env/secrets/network required for the live-dispatch test). The scoped-call proof chain asserts on real persisted assembly logs (gate), and the unscoped unit test asserts `project`/`task_type` are absent from the outbound payload — an invented default fails both layers. Nothing NEW rides in under KAI-776: ruff on all four touched/new files shows the new files clean; the 6 findings are pre-existing debt on untouched lines of `router.py`/`council_config.py`.

## C8 — Credential incident: no credential in git; token NOT rotated (OPEN LEO ACTION)
- **(a) Scan:** zero occurrences of the shared basic-auth credential (raw, password part, base64) and of the Plane API token across the committed transcript, the review doc, all five M1 commit diffs, and this reviewer transcript. **No credential material is in git history.** The incident artifact lives outside git: 1 occurrence of the raw Plane API token in the Mac-side Codex session log (`~/.codex/sessions/2026/07/13/rollout-2026-07-13T08-48-34-*.jsonl`).
- **(b) No Plane mutation from the failed call:** the 14:21:25Z failure was `http.client` `ValueError: Invalid header value` raised in `putheader` — client-side, before transmission (token file read without splitting the trailing `plane_workspace_slug` line). KAI-738 activity feed shows nothing at that timestamp; the only mutations today are the authorized 14:21:41Z un-park pair.
- **(c) Exposure surface:** the L15/SUBSTRATE-4 print-guard covers the orchestrator/session surface and the shared basic-auth credential class; it does not cover builder-agent tool output persisted by harness .jsonl logs on the Mac, nor the Plane-token credential class. **Filed with readback: KAI-780 `f737b18b-1279-4a70-af78-fbc70891b78d`** — Backlog, `parked-post-gate`, parent KAI-738, high.
- **(d) Rotation status: NOT ROTATED.** The exposed Plane API token is byte-identical to the currently live token (file mtime 2026-04-21). It is OUTSIDE the SUBSTRATE-5 one-command set (that tool rotates the shared basic-auth credential). **OPEN LEO ACTION:** regenerate the Plane API token, update the worker secrets file + `/run/secrets` consumers, restart dependents. Not performed inside this review session (not directed).

## Verdict
**PASS — C1–C8 all pass; C0 pass with one drift note.** KAI-738 → Done via manual API transition with readback (recorded in the session log). Discovered/filed this review: KAI-779 (medium, design-fact), KAI-780 (high, credential guard gap + rotation action).

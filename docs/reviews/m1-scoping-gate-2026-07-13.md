# M1 scoping wire — Codex build evidence

Plane session issue: KAI-738, `1459bfff-8ec4-4742-bec7-10abcdfdd7f8`.
The direct API readback showed state `In Progress`, no parked label, and the
expected title. Scope/design comment readback:
`a725c4d5-70ea-4180-b0b7-213d79062c9b`.

The one authorized deferred issue is KAI-778,
`f07245c1-d2d0-4d2f-b51c-70b70ca57517`: title
“Active-project stateful/inferred resolution (deferred from M1),” state
`Backlog`, label `parked-post-gate`, parent KAI-738. The binding decision was
committed before runtime code as worker `sonicink` commit `086c582` in
`docs/CONTEXT_SPEC.md` v1.6.

## Deployed gate command

```bash
cd /home/leo/kai-system
install -d -m 0700 /tmp/kai-m1-gate-1459-rerun
set -o pipefail
CAPTURE_DIR=/tmp/kai-m1-gate-1459-rerun \
  bash scripts/fixtures/m1/run_gate.sh 2>&1 \
  | tee /tmp/kai-m1-gate-1459-rerun/transcript.txt
```

The complete command output, all three full chat responses, all three persisted
assembly-log rows, seed output, and cleanup output are checked in beside this
file as
[`m1-scoping-gate-transcript-2026-07-13.txt`](m1-scoping-gate-transcript-2026-07-13.txt).

## Acceptance result

| Call | `package_id` | Persisted `t4.facts` fixture IDs |
|---|---|---|
| `project=alpha`, `task_type=m1-scope` | `6005f8ea-a734-4f4e-9aed-2a4156dedf6b` | `m1-alpha-fact-001` |
| `project=beta`, `task_type=m1-scope` | `73fd4a07-afb8-4cff-83e6-26a268011de6` | `m1-beta-fact-001` |
| no project or task type | `10e00b31-b77f-46b2-ae86-0df41561b0e2` | both alpha and beta IDs |

All calls used advisor `m1smoke`, message “What is the verified M1 project
marker?”, and conversation key
`["m1smoke", "m1-smoke-gate", null, null]`. The unscoped union is the live
no-default proof: no project filter was invented. The unit regression also
asserts that absent scope keys are omitted from the outbound assemble payload.

Tier 3 project scoping is **not implemented** in the current server design:
prose ingest has no project payload field, `_tier3_recall()` has no project
filter, and M1 did not change `_VALID_COLLECTIONS`. Therefore all three
`t3.hits` arrays are empty for `m1smoke`; only Tier 4 is used as scoping proof.

## Cleanup result

Registry SHA before seed and after cleanup:
`d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72`.
The pre-existing topology fact remained verified, both M1 fact IDs were removed,
the final registry parsed as valid JSON, and `GET /collections/m1smoke` returned
HTTP 404 after deletion. The synthetic persona was removed. Persisted
assembly-log/conversation rows remain as audit evidence; checked-in fixtures and
the synthetic chat mapping remain so the gate is repeatable.

## Regression commands and results

Focused one-off container run: `3 passed`. Complete council suite in the same
built image with an ephemeral pytest target: `24 passed`, `4 subtests passed`.
The repository `scripts/ci.sh` command was also run and failed because `ruff`
and/or `pytest` are absent from service images, the already-filed KAI-776 defect;
the orchestrator portion still reported `12 passed, 1 skipped`. An ephemeral
full Ruff scan reported existing council lint debt outside this issue; no
unrelated files were changed.

The first gate attempt rejected the fact fixtures before mutation because they
used a JSON array instead of the M0 contract’s `{ "facts": [...] }` envelope.
Its fail-safe cleanup deleted the collection and restored the same registry SHA.
The envelopes were corrected, validated, and the complete gate above passed.

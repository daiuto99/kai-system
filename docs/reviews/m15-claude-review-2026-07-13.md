# M1.5 Claude rule-#9 review — null-advisor (global) fact write path — 2026-07-13

**Plane issue:** KAI-787 `4fc4e8ae-22a8-474a-954c-9251b38fb329` (In Progress at review start)
**Builder:** Codex, worker commit `a0ab52a` ("KAI-787 4fc4e8ae: add explicit null-advisor global fact writes")
**Reviewer:** Claude (independent; every load-bearing claim reproduced live on the worker; nothing sourced from `/tmp` or builder transcripts)

## VERDICT: PASS (C1–C7 all PASS; C0 standing condition PASS)

---

## Preamble — authoritative repo state

- `/home/leo/kai-system` working tree **clean**; `a0ab52a` is **HEAD exactly** (`git log --oneline -1` = `a0ab52a KAI-787 …`; `merge-base --is-ancestor a0ab52a HEAD` = true).
- Diff stat of `a0ab52a`: `docs/INGEST_FORMAT.md`, `kai-orchestrator/registry.py`, `kai-orchestrator/tests/test_registry_write.py`, `scripts/ingest.py`, `scripts/fixtures/m15/*` — nothing else.
- Deployed-code consistency: served module `/app/registry.py` inside `kai-orchestrator` is md5-identical (`a04968fa685733d09dc243d0e8cbf165`) to repo `kai-orchestrator/registry.py` at `a0ab52a` and to the `/kai-system` mount copy.

## C0 — STANDING: prior close landed — PASS

Mac `~/sonicink` tree clean; `StateOfTheUnion.md` and `Sprint_History.md` (repo root) record: M0 review PASS → KAI-775 Done, M1 review PASS → KAI-738 Done, the M1 CONTEXT_SPEC deviation entry committed with the M1 close (Sprint_History line: "Mac `docs/CONTEXT_SPEC.md` carried the M1 deviation entry uncommitted … committed with this session's close"), the Plane-token rotation verified (KAI-780 OPEN LEO ACTION closed), and KAI-781-track/782/783/784 filings in the close narrative with full UUIDs. Nothing stale found.

## C1 — PLANE HYGIENE — PASS

- Parent link read from Plane API: `parent = 532c0d4a-cefc-43f6-818a-8e854d0189c3` — real link to S7-1 (D9). ✔
- Scope comment present at creation (22:17:12Z): `--global` → `advisor:null`, mutually exclusive with `--advisor`, project/task_type orthogonal, INGEST_FORMAT.md + red/green regression coverage, no reader/router/assemble/endpoint changes, no real Leo facts. **Records KAI-786 (`73c92f0b`) as the dependent blocked issue.** ✔
- Delivery comment (22:29:01Z) matches the scope comment and matches the shipped diff (verified against `a0ab52a` below). ✔

## C2 — GLOBAL WRITE + DUAL-ADVISOR READ (the check M2 failed) — PASS, reproduced fresh

Reviewer-owned namespace `m15review-*` (not the builder's `m15-*`). Wrote one global fact via the CLI on the worker host:

```
python3 scripts/ingest.py --facts f1_global_general.json --global --ingested-by claude-m15review
→ exit 0, added_ids: ["m15review-global-general-001"], facts 3→4
  before_sha256 d242d192…, after_sha256 d7982ea5…
```

Stored object verified in the registry file: `advisor: None, project: None, lifecycle: verified`.

Live `POST /context/assemble` on the deployed orchestrator (`docker exec kai-orchestrator curl … localhost:8003/context/assemble`), reviewer package IDs:

| Assemble | package_id | t4 facts include `m15review-global-general-001`? |
|---|---|---|
| advisor=**roads** | `acfccf11-5de8-4e5f-9b44-ecbfb9be8435` | **YES** |
| advisor=**sky** | `a73c5c8f-ab35-4cfe-9139-3a262498e037` | **YES** |

The SAME global fact surfaces under BOTH advisors, on the reviewer's own hands.

## C3 — SCOPE MATRIX — PASS, all four combinations reproduced live

Four reviewer facts written (exit 0 each), stored scopes verified in the registry file:

| Fixture | CLI | Stored |
|---|---|---|
| `m15review-global-general-001` | `--global` | `advisor:null, project:null` |
| `m15review-global-project-001` | `--global --project m15reviewproj` | `advisor:null, project:"m15reviewproj"` |
| `m15review-advisor-general-001` | `--advisor roads` | `advisor:"roads", project:null` |
| `m15review-advisor-project-001` | `--advisor roads --project m15reviewproj` | `advisor:"roads", project:"m15reviewproj"` |

Live assembles (reviewer package IDs) — t4 fact lists exactly per the null-or-exact rule:

| Assemble | package_id | t4 facts (m15review only) |
|---|---|---|
| roads, no project | `acfccf11-5de8-4e5f-9b44-ecbfb9be8435` | all four (unscoped call does not filter project — pre-existing documented reader shape) |
| sky, no project | `a73c5c8f-ab35-4cfe-9139-3a262498e037` | both globals only; roads-scoped facts **absent** (advisor exact) |
| roads, project=`m15reviewproj` | `12f8e80a-4a01-4144-b019-2c6af25b01ea` | all four (null-or-exact match) |
| roads, project=`m15review-other` | `93da71ee-9512-4034-b7f2-432916d932c0` | generals only; **both** project-scoped facts (global-project `m15review-global-project-001` AND advisor-project) **absent** — the 4cd227b2/32a28118 claim reproduced with reviewer IDs |

Mutual exclusion reproduced: `--global --advisor roads` → argparse error `argument --advisor: not allowed with argument --global`, **exit 2**, registry SHA unchanged.

## C4 — FAIL-CLOSED INTEGRITY — PASS (a–d reproduced live, e by code read)

Registry SHA before every negative test: `d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72`.

- **a. Malformed global fact** (missing `source`): `Error: fact ingest rejected; registry unchanged: source must be a non-empty string`, **exit 2**, SHA after byte-identical `d242d192…`. ✔
- **b. Missing `--ingested-by`**: `ingest.py: error: --ingested-by is required with --facts`, **exit 2**, SHA byte-identical. ✔
- **c. Pre-existing fact integrity**: after all four reviewer writes, in-container `facts_for("kai")` = `['fact-kai-system-topology-001', 'm15review-global-general-001', 'm15review-global-project-001']` — hand-seeded topology fact present and `lifecycle: verified` (globals correctly join every advisor's feed); post-cleanup `facts_for("kai")` = `['fact-kai-system-topology-001']`. ✔
- **d. Idempotent rerun** of the global batch: `added: 0, already_present: ["m15review-global-general-001"]`, before/after SHA both `65e9991e…` — byte-identical no-op. ✔
- **e. Single atomic write path (code read)**: the null-advisor change is confined to `append_verified_facts` — signature `advisor: str | None` and the guarded validation branch at `kai-orchestrator/registry.py:154-158`. The write chain is unchanged and shared: whole-batch validate (`:154-180`) → `fcntl.flock` on sidecar lock (`:186-187`) → `tempfile.mkstemp` same-dir (`:239`) → `fsync` tmp (`:247`) → `os.replace` (`:248`) → directory fsync (`:249-251`). **No parallel weaker path exists**; `scripts/ingest.py --global` calls the same `ingest_facts` → `append_verified_facts` entry point with `advisor=None`. ✔

## C5 — NON-GOALS HELD — PASS

- `a0ab52a` touches **no** `router.py`, **no** `context_service.py`, **no** assemble() internals, **no** endpoint code (full diff-stat above). `registry.py` changes are writer-side only; `facts_for` (`registry.py:318-337`, null-or-exact at `:330-335`) is untouched by the commit — the dual-advisor read works through the pre-existing reader. ✔
- Live registry clean before review: 3 facts (`fact-kai-system-topology-001`, `m0-smoke-fact-001`, `m0-smoke-fact-002` — the documented M0 retentions), SHA exactly `d242d192…`; **zero** `m15-*`/gear/seed facts. ✔
- KAI-786 fixtures are committed only under `scripts/fixtures/m2/` (commit `8002203`: `equipment_facts.json`, `roads_notes.md`, `sky_notes.md`) and none of their content is in the live registry. ✔

## C6 — TESTS — PASS

- **Green-after** (reviewer-run, deployed container): `pytest tests/test_registry_write.py -v` → **4 passed**.
- **Red-before** (reviewer-run, mechanical): extracted `a0ab52a~1:kai-orchestrator/registry.py`, ran the committed test file against it in the container → **2 failed, 2 passed**, both failures at advisor validation (`'advisor must be a non-empty string'` raised where the tests expect global behavior) — exactly the builder's red-before claim.
- **Full orchestrator suite** in the deployed container: **14 passed, 1 skipped** (skip pre-existing).
- **Lint**: in-image ruff still absent (`exec: "ruff": executable file not found`) — KAI-675/KAI-776 unchanged, nothing new rides under it. Host ruff (`~/.local/bin/ruff`) on all four changed Python files (`registry.py`, `test_registry_write.py`, `ingest.py`, `run_gate.py`): **All checks passed!**

## C7 — CONTRACT DOC — PASS

`docs/INGEST_FORMAT.md` (diff in `a0ab52a`) now documents: the `--global` command line, the mutual-exclusion rule with rationale (explicit flag rather than reserving the name `global`), a four-row command-scope → stored-scope → meaning table covering the full advisor/project matrix, the always-required `--ingested-by`, and the reader's null-or-exact matching including the unscoped-call non-filtering caveat, with the guidance "seed facts should be scoped whenever their truth is not universal on that dimension." A reader can determine when to use `--global` vs `--advisor` from this doc alone. Usable as Leo's M2 prep contract.

## Cleanup — reviewer residue removed, shown

- All four `m15review-*` facts removed under the registry lock with a fail-loud SHA gate (abort-without-write if the candidate bytes did not hash to the pre-review SHA); restored registry SHA readback: `d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72` — **byte-identical to pre-review**. `grep -c m15review facts.json` = 0.
- Live reader readback post-cleanup: `facts_for("roads")` = `[]`, `facts_for("sky")` = `[]`, `facts_for("kai")` = topology fact only.
- Container residue (`/tmp/redbefore`, pytest cache) removed; reviewer scratch `~/m15review-scratch` removed after this doc was committed.

## Discovered (no out-of-scope fixes made)

- None new. KAI-675/KAI-776 (ruff absent in image) re-confirmed unchanged. The reader's unscoped-call non-filtering on project/task_type is pre-existing, documented behavior (KAI-779 tracks Tier 3's analogue).

## Disposition

- KAI-787 → **Done** via manual API state transition + readback (engine close not used — `cb1c348c`/D13).
- KAI-786 (M2 seed) unblocks per the scope comment's dependency note.

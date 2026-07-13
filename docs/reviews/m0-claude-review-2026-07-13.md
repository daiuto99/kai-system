# M0 seed-ingest — Claude rule-#9 review — 2026-07-13

Reviewer: Claude (LSE session). Plane issue: KAI-775 `0a46606b-59d1-45bc-a639-fce5c2f9732f`
(M0: seed-ingest pipeline + Fact Registry write path). Builder: Codex, worker commits
`af7156e` (build) / `24ae8c3` (close). All evidence below was **independently reproduced
live** in this review session; nothing in the verdict depends on the builder's
`/tmp/kai-m0-gate-20260713/` capture.

## VERDICT: PASS — C1–C8 all pass

KAI-775 transitions to Done manually via the Plane API (not the engine close, per open
defect cb1c348c / D13), with readback recorded at the bottom of this document.

Preconditions verified first: worker tree clean at HEAD `24ae8c3`; `af7156e` and
`24ae8c3` both confirmed ancestors of HEAD (`git merge-base --is-ancestor` both true).

---

## C1 PLANE HYGIENE — PASS

- KAI-775 exists: `0a46606b`, sequence_id 775, state In Progress at review start.
- Real parent link (raw Plane API `parent` field, not name-prefix grouping — D9):
  `parent = 0a28ff0d-9496-49d4-b277-eb4657052f6f` (S7-3).
- S7-1 `532c0d4a` correctly referenced in the issue body as parent-in-spirit.
- Recorded scope (INGEST_FORMAT.md; extend ingest.py without changing chunking/payload
  schema; atomic fail-closed registry write path; fixtures + regression tests + live
  assemble evidence) matches the delivered file list of `af7156e` exactly (14 files;
  see C6).
- Neither parent un-parked or modified: both still Backlog with label `ef3303bc`
  (parked-post-gate), `updated_at` 2026-07-11T21:06Z — two days before the build.

## C2 GATE REPRODUCTION — PASS (with one documented methodology constraint, not a defect)

**Fresh-namespace probe (`m0review`).** The review brief asked for a fresh namespace.
Structural constraint found: `context_service.py` Tier 3 recall runs against the
pre-existing `_VALID_COLLECTIONS` allowlist (§4.2/L4, predates this build); an
unlisted advisor gets Tier 3 silently skipped, and Tier 4 has no allowlist. The build
added only `m0smoke`/`m0isolation` to that allowlist. Consequence: an arbitrary fresh
namespace can prove Tier 4 but not Tier 3 without a code change, which the non-goals
forbid. Reproduced live:

- Ingested `scripts/fixtures/m0/test_document.md` into a fresh `m0review` collection
  (created by `ensure_collection`) and a **fresh-ID fact** through the live write path:
  `m0-review-fact-001`, registry 3→4 facts,
  sha256 `d242d192…` → `b492fdff…` — first-hand proof the write path works fresh, not
  a replay of builder state.
- Live `POST /context/assemble` advisor=`m0review`: package
  `cf1e4675-bdd4-4e17-a870-ced1f522a70d`. Assembly-log row (via committed
  `scripts/m0_read_assembly_log.py`):
  t3 `{"hits": [], "tokens": 0}` with orchestrator log line
  `Tier 3 recall skipped — advisor 'm0review' not in collection allowlist`;
  t4 `{"facts": ["m0-review-fact-001"], "tokens": 57}`. The assembled `facts_text`
  rendered `<verified_fact id="m0-review-fact-001" … source="registry:claude rule-#9
  review 2026-07-13, fresh-namespace write-path proof" trust="verified">` —
  verified state + provenance present end-to-end.

Ruling: not a defect. The allowlist is pre-existing production behavior (spoofed-key
guard); dedicated whitelisted synthetic namespaces were the only design compliant with
the "no assemble()/route changes" non-goal. The gate is repeatable by anyone via the
committed namespaces.

**Documented gate, run fresh by the reviewer.** To eliminate builder residue from the
Tier 3 evidence, the `m0smoke` collection was cleared first (`python3 scripts/ingest.py
--clear m0smoke` — a documented command), so every vector retrieved below was ingested
by this review. Then the committed command ran exactly as written:
`CAPTURE_DIR=… bash scripts/fixtures/m0/run_gate.sh` → **gate PASS**, zero improvisation.

- (a) Tier 3 attribution — smoke package `5a9160b0-de9c-4eed-a808-e8b1930fa048`,
  assembly-log row read back by package_id:

  ```json
  "t3": {"hits": [{"doc_id": "/home/leo/kai-system/scripts/fixtures/m0/test_document.md#chunk0",
                    "score": 0.7169, "source_collection": "m0smoke"}],
          "excluded_below_threshold": 0, "truncated_by_budget": 0, "tokens": 113}
  ```

  `recall_text` contains the cobalt-compass fixture inside
  `<recalled source="qdrant:m0smoke" trust="untrusted">`.
- (b) Tier 4 — same package/log row:

  ```json
  "t4": {"facts": ["m0-smoke-fact-001", "m0-smoke-fact-002"], "excluded_stale": 0, "tokens": 105}
  ```

  `facts_text` rendered both as
  `<verified_fact id="m0-smoke-fact-00N" domain="testing"
  source="registry:scripts/fixtures/m0/test_facts.json" trust="verified">` —
  verified lifecycle + provenance present.
- (c) Isolation — package `73a7ed64-0a65-44fe-89b0-7a87d68e1191` (advisor
  `m0isolation`), assembly-log row: t3 `{"hits": [], "tokens": 0}`,
  t4 `{"facts": [], "tokens": 0}`; response `recall_text` and `facts_text` both empty.

## C3 REGISTRY SAFETY — PASS

- (a) After all review writes, live-path read inside the container:
  `registry.facts_for("kai", project="kai-system")` → exactly
  `fact-kai-system-topology-001`, content intact.
- (b) Invalid-fact rejection re-run standalone by the reviewer with the committed
  `scripts/fixtures/m0/invalid_facts.json`: **exit code 2**, stderr
  `Error: fact ingest rejected; registry unchanged: source must be a non-empty string`,
  sha256 before == after ==
  `d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72` (byte-identical).
  The gate's own fail-closed leg also re-passed during the gate run (exit 2, sha256
  `b492fdff…` unchanged — the mid-review registry state including the reviewer's fact).
- (c) Atomicity is structural, code-read at commit `af7156e`,
  `kai-orchestrator/registry.py`:
  - whole-batch validation completes before any filesystem write
    (`append_verified_facts`, registry.py:137–180 — prepare/normalize, duplicate-ID
    check, all raising `RegistryValidationError` pre-write);
  - exclusive `fcntl.flock` on a sidecar lock file (registry.py:186);
  - `tempfile.mkstemp` into the **same directory**, `fchmod` to the original mode,
    `write` + `flush` + `os.fsync` (registry.py:238–246);
  - atomic `os.replace(tmp, path)` then directory-fd fsync (registry.py:247–252);
  - failure path unlinks the temp file and re-raises (registry.py:253–258).
  A reader can never observe a partial file; a crash leaves the original intact.

## C4 COMPATIBILITY — PASS

`chunk_text` (400/50) and the Qdrant payload fields
(`source`/`title`/`chunk_index`/`chunk_total`/`text`/`advisor`) are untouched by the
diff (additions only: advisor-name validation, `ensure_collection`, `--facts` mode).
Read-only live check against production advisor `roads`: package
`e054c843-68fa-4b54-b196-eb4de60557d6` assembled cleanly — both pre-existing chunks
retrieved (`/home/leo/vault/60_Council/roads/ROADS.md#chunk0/1`, scores 0.7088/0.5755,
`source_collection: roads`), correct `<recalled source="qdrant:roads">` attribution,
zero fixture leakage (no cobalt/silver-orchid content), t4 empty as expected. No
production namespace was written to at any point in this review.

## C5 TESTS — PASS

Reviewer-run in the rebuilt container:
`docker exec kai-orchestrator pytest /app/tests/ -m "not destructive"` →
**12 passed, 1 skipped** (5.5s). The single skip is
`test_s5_4_cost_summary.py:36 test_deferred_invariants_defined`, reason
"requires kai-scheduler context — verified directly in that container" — pre-existing,
unrelated to M0; it covers neither the fail-closed rejection nor read-path
compatibility. The two M0 regression tests exist and pass:
`test_invalid_batch_is_rejected_without_registry_mutation` (asserts the exception AND
byte-identical file) and `test_append_preserves_legacy_read_path_and_adds_scoped_provenance`
(legacy fact object preserved, `facts_for` read-path equality, provenance fields,
idempotent rerun → `already_present`). Red-before/green-after is structural: both
tests call `registry.append_verified_facts` / `RegistryValidationError`, neither of
which existed at the pre-build parent `d758fd5` — they cannot pass on the old code.

## C6 NON-GOALS HELD — PASS

`git show --stat af7156e`: 14 files — 2 docs, 7 fixture files, 1 test file,
`scripts/ingest.py`, `scripts/m0_read_assembly_log.py` (read-only sqlite `mode=ro`),
`kai-orchestrator/registry.py`, `kai-orchestrator/context_service.py`. `24ae8c3`
touches only `logs/orchestrator_backup.log`. No `router.py` change, no `/context/*`
route change, no watcher/queue/auto-ingest machinery. The single `context_service.py`
change is two entries + comment appended to the `_VALID_COLLECTIONS` allowlist set —
no `assemble()` logic change. Noted honestly: this is a change inside the assemble
path's data, made so the gate can run without touching production namespaces; it
affects behavior only for the two synthetic advisor names. Judged in-scope and
minimal.

## C7 CONTRACT QUALITY — PASS

`docs/INGEST_FORMAT.md` read as Leo's contract, not code documentation: opens with the
decision rule (prose → Tier 3 file, exact truths → facts JSON, never combined); names
accepted formats (PDF/MD/TXT/RST/CSV) and CSV behavior; gives copy-pasteable commands
for both tiers; tables every payload/stored field with plain-language purpose; states
which fields are required, what `source` should say ("where the truth came from, not
merely name the import file"), scoping guidance, idempotence, and fail-closed
behavior. A non-developer can prepare content against it. Matches the deployed
behavior verified in C2/C3/C4.

## C8 TOOLING CLAIM — PASS (claim verified true; KAI-675 is a FALSE CLOSE)

- Where Ruff ran: the **worker host** (`python3 -m ruff` = ruff 0.15.21). The
  changed-file gate was reproduced by the reviewer:
  `python3 -m ruff check scripts/ingest.py scripts/m0_read_assembly_log.py
  kai-orchestrator/registry.py kai-orchestrator/context_service.py
  kai-orchestrator/tests/test_registry_write.py --no-cache` → All checks passed, rc 0.
- In-image gate: **absent**. Image `kai-system-kai-orchestrator:latest` rebuilt
  2026-07-13T13:04Z (verified via `docker inspect`; running container uses it):
  `docker exec kai-orchestrator ruff check /app` →
  `exec: "ruff": executable file not found in $PATH`; `python -m ruff` → No module
  named ruff. Root cause: ruff lives only in `requirements-dev.txt:2`; the Dockerfile
  installs `requirements.txt` only.
- `scripts/ci.sh:23` runs ruff **via docker exec** for kai-orchestrator, so the ci.sh
  orchestrator leg cannot pass — KAI-675's own acceptance ("verify scripts/ci.sh
  passes") could not have been met. **KAI-675 (`38f58b84`, Done) is a false close.**
  The M0 builder disclosed this honestly and made no out-of-scope fix.

---

## Housekeeping & evidence durability

- **Builder's fixture residue:** the `m0smoke` Qdrant collection was NOT cleaned up by
  the builder and retention was not documented — housekeeping finding, resolved in
  this review: the reviewer deleted both `m0smoke` and `m0review` collections
  (contents at deletion time were the reviewer's own re-ingest; Qdrant confirms
  neither present). **Retained deliberately and documented here:** the two
  `m0smoke`-scoped registry facts (`m0-smoke-fact-001/002` — advisor-scoped, never
  assembled for production advisors; the gate's stable-ID idempotence re-adds or
  no-ops them on every run) and the committed synthetic personas
  `vault/60_Council/m0smoke/` + `m0isolation/` (reinstalled by `run_gate.sh` on every
  run). `m0isolation` never had a Qdrant collection.
- **Reviewer residue fully removed, shown live:** fact `m0-review-fact-001` removed
  under the registry's own lock with the same mkstemp+replace pattern; registry
  returned **byte-identical to its pre-review state**
  (sha256 `d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72`, 3 facts:
  topology + 2 fixture); `facts_for("kai")` re-verified intact post-cleanup; temporary
  `vault/60_Council/m0review/` persona removed; reviewer capture directory
  `/home/leo/m0review-evidence/` deleted after this document was written (full
  assemble responses carry Tier 5 personal context and are not committable — all
  load-bearing evidence is inline above).
- **Discovery issues filed with readback** (FILE-only, no work done):
  - **KAI-776** `66f45d00-7d4f-4022-86ce-8da6d3732362` — [BUG] KAI-675 false Done —
    ruff absent from rebuilt kai-orchestrator image; ci.sh in-image gate cannot pass
    (high, Backlog; readback confirmed).
  - **KAI-777** `126198b6-ba08-4841-8dd4-11d99336c9ab` — [BUG] July decisions log
    contains 2,585 NUL bytes (independently verified:
    `/home/leo/vault/60_Council/decisions/2026-07.md` = 2,585 × 0x00) — noted that
    SUBSTRATE-2 close verification byte-compares against this file, so the corruption
    sits under the close-integrity mechanism (medium, Backlog; readback confirmed).

## KAI-775 transition readback

Appended post-commit as a Plane comment on KAI-775; transition performed manually via
the Plane API (PATCH state → Done) with GET readback, **not** the engine close
(cb1c348c / D13 demotes Done issues). Readback result recorded in the KAI-775 comment
and the session close.

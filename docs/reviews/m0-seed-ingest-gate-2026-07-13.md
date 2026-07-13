# M0 seed-ingest build evidence — 2026-07-13

Builder: Codex. Plane issue: `0a46606b-59d1-45bc-a639-fce5c2f9732f`
(KAI-775), read back as **M0: seed-ingest pipeline + Fact Registry write
path**, parent `0a28ff0d-9496-49d4-b277-eb4657052f6f`, state In Progress.
S7-1 `532c0d4a-cefc-43f6-818a-8e854d0189c3` is referenced in the issue body;
neither parked parent was modified.

## Reproduce

```bash
cd /home/leo/kai-system
docker exec kai-orchestrator pytest /app/tests/ -v --tb=short -m "not destructive"
python3 -m ruff check scripts/ingest.py scripts/m0_read_assembly_log.py \
  kai-orchestrator/registry.py kai-orchestrator/context_service.py \
  kai-orchestrator/tests/test_registry_write.py --no-cache --output-format=full
CAPTURE_DIR=/tmp/kai-m0-gate bash scripts/fixtures/m0/run_gate.sh
```

The gate prints every component command and exits nonzero on a missing Tier 3
source, missing Tier 4 fact, cross-advisor leak, malformed registry, accepted
invalid input, or registry mutation during the rejected-input test.

## Initial registry append

The first live run began with the single hand-seeded fact:

```text
before: sha256=ba70e399b7863c2406a89416643d25f076e154c5fe5957d1eada07f07666bcdf facts_count=1 valid_json=true
pre_existing_fact_intact=true
```

The Tier 4 command returned:

```json
{
  "ok": true,
  "added": 2,
  "added_ids": ["m0-smoke-fact-001", "m0-smoke-fact-002"],
  "already_present": [],
  "facts_before": 1,
  "facts_after": 3,
  "before_sha256": "ba70e399b7863c2406a89416643d25f076e154c5fe5957d1eada07f07666bcdf",
  "after_sha256": "d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72"
}
```

JSON readback showed all three IDs in order and the original
`fact-kai-system-topology-001` object intact. Each new fact has advisor
`m0smoke`, project `m0-seed`, task type `registry-smoke`, lifecycle `verified`,
source, `ingested_at`, `ingested_by`, and `updated_at`. Subsequent runs report
both IDs under `already_present` and leave the hash unchanged.

## Fail-closed rejection

```text
exit_code=2
Error: fact ingest rejected; registry unchanged: source must be a non-empty string
sha256_before=d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72
sha256_after=d242d1927588875e2892854ffa55c514516536f2d1bbcf585c07a16c0eae0a72
```

## Live assemble + assembly-log readback

Final captured smoke package: `365a9740-5d37-4288-896d-e89bcdb6ba75`.

```json
{
  "t3": {
    "hits": [{
      "source_collection": "m0smoke",
      "doc_id": "/home/leo/kai-system/scripts/fixtures/m0/test_document.md#chunk0",
      "score": 0.7169
    }],
    "excluded_below_threshold": 0,
    "truncated_by_budget": 0,
    "tokens": 113
  },
  "t4": {
    "facts": ["m0-smoke-fact-001", "m0-smoke-fact-002"],
    "excluded_stale": 0,
    "tokens": 105
  }
}
```

The full response's `recall_text` contains the cobalt-compass fixture inside
`<recalled source="qdrant:m0smoke">`; `facts_text` contains both verified-fact
blocks and the `silver-orchid-7319` marker. The assembly-log row retrieved by
the same package ID contains the Tier 3 `doc_id` above and both Tier 4 IDs.

Final isolation package: `a0919a37-3193-4841-b778-502967d87372`.

```json
{
  "key_tuple": ["m0isolation", "m0-smoke-gate", null, null],
  "t3": {"hits": [], "tokens": 0, "excluded_below_threshold": 0, "truncated_by_budget": 0},
  "t4": {"facts": [], "tokens": 0, "excluded_stale": 0}
}
```

Its response also has empty `recall_text` and `facts_text`.

## Full protected capture

The complete 54,778-byte transcript, unabridged assemble responses, registry
snapshots, rejection output, and both assembly-log rows are on the worker at
`/tmp/kai-m0-gate-20260713/` (directory mode 0700; files mode 0600). They are
not committed because full Tier 5 responses contain Leo's assembled personal
context. Integrity checks:

```text
ceb7cbcf8fe7bc03c2290bcf56c4ea4e476a12a882396a73b929d5de54114bb8  m0smoke-response.json
a5a7670b6b7658eca29dbca2c859b7bca6715328de2bbf2b42b8f49bed9a0d7d  m0smoke-assembly-log.json
e93b61f26939b56a6b12db082778cbc7ebefd475894ed7bcee55ceda8e95cb04  m0isolation-response.json
84b56e5f18bd266059e506f46e4157e86b5f6d0ff59cadde1307c682d8e2dda2  m0isolation-assembly-log.json
619e1f25b8843c4ccd48d78e1200b85f38c992c94a9319ffd9a5f4c6037441b3  transcript.txt
```

## Test result

```text
ruff: All checks passed!
pytest: 12 passed, 1 skipped
gate: PASS
tier3_source_attributed: true
tier4_fixture_facts_present: m0-smoke-fact-001, m0-smoke-fact-002
advisor_namespace_isolation: true
```

Independent Claude rule-#9 review remains pending; KAI-775 must stay In
Progress until that review reproduces this gate and is recorded.

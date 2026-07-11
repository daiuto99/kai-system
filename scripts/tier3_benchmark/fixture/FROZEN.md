# Frozen fixture — S7-13a Tier 3 recall benchmark

Per CONTEXT_SPEC.md §12.4: this fixture is fixed before either candidate is built out or tuned. Do not edit `corpus.json` or `queries.json` after this point without re-running both candidates from scratch.

Frozen: 2026-07-11

```
sha256(corpus.json)  = a42722c28686504da1194a3c20b485940b9b5e33fcadc98f97970c6f730eb262
sha256(queries.json) = ef72ab1e8a54f038201d521b1ddfbee289da245a513ca39365cfd62c627b1b07
```

## Contents

- `corpus.json` — 20 documents across 4 advisor namespaces (kai, dev, devops, creative), pulled verbatim/near-verbatim from real vault content (persona docs + decisions log). Includes one deliberate stale/current fact pair (`kai-05-stale` / `kai-06-current`, both about auto-close subsystem status) and one deletion-test target (`kai-07-deletion-target`).
- `queries.json` — 12 normal recall queries with expected-evidence doc IDs and expected answers, 5 adversarial namespace-leak queries (cross-advisor bait with forbidden evidence IDs), 1 stale/contradictory-fact query, 1 deletion test.

## Namespace source-of-truth

Documents are tagged with the advisor namespace they belong to based on which advisor's real domain they came from (KAI.md → kai, DEV.md → dev, DEVOPS.md → devops, CREATIVE.md → creative; decisions-log entries tagged by subject). Both candidates ingest into 4 advisor-scoped stores under identical tagging — this mapping is not re-derived by either candidate, it is given.

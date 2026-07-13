# KAI seed-ingest format

Use this contract when preparing knowledge for an advisor. Put explanatory
material in a prose file (Tier 3). Put short, exact truths that must override
fuzzy recall in a facts JSON file (Tier 4). Do not combine the two formats.

## Tier 3 — prose documents

Accepted files are PDF, Markdown (`.md`), plain text (`.txt`/`.rst`), and CSV.
Write natural prose with clear headings; KAI handles the rest. CSV rows become
`column: value | column: value` lines. Empty documents are skipped.

```bash
python3 scripts/ingest.py path/to/document.md --advisor roads \
  --title "Optional human-readable title"
```

The existing ingest behavior is the contract: 400-word chunks with a 50-word
overlap, embedded by `nomic-embed-text`, then upserted into the collection named
for `--advisor`. A missing safe-named collection is created with 768-dimensional
Cosine vectors; an existing collection is never recreated or cleared.

Each Qdrant point keeps these payload fields:

| Field | Meaning |
|---|---|
| `source` | The input path exactly as supplied to the command |
| `title` | `--title`, or the file name without its extension |
| `chunk_index` | Zero-based position of this chunk |
| `chunk_total` | Total chunks produced from the document |
| `text` | The chunk text |
| `advisor` | The target advisor namespace |

The source path plus chunk index is what the assembly log uses for attribution.
Re-running the same path is an upsert, not a second copy. Advisor names use lower
case letters, numbers, `_`, or `-`, start with a letter/number, and are at most
64 characters.

## Tier 4 — verified facts

Prepare one JSON object containing a non-empty `facts` array:

```json
{
  "facts": [
    {
      "id": "roads-pedalboard-power-001",
      "domain": "gear",
      "key": "pedalboard_power_supply",
      "value": "The touring board uses the CIOKS DC7 power supply.",
      "source": "Leo-confirmed, pedalboard inventory 2026-07-13"
    }
  ]
}
```

`domain`, `key`, `value`, and `source` are required non-empty text. `id` is
recommended and must be stable; if omitted, the writer derives a stable ID from
the fact and its scope. Unknown fields and any lifecycle other than `verified`
are rejected. `source` should tell Leo where the truth came from, not merely name
the import file.

```bash
python3 scripts/ingest.py --facts path/to/facts.json \
  --advisor roads --project studio-refresh --task-type gear-advice \
  --ingested-by leo
```

`--advisor` and `--ingested-by` are required. `--project` and `--task-type` are
optional and apply to every fact in the batch. Omit them for advisor-general
facts. The stored object is:

| Field | Set by | Purpose |
|---|---|---|
| `id`, `domain`, `key`, `value`, `source` | Prep file/writer | Identity, truth, and provenance |
| `advisor` | `--advisor` | Advisor namespace; required on seed writes |
| `project`, `task_type` | Optional CLI scope | Exact assembly scope, or `null` for general facts |
| `lifecycle` | Writer | Always `verified`; only verified facts are readable |
| `ingested_at`, `updated_at` | Writer | UTC write timestamp |
| `ingested_by` | `--ingested-by` | Person or process that trusted the source |

The deployed reader also accepts legacy global facts where `advisor` is `null`.
When assemble supplies a project/task type, a fact matches if its corresponding
scope is `null` or exactly equal. When assemble omits a scope, the current reader
does not filter on that dimension. Seed facts should therefore be scoped whenever
their truth is not advisor-general.

The whole facts batch validates before storage is touched. The writer locks the
registry, preserves its existing root metadata and facts, writes a same-directory
temporary file, flushes it, and atomically renames it. A malformed batch, ID
conflict, or malformed existing registry exits nonzero without changing
`facts.json`. An exact stable-ID rerun is an idempotent no-op.

## Repeatable gate

The synthetic document, valid/invalid facts, dedicated advisor personas, and the
exact live-assembly gate are in `scripts/fixtures/m0/README.md`. They use only the
`m0smoke` and `m0isolation` namespaces.

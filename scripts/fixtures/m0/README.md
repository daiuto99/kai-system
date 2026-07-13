# Milestone 0 repeatable smoke gate

Run from the authoritative worker repository. The script installs two synthetic
personas, ingests the Tier 3 document and Tier 4 fact batch, proves invalid-input
rejection without registry mutation, calls the live orchestrator twice, and reads
each full assembly-log row by its returned `package_id`.

```bash
cd /home/leo/kai-system
bash scripts/fixtures/m0/run_gate.sh
```

To retain the complete responses and assembly logs for review without committing
assembled personal context, set a protected temporary capture directory:

```bash
CAPTURE_DIR=/tmp/kai-m0-gate bash scripts/fixtures/m0/run_gate.sh
```

The gate exits nonzero unless the test document is attributed in Tier 3, both
verified facts appear in Tier 4, the legacy topology fact remains intact, invalid
input leaves `facts.json` byte-identical, and `m0isolation` receives none of the
fixture content. Exact component commands are printed before execution.

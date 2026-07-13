# Milestone 1.5 repeatable global-fact gate

Run from the authoritative worker repository after deploying the writer change:

```bash
cd /home/leo/kai-system
python3 scripts/fixtures/m15/run_gate.py
```

The gate writes four throwaway facts covering advisor/project scope combinations,
proves `--ingested-by` remains mandatory, rejects a malformed global batch without
mutation, proves an exact rerun is byte-identical, then calls the live
`POST /context/assemble` path for Roads and Sky. It reads each returned assembly
log by `package_id`, verifies the global project fact's exact-match behavior, and
atomically restores the registry's original bytes.

All throwaway IDs start with `m15-`; the advisor-scoped fixtures use `m15scope`.
Cleanup refuses to overwrite concurrent non-M1.5 changes and the gate fails unless
the final registry SHA-256 equals the pre-gate SHA-256.

Set `CAPTURE_DIR` to retain the responses and assembly logs in a mode-0700
directory with mode-0600 files. The gate never writes Leo's real seed facts.

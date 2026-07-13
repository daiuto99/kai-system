# Milestone 1 repeatable scoping gate

Run this only from the authoritative worker repository after deploying the
current `kai-council-api` image:

```bash
cd /home/leo/kai-system
umask 077
CAPTURE_DIR=/tmp/kai-m1-gate bash scripts/fixtures/m1/run_gate.sh \
  | tee /tmp/kai-m1-gate/transcript.txt
```

The gate uses the M0 `scripts/ingest.py` write path to create a dedicated
`m1smoke` Qdrant collection and append two fact batches. The alpha batch has an
`m1-scope` fact plus a decoy scoped to `m1-other-scope`; the beta batch has an
`m1-scope` fact. It then
sends the same message for the same advisor and conversation through the live
`POST /council/message` path three times: alpha, beta, and unscoped. Each
returned `package_id` is resolved to its persisted assembly-log row, and
`assert_gate.py` checks the real Tier 4 fact IDs.

The expected results are alpha-primary-only, beta-only, then all three facts
for the unscoped call. Excluding the alpha decoy proves the live chat path
carried `task_type=m1-scope`; excluding the other project's facts proves it
carried `project`. The final union is the no-default proof: current unscoped
`facts_for()` behavior applies neither filter. Unit coverage also asserts that
absent `project` and `task_type` keys are omitted from the outbound assemble
payload rather than sent as defaults.

Tier 3 is not project-scoped in the current server design: prose ingest has no
project field, `_tier3_recall()` does not filter payloads by project, and M1 is
forbidden from changing the Tier 3 collection allowlist. Accordingly, this
gate asserts empty `t3.hits` for `m1smoke` and uses only `t4.facts` as scoping
evidence. The collection is still created so cleanup is exercised rather than
claimed.

Cleanup is part of the gate. On success or failure the script deletes the
`m1smoke` collection, atomically restores the byte-exact pre-gate registry only
if no unrelated facts changed concurrently, verifies the registry SHA, and
removes the synthetic vault persona. Persisted assembly-log/conversation rows
remain as the audit evidence, and the checked-in fixture plus the
`m1smoke` channel mapping remain so anyone can reproduce the gate.

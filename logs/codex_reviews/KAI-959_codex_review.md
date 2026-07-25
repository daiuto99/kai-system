# Codex Review Log — KAI-959 (Hermes hardened default profile + Gate 0)

Protocol: Claude builds → Codex verifies directly (`codex exec`, cross-provider: gpt-5.6-sol
vs Claude). Logged per Leo's standing caveat (2026-07-25): every Codex review is recorded here so
a later regression can be traced to what was verified. Full transcripts alongside this file.

Ticket: `21ef40be-6258-4419-96ec-dab43fdce5ae` · Session: 2026-07-25

---

## Round 1 — initial verify (build commit a973cb8) → FAIL
```
KAI-959 VERIFICATION — FAIL
F1: fail — stopped root/writable `default/default` legacy container remained eligible for label-only reuse.
F2: fail — that reusable legacy container is attached to unrestricted `bridge`.
F3: mechanism-only — snapshot valid + rollback code exists, but restorability needs a live-reversal test.
GATE0: green   GREEN BASELINE: green
CONCERNS:
 1. Renaming legacy containers does not retire them: Hermes reuse ignores names, matches labels across all states. Gate 0 ignored stopped containers.
 2. Gate 0 not truly fail-closed: omits backend/docker_network checks, permits contradictory trailing docker args, doesn't enforce kai-litellm as sole attachment.
 3. Probe hardcodes hardened constructor args instead of consuming live config; cleanup left kai959floorprobe/ residue.
 4. Applier idempotency guard accepts any docker_run_as_host_user: occurrence; writes before validating.
 5. F3 only proves readable snapshot artifacts; no live reversal.
 6. Docker per-container disk quotas unsupported (environment).
RECOMMENDATION: do NOT close.
```

## Round 2 — reverify (commit 357f364) → FAIL
```
#1: resolved (Gate 0 uses -aq across all states; 0 reusable labeled containers).
#2: open — trailing --network=host / --read-only=false / --user=0 / --privileged still pass + override.
#3: resolved (probe consumes live config; no residue).
#4: resolved (YAML-parsed guard; staged copy validated before write).
#5: mechanism-only.
#6: accepted-limitation.
GATE0 green · PROBE green · BASELINE green
NEW: probe cleanup not fail-closed.
RECOMMENDATION: do NOT close.
```

## Round 3 — final (commit c64ef27) → FAIL
```
#2: closed — exact-match rejects trailing --network=host/--privileged; adversarial Gate 0 = RED.
#5: closed — F3 mutates snapshot-seeded temp DB, restores, confirms mutation gone.
cleanup: open — finally runs + dir residue verified, but container teardown exceptions warning-only; container residue not verified.
GATE0 green · BYPASS-REJECTED yes · PROBE green · BASELINE green · NEW REGRESSIONS none
RECOMMENDATION: do NOT close.
```

## Round 4 — final2 (commit 5b8f6b2) → PASS
```
KAI-959 FINAL2 — PASS
cleanup: closed — force-removes by name, verifies container absence, requires both container and dir gone.
GATE0 green · BYPASS-REJECTED yes · PROBE green · BASELINE green · LEAK 0 · NEW REGRESSIONS none
RECOMMENDATION: mark Plane 21ef40be Done.
```

---
Convergence: 6 findings → 2 → 1 minor → 0 (PASS). One genuine security hole (reuse via labels,
Round 1 #1) caught and closed. Final build commit: 5b8f6b2.

Operational note: `codex exec` in background hangs on "Reading additional input from stdin..."
unless launched with `< /dev/null` (prompt-as-arg alone is not enough). Round-4's first launch
hung ~36 min for this reason; relaunched with stdin closed and it passed.

Leo reviews this Round-4 verdict before the ticket is marked Done (his standing caveat, 2026-07-25).

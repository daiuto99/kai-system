# Rule #9 review — plan ↔ Plane reconcile close gate and warmboot field

- Plane review ticket: `1a98ea51-a417-48bc-9bc4-a9555250a4f2`
- Reviewed commits: sonicink `263d7cb`; worker `767617c`
- Reviewer: Codex (independent of the Claude/Fable builder)
- Date: 2026-07-17
- Verdict: **PASS**

## Independent live evidence

Baseline `python3 scripts/state_and_plan.py --reconcile` returned:

```text
[state_and_plan] OK: plan.json consistent with Plane (7 stages)
```

I preserved `docs/plan/plan.json`, changed only `active_stage` to the known
board-complete `core-done` stage, and ran the gate. It rejected the
contradiction with exit code 1:

```text
CONTRADICTION: active_stage 'core-done' is complete on the board
contradiction_rc=1
```

The original plan was restored, its SHA-256 recorded, and reconciliation again
returned OK.

For the unreachable-Plane path, I loaded the real module and changed only its
in-process worker endpoint to `127.0.0.1:9`. `--reconcile` returned:

```text
FAIL: could not reach Plane — reconciliation not verified
unreachable_plane_rc=1
```

That proves the gate fails closed without changing the live worker or Plane.

The exact matcher also rejected an incidental `M-A` mention and accepted the
canonical `M-A:` milestone form:

```text
precision_false=False
precision_true=True
```

Finally, the live worker `/session/brief` returned:

```text
{'ran': True, 'status': 'ok',
 'detail': 'reconciliation ran in-close (execute raises on any contradiction)'}
```

This confirms the warmboot-facing result is surfaced from the close manifest.
No persistent test mutation remains.

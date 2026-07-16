# BUG-14 (e7d28ad3) — Claude rule-#9 review — 2026-07-16

**Build under review:** Codex commits `c29dc50` (red regression test) + `291ed3d` (fix), pushed.
**Verdict: PASS** — issue may be marked Done.

## What was reproduced (reviewer's own hands, nothing trusted from the builder transcript)

1. **Root cause confirmed by code read.** `resume()` iterates a pre-run snapshot of step rows;
   `_run_step()` returns `True` on `failed_permanent` ("let resume() see the failed_permanent on
   next iteration") — but the loop never re-reads live rows, so a sole/last step failing during
   the run fell through to the unconditional `engine.transition("job", ..., "succeeded")`.
   Exactly job `f29d1644`'s recorded shape.
2. **Red-before (reviewer-run).** Fresh worktree at `c29dc50` (test present, fix absent),
   test executed in an ephemeral `kai-system-kai-orchestrator` container:
   `FAILED ... AssertionError: + succeeded` — the job rolled up succeeded over a
   `failed_permanent` sole step. 1 failed.
3. **Green-after in the DEPLOYED container (not a mount).**
   `docker exec kai-orchestrator python -m pytest -q tests/test_bug14_failed_step_rollup.py
   tests/test_engine_sole_writer.py` → **3 passed**.
4. **Deployed-code parity.** `/app/workflow_base.py` in the running container md5-identical to
   repo HEAD (`f592f665…`); container `Up (healthy)` post-rebuild.
5. **L1/Pattern 1 held.** Both new rollup writes go through `engine.transition()` (sole writer);
   the terminal-state guard in `_transition_step` is untouched; the success path still requires
   the pre-existing verification flow. The non-terminal branch conservatively warns and leaves
   the job un-transitioned rather than inventing a state.
6. **Test quality.** The regression test drives the real `Workflow.resume()` against a real
   temp DB via the `F29d1644ShapeWorkflow` single-step `failed_permanent` shape and asserts
   step status, step error payload, and job status — not a mock of the rollup.

## Findings (non-blocking)

- **FILED `45973090` [BUG, medium]:** after a mid-run permanent failure, `resume()` still
  executes SUBSEQUENT steps off the stale snapshot (their rows read `pending`). BUG-14's fix
  makes the final rollup honest, but later steps shouldn't run at all. Out of the reviewed
  ticket's minimal scope; needs its own red test (two-step workflow, step 1 fails, assert
  step 2 never ran).
- Minor: a `cancelled` sole step rolls the job up with the error string "permanently failed" —
  slight mislabel, cosmetic, noted only.
- Codex's mirror sync of `workflow_base.py` was staged but uncommitted (its sandbox cannot take
  `.git/index.lock` on the Mac); committed by the reviewer alongside this verdict.

Reviewer: Claude (LSE). Evidence commands and outputs reproduced live on the worker 2026-07-16
~18:00–18:15Z; ephemeral red-before worktree removed after use (plus Codex's stale
`/tmp/bug21-parent` review worktree, found and pruned).

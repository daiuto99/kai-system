# Rule #9 review — plan.json authority and generated plan document

- Plane review ticket: `691ddfa2-3e51-4bac-a122-7dbf15b2fcd9`
- Reviewed commit: `6b3e17e`
- Reviewer: Codex (independent of the Claude/Fable builder)
- Date: 2026-07-17
- Verdict: **PASS**

## Independent live evidence

Baseline `python3 scripts/state_and_plan.py --check` succeeded. I then appended
a harmless review marker to `docs/KAI_STATE_AND_PLAN.md` and ran the same check:

```text
[state_and_plan] FAIL: KAI_STATE_AND_PLAN.md is out of date vs plan.json+board
check_after_hand_edit_rc=1
```

I invoked `CloseEngine.step_state_and_plan()` directly with only its manifest
flush disabled (avoiding an unrelated session-close state write). It regenerated
the document and performed its own exact-content readback:

```text
step_status=ok
step_detail=KAI_STATE_AND_PLAN.md content readback matched
  (3838 bytes, sha256=d6733b2a66dd)
```

The following `--check` succeeded. The initial, regenerated, and preserved
backup SHA-256 values were identical (`d6733b2a...`), so the hand edit did not
survive the close step and test state was restored.

## Input-direction audit

Executable references to `KAI_STATE_AND_PLAN.md` are limited to:

- the renderer's drift comparison (`DOC_MD.read_text()` under `--check`); and
- `step_state_and_plan`'s exact post-write readback (`doc_path.read_text()`).

All semantic plan inputs come from `docs/plan/plan.json`; no executable path
uses the Markdown document as plan/state input.

## Note on later increments

At commit `6b3e17e`, the optional Plane cross-check was non-fatal, as required
by this increment. Later reviewed SSOT increments deliberately made live board
state mandatory for derived stage status and added a separate gated
`plan_reconcile` step; that behavior belongs to review `1a98ea51`, not this
authority/readback review.

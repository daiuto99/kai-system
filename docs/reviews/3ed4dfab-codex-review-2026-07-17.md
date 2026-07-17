# Rule #9 review — board-derived stage status

- Plane review ticket: `3ed4dfab-3516-4064-ae70-c0ce8c7b2ebc`
- Reviewed commit: `01e4906`
- Reviewer: Codex (independent of the Claude/Fable builder)
- Date: 2026-07-17
- Verdict: **PASS**

## Independent live derivation evidence

`plan.json` contains no authored per-stage `status` or `chip` fields. The live
fetch used 683 issues, including 406 completed issues, proving the
`include_done=true` dependency is effective. Derived live results were:

```text
core-done: derived=done,   matched=7,  completed=7
ssot:      derived=active, matched=31, completed=6
m-c:       derived=planned
m-e:       derived=planned
m-f:       derived=planned
m-g:       derived=planned
backlog:   derived=planned
```

The rendered chip mapping was `Done / Now / Next / Then / Then / Later`; the
non-rendered backlog stage received no chip. Synthetic inputs independently
confirmed `completed -> done`, `started -> active`, and cancelled-only ->
planned. This proves the state is computed from board `state_group`, not
asserted in the plan file.

## Active-stage gate evidence

I preserved the plan, changed its only authored pointer to the board-complete
`core-done` stage, and ran both the command gate and isolated close-engine step.
Both failed:

```text
CONTRADICTION: active_stage 'core-done' is complete on the board
close_gate_status=fail
close_gate_detail=execute failed: plan.json contradicts Plane ...
```

Changing the pointer to `not-a-stage` also returned exit 1. The exact original
plan was restored (SHA-256 `0c81819f...`), and final live reconciliation
returned OK. Manifest flushing was disabled only for the isolated close-step
test, so reviewer testing did not alter session-close state.

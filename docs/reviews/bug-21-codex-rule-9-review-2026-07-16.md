# BUG-21 independent rule-#9 review — PASS

Reviewer: Codex (independent of Claude builder)  
Reviewed build: worker commit `2561fe7`  
Plane review issue: `4b1e5f5f`

## Findings

- PASS — all four `graphs/bug_nodes.py` nodes unpack the router's five-value return and call `_track_usage` with input, output, cache-read, and cache-creation token counts.
- PASS — risk-class sweep lists nine `_run_agentic_loop` call sites in `kai-council-api`; no remaining three-value unpack exists.
- PASS — deployed-container live fires completed for `lse_review`, `architect_review`, and `kai_validation`.
- PASS — the new graph-level failure-layer regression test is red against `2561fe7~1` (four `ValueError: too many values to unpack`) and green against `2561fe7` (4 tests, OK).

## Verdict

PASS. BUG-21's tuple-compatibility and usage-accounting requirements are independently verified. The regression test closes the previously missing failure-layer coverage.

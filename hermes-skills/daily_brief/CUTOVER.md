# AR-2 Daily Brief — Cutover & Retirement (handoff)

**Status:** Skill built + shadow parity PROVEN (5/5 green, see `shadow/comparison_log.md`).
**Blocked on:** SSH access to `71-kai-mini` (100.106.160.41) — the Hermes runtime.
Neither the Mac nor the worker has a key authorized there; the mini's Hermes is
colima/docker (AR-0). This is the only step left and it needs mini access.

## What is DONE (live-verified this session)
- `daily_brief` skill authored: `SKILL.md` + `scripts/build_brief.py` +
  `references/brief-contract.md`. Self-contained port of `kai-worker-api/focus.py`.
- Shadow harness `shadow/run_compare.py`: 5/5 green vs the live incumbent, on the
  worker, using real Todoist (9 overdue) + real Anthropic. Evidence in
  `shadow/comparison_log.md` + `comparison_result.json`.
- Standalone skill entrypoint verified in `--mode shadow`: valid `kai.daily_brief.v1`
  envelope, sink written to a file, **no Slack post**.

## What is already TRUE about the old flow (no work needed to "disable")
- n8n workflows "KAI - Morning Focus Brief" and "KAI - Morning Brief Email" are
  BOTH `active:false` (verified via `n8n export:workflow`).
- `kai-scheduler` brief cron has been commented out since 2026-05-19
  (`scheduler.py:657` "BRIEFS PAUSED"). Nothing fires the brief today.
- => Retirement is an *assertion* (G-D confirms these stay off), not a disable step.

## REMAINING — run on 71-kai-mini (needs mini SSH)
1. Install the skill into Hermes:
   ```bash
   rsync -az ~/kai-system/hermes-skills/daily_brief/ <mini>:~/.hermes/skills/daily_brief/
   ```
   Ensure the hardened profile allows egress to exactly: `api.todoist.com`,
   `api.anthropic.com`, `slack.com`. Mount the secrets dir (todoist/anthropic/slack)
   and the vault read path.
2. Shadow on cron (still safe — writes a file, no Slack):
   ```bash
   docker exec <hermes> hermes cron create "40 8 * * *" \
     "Run the daily_brief skill in shadow mode and post the sink path to my Slack DM." \
     --name "daily-brief-shadow" --deliver slack
   ```
   Let it run a few mornings; eyeball the shadow file vs. the existing brief.
3. Cutover — flip to live only after the shadow mornings look right:
   ```bash
   docker exec <hermes> hermes cron create "45 8 * * *" \
     "Run daily_brief --mode live --channel C0ASGETFCEB." \
     --name "daily-brief" --deliver slack
   docker exec <hermes> hermes cron delete daily-brief-shadow
   ```
   `C0ASGETFCEB` = `#kai-focus`, the same channel the old n8n "Morning Focus Brief"
   POSTed to. Confirm the n8n workflows stay `active:false`.
4. Leave the n8n workflows disabled for a week (do not delete). G-D per-step
   retirement assertion confirms the old flow never fires. After a clean week,
   delete the two n8n workflows and (optionally) retire `focus.py`'s `/focus/*`
   route — it is now dead once the skill owns generation.

## Follow-ups (separate tickets, do NOT fold in — no-theater / one change at a time)
- Route the brief LLM from `claude-haiku-4-5` (Anthropic cloud) to local `qwen-mid`
  (LiteLLM) to honor self-hosted-default + tighten the profile egress to zero cloud.
  This changes wording, so re-run the shadow parity gate after.
- Langfuse tracing for the skill (Plane 85c56917 — sibling ticket, not this one).

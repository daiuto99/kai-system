# AR-2 Daily Brief — Cutover & Retirement (handoff)

Plane: af15154e (high) — "AR-2: Daily brief → Hermes skill + cron (shadow→cutover);
retire the bespoke/n8n flow (F1 step 1)". First strangler-fig retirement.

## Current reality (2026-08-25) — reworked for local LLM + Slack-free

The skill was reworked this session:
- **LLM is now LOCAL** — `qwen2.5:7b` on the mini's Ollama (`100.85.243.2:11434`),
  self-hosted-default. No Anthropic cloud key, no cloud inference egress.
- **Slack fully removed** — Slack is retired system-wide (Buzz primary, Telegram
  emergency). Delivery function reads Telegram (interim); see the delivery decision below.
- **Prompt hardened** against hallucination (local model invented tasks on empty input;
  now emits "None"). Parity criterion #4 (no fabricated tasks) enforced by the prompt.

**Proven on the mini this session (empty-task smoke, no secret needed):**
- Reworked skill runs under the mini venv, calls local qwen, ~35s, emits a valid
  `kai.daily_brief.v1` envelope with all 3 sections and no hallucinated tasks.

## What is DONE (live-verified)
- `daily_brief` skill authored + reworked: `SKILL.md` + `scripts/build_brief.py` +
  `references/brief-contract.md`. Deployed to mini `~/.hermes/skills/local/daily_brief/`.
- Local qwen-mid inference path proven on the mini (envelope + sections + no fabrication).
- Old flow already DEAD (retirement is an assertion, not a step):
  - n8n "KAI - Morning Focus Brief" + "KAI - Morning Brief Email" are BOTH `active:false`.
  - `kai-scheduler` brief cron commented out since 2026-05-19 (scheduler.py "BRIEFS PAUSED").

## REMAINING (the real work) — all needs the mini
1. **Todoist secret → mini** (`~/.hermes/secrets/todoist_api_key`). GATED: the mode-lock
   secrets-path guard blocks any Bash/scp write to a `secrets/` path (by design — do NOT
   rename the dir to dodge it). Authorized paths: (a) the HOSTOPS rail's `place_secret`,
   once wired to target the mini (open ar-2 ticket "[HOSTOPS staging] …place_secret"), or
   (b) Leo places the one key by hand (mode 600). Only Todoist is needed — no anthropic, no slack.
2. **Shadow parity — 5 green cycles** with REAL Todoist data, per `references/brief-contract.md`
   (schema valid, all sections, input parity vs focus.py counts, no hallucinated tasks, fresh date).
   `shadow/run_compare.py` is the harness. This is the cutover evidence — no cutover without it.
3. **Delivery surface decision (blocks LIVE, not shadow).** The routine daily brief is
   *primary* comms → Buzz. But Buzz proactive-push is NOT wired (native agents are
   DM-only/desktop-bound; substrate lands with M4). Telegram is *emergency-only* in the
   comms model, so shipping the routine brief over Telegram is interim, not correct.
   Decide the live surface before cutover — do not default to Telegram silently.
4. **Sandbox network (if running the skill under the docker backend).** config.yaml pins
   `--network hermes-ember`, which does not exist on the reimaged mini. Either create that
   network + a local gateway, or run the skill on the host (host = full egress; fine for a
   local-LLM + Todoist-only brief). Host execution is the simplest path now.
5. **Cron in shadow a few mornings → flip live → assert n8n stays off (G-D) for a week.**

## Follow-ups (separate tickets — do NOT fold in)
- System-wide Slack purge (Leo directive 2026-08-25): remove every Slack reference from
  system/plan/docs (e.g. `kai-orchestrator/context_service.py` Slack poster, plan.json, docs).
- Buzz proactive-push path for the brief (delivery decision #3 above).
- Langfuse tracing for the skill (Plane 85c56917 — sibling ticket).

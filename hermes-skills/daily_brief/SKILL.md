---
name: daily_brief
description: "Generate Leo's daily focus brief (Top 3 / Next 5 / Carried-over) from Todoist tasks + KAI close notes, and deliver it to Slack. Runs on cron under the Hermes hardened profile."
version: 1.0.0
author: KAI (LSE) — strangler-fig port of kai-worker-api/focus.py
tags: [daily-brief, focus, todoist, slack, cron, strangler-fig]
platforms: [linux, macos]
---

# Daily Brief — KAI morning focus

Produce Leo's daily focus brief and deliver it. This skill is the Hermes-native
replacement for the retired n8n "Morning Focus Brief" / "Morning Brief Email"
workflows (AR-2, Plane af15154e). The brief logic it ports lived in
`kai-worker-api/focus.py`; this skill makes it self-contained so it can run under
the hardened profile on cron with a reviewable trace.

## When to Use

- On a daily cron trigger (the morning brief), fired by `hermes cron`.
- When Leo asks for "today's focus" / "the brief" / "what's on today".

## What it does (contract)

Runs `scripts/build_brief.py`, which:

1. Pulls **Todoist** tasks — due-today and overdue (`todoist_api_key`).
2. Loads **yesterday's KAI close notes** from the vault (`60_Council/kai/context.md`).
3. Calls **claude-haiku-4-5** to compose the brief in exactly this shape:
   - **Top 3** — the 3 most important things to move today
   - **Next 5** — on deck after the Top 3
   - **Carried over** — overdue items needing attention
4. Emits a schema-valid JSON envelope (`kai.daily_brief.v1`) — see
   `references/brief-contract.md`.

## Modes — shadow is the default, and it is safe

- `--mode shadow` (**default**): writes the brief to a **sink file only**. It
  **never** posts to Slack and never writes the vault. Use this for the cutover
  parity comparison — the brief is visible to Leo as a file/thread, not in his
  real `#kai-focus` channel.
- `--mode live`: posts to the real Slack channel (`--channel`) and writes the
  vault context. Only flip to this after ≥5 green shadow comparisons.

```bash
# shadow (cutover-safe): write the brief to a file, print the JSON envelope
python scripts/build_brief.py --mode shadow \
  --secrets-dir /run/secrets --vault /vault \
  --sink-file /tmp/daily_brief_shadow.md

# live (post-cutover only): deliver to the real focus channel
python scripts/build_brief.py --mode live \
  --secrets-dir /run/secrets --vault /vault \
  --channel C0ASGETFCEB
```

## Egress (hardened profile)

The skill needs exactly three outbound hosts — allow only these in the profile:

- `api.todoist.com` (task pull)
- `api.anthropic.com` (brief composition — claude-haiku-4-5)
- `slack.com` (delivery, **live mode only**)

No other egress. Secrets are read from the mounted secrets dir, never embedded.

## Retirement note (AR-2)

The two n8n workflows this replaces ("KAI - Morning Focus Brief",
"KAI - Morning Brief Email") were already `active:false` and the
`kai-scheduler` brief cron has been paused since 2026-05-19 — so this skill is
the *only* path that fires the brief going forward. Cutover = install this skill
+ `hermes cron create` + flip `--mode live`. G-D asserts the old n8n flow stays
off.

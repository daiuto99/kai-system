# AR-2 Daily Brief — Cutover & Retirement (handoff)

**Status:** Skill built + shadow parity PROVEN (5/5 green, see `shadow/comparison_log.md`).
Hermes RUNTIME on the mini REPAIRED 2026-07-27 (see below). Remaining: provision secrets +
custom egress profile + scheduler on the mini, cron in shadow a few mornings, then flip live.

## 2026-07-27 — Hermes runtime repaired on the mini (was the real "not running" cause)
Hermes wasn't running on the mini because its **venv was broken** — created from portable
CPython 3.11 (`~/.local/opt/python`) at `~/.hermes/hermes-agent/venv` but only ~45 packages
installed; `cryptography`, `python-dotenv`, `fastapi`, `uvicorn`, etc. were missing, so the
CLI died with `ModuleNotFoundError: cryptography`. Fixed by reinstalling deps into the venv:
```bash
# the transported ~/.hermes/bin/uv is a LINUX binary (exec format error on macOS) — do NOT use it.
~/.hermes/hermes-agent/venv/bin/python -m pip install -e ~/.hermes/hermes-agent   # native macOS wheels
```
Now working: `~/.hermes/hermes-agent/venv/bin/python ~/.hermes/hermes-agent/hermes --version`
(Install method: git · Python 3.11.15) and `hermes cron {list,create,status,tick,...}`.
config.yaml already carries the KAI959 hardened profile (docker backend, `--read-only`,
`docker_run_as_host_user`). No hermes launchd plist exists (only colima + ollama) → not
autostarted; add one after cutover.

## Remaining provisioning (each still needed before a shadow cron)
1. **Secrets** — the mini has NO todoist/anthropic/slack keys and no `~/.hermes/secrets`.
   `build_brief.py` reads `<secrets-dir>/{todoist_api_key,anthropic_api_key,slack_bot_token}`.
   ⚠ Automated transport is BLOCKED by the mode-lock secrets-path guard (a Bash write to any
   `secrets/` path is denied by design; do NOT rename the dir to dodge it). Provision via a
   KAI-orchestrated path or have Leo place the three keys (mode 600) at `~/.hermes/secrets/`.
2. **Skill deps** — the hermes venv lacks `anthropic` (has httpx). `pip install anthropic`
   into the venv, or give build_brief.py its own venv.
3. **Egress profile** — the default hardened profile egress-locks sandboxes to the Ember
   gateway ONLY; the brief needs `api.todoist.com` + `api.anthropic.com` + `slack.com`.
   Author a custom profile allowing exactly those three, or run the skill on the host in
   shadow (host = full egress; acceptable for shadow since it never posts, NOT for live).
4. **Scheduler** — no cron scheduler runs on the mini (`hermes cron status` = none). Either
   run `hermes gateway` under a launchd plist, or launchd-invoke `hermes cron tick`.
5. **Vault** — no `~/vault` on the mini; close-notes will be empty (build_brief tolerates it)
   until the vault (or just `60_Council/kai/context.md`) is synced.

## Original plan (unchanged)

**Mini access (CORRECTED — it is reachable):**
```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/kai_worker leodaiuto@100.106.160.41   # 71-kai-mini
```
(An earlier "blocked on SSH" reading was an ssh-agent too-many-keys artifact — the
`IdentitiesOnly=yes` + user `leodaiuto` invocation works. On the mini, `docker`/`colima`
are at `~/.local/bin`; use a login shell: `zsh -lc "docker ps"`.)

**Real gap:** colima IS running on the mini and the Ember litellm gateway (`kai-litellm`)
is up, but **no Hermes container is running there** — `docker ps -a` shows only
`kai-litellm`. AR-0's autostart brought up colima + Ember but not Hermes. So step 0 of
cutover is to bring the Hermes runtime up on the mini under the hardened profile
(re-run the AR-0 bring-up / Gate-0 + F1/F2/F3 probes), THEN do the steps below.

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

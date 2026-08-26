#!/usr/bin/env bash
# CI grep-gate — fails if Slack creeps back into any LIVE surface.
# Slack is retired (AR-5, sole surface = Telegram via the notify() gateway;
# fully removed KAI-1127, 2026-08-16). Three checks:
#   1. No live slack.com/api reference in any .py OR .sh (the old gate saw .py only —
#      it was structurally blind to backup.sh, which posted to Slack on every backup).
#   2. No council LLM tool DEFINITION whose name provisions/posts Slack
#      (create_slack_channel / invite_to_slack_channel / send_slack_message, …) —
#      the model must not be handed a Slack tool.
#   3. No Slack secret mount or definition in docker-compose.yml — no live service
#      may be handed a slack_* token.
#
# Exceptions (documented): archived paths, syncthing temp files, historical doc
# snapshots, and — check 1 only — kai-worker-api/routes/mode_lock.py (Leo-owned, see
# DEFERRED EXCEPTION below).
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (/home/leo/kai-system)

FAIL=0

# ── Check 1: live slack.com/api in .py and .sh ──────────────────────────────
# Exclude this gate script itself (it necessarily contains the literal pattern).
# Tests ARE scanned (KAI-1127: the retired live call lived in test_jarvis_system.py —
# excluding tests/ is exactly how it stayed invisible). Only archived paths, syncthing
# temp files, historical doc snapshots, this gate script, and the mode_lock deferred
# exception are skipped.
HITS="$(grep -rn "slack\.com/api" --include=*.py --include=*.sh . 2>/dev/null \
  | grep -vE '(^|/)_archived/' \
  | grep -v '\.syncthing' \
  | grep -vE '(^|/)(IDONTNEEDTHIS|docs/plan/history|docs/reviews)/' \
  | grep -v 'scripts/ci_no_slack_api.sh' \
  | grep -v 'kai-worker-api/routes/mode_lock.py' \
  || true)"

# DEFERRED EXCEPTION: kai-worker-api/routes/mode_lock.py still references the Slack
# web API for the mode-lock unlock-approval flow. Migrating it to Telegram touches
# protected lock assets (~/.claude/hooks/kai_mode_gate.sh etc.) and is owned by Leo
# — tracked as the dedicated mode-lock-approval ticket. Remove this exclusion when
# that ticket lands. See memory feedback_mode_lock_approval_telegram.

if [ -n "$HITS" ]; then
  echo "CI FAIL — live slack.com/api reference(s) found (Slack is retired, AR-5):"
  echo "$HITS"
  FAIL=1
fi

# ── Check 2: no Slack council LLM tool definitions ──────────────────────────
# Matches a tools-list entry: {"name": "<...slack...>", ...} in the council router.
# Tolerant of single/double quotes and spaces around the colon so a re-add can't
# slip through on formatting alone.
TOOL_HITS="$(grep -nE '["'\''"]name["'\''"][[:space:]]*:[[:space:]]*["'\''"][a-z_]*slack[a-z_]*["'\''"]' kai-council-api/router.py 2>/dev/null || true)"
if [ -n "$TOOL_HITS" ]; then
  echo "CI FAIL — council LLM Slack tool definition(s) found (Slack is retired, AR-5):"
  echo "$TOOL_HITS"
  FAIL=1
fi

# ── Check 3: no Slack secret mounts/defs in compose ─────────────────────────
# Matches a short-form mount ('- slack_...'), a long-form mount ('- source: slack_...'),
# or a secret definition ('slack_...:'). Comments (leading '#') are ignored so
# retire-in-place notes are allowed.
COMPOSE_HITS="$(grep -nE '^[[:space:]]*(-[[:space:]]*(source:[[:space:]]*)?slack_[a-z_]*|slack_[a-z_]*:)' docker-compose.yml 2>/dev/null || true)"
if [ -n "$COMPOSE_HITS" ]; then
  echo "CI FAIL — Slack secret reference(s) in docker-compose.yml (Slack is retired, AR-5):"
  echo "$COMPOSE_HITS"
  FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  exit 1
fi

echo "CI OK — no live Slack references (api / council tools / compose secrets)."

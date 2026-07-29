#!/usr/bin/env bash
# AR-5.3 CI grep-gate — fails if any LIVE service references the Slack web API.
# Slack is retired (AR-5, sole surface = Telegram). Exceptions: retired bot code,
# archived paths, tests, and syncthing temp files.
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (/home/leo/kai-system)

HITS="$(grep -rn "slack\.com/api" --include=*.py . 2>/dev/null \
  | grep -vE '(^|/)(kai-slack-bot|_archived|tests?)/' \
  | grep -v '\.syncthing' \
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
  exit 1
fi

echo "CI OK — no live slack.com/api references."

#!/bin/bash
# KAI-1047 · Fleet heartbeat cron installer (idempotent, reproducible).
# Installs the host-level fleet heartbeat that writes /home/leo/vault/_fleet_state.json
# every 3 minutes. Runs on the worker HOST (it holds the tailnet + ssh keys the
# kai-scheduler container deliberately does not). Re-run after a fresh checkout;
# safe to run repeatedly — it de-dupes its own line.
set -euo pipefail

ROOT="/home/leo/kai-system"
LOG_DIR="$ROOT/logs"
# PATH prefixes python3 so the subprocess docker/ssh calls resolve under cron's
# minimal environment (env-prefix applies to python3, inherited by subprocesses).
CRON_LINE="*/3 * * * * cd $ROOT && PATH=/usr/bin:/bin /usr/bin/python3 scripts/fleet_heartbeat.py >> $LOG_DIR/fleet_heartbeat.log 2>&1"

mkdir -p "$LOG_DIR"

# Remove any prior fleet_heartbeat line, then append the canonical one.
( crontab -l 2>/dev/null | grep -v "fleet_heartbeat.py" ; echo "$CRON_LINE" ) | crontab -

echo "fleet cron installed:"
crontab -l | grep "fleet_heartbeat.py"

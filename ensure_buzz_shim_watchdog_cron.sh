#!/bin/bash
# KAI-1182 · Buzz shim autoheal watchdog cron installer (idempotent, reproducible).
# Installs the host-level watchdog that pokes kai-buzz-shim :4001 every minute and
# `docker restart`s it after 2 consecutive fails (outside a 5-min cooldown). Runs on
# the worker HOST because it needs the docker socket the shim container does not have.
# Closes the 2026-08-21 gap: the shim HUNG (process alive, port refused) so Docker's
# restart:always never fired and the compose healthcheck's "unhealthy" verdict had no
# actor. Re-run after a fresh checkout; safe to run repeatedly — it de-dupes its line.
set -euo pipefail

ROOT="/home/leo/kai-system"
LOG_DIR="$ROOT/logs"
# PATH prefixes python3 so the subprocess `docker restart` resolves under cron's
# minimal environment (env-prefix applies to python3, inherited by subprocesses).
CRON_LINE="* * * * * cd $ROOT && PATH=/usr/bin:/bin /usr/bin/python3 scripts/buzz_shim_watchdog.py >> $LOG_DIR/buzz_shim_watchdog.log 2>&1 # buzz_shim_watchdog KAI-1182"

mkdir -p "$LOG_DIR"

# Remove any prior watchdog line, then append the canonical one.
( crontab -l 2>/dev/null | grep -v "buzz_shim_watchdog" ; echo "$CRON_LINE" ) | crontab -

echo "buzz shim watchdog cron installed:"
crontab -l | grep "buzz_shim_watchdog"

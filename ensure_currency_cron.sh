#!/bin/bash
# CUR-4 · Weekly System-Currency scan cron installer (idempotent, reproducible).
# Runs the read-only currency scanner (scripts/currency_scan.py) once a week on the
# worker HOST — it needs host apt + docker + the Cloudways ssh key the kai-scheduler
# container deliberately does not hold. It writes shared/currency/freshness_state.json;
# the currency custodian (on the */15 devops_custodian runner) reads that and routes
# actionable staleness. On-demand runs are just `python3 scripts/currency_scan.py`.
# Re-run after a fresh checkout; safe to run repeatedly — it de-dupes its own line.
set -euo pipefail

ROOT="/home/leo/kai-system"
LOG_DIR="$ROOT/logs"
# Weekly: Monday 06:17 UTC (off the top-of-hour to avoid contending with other jobs).
# PATH prefixes python3 so the subprocess docker/ssh calls resolve under cron's minimal env.
CRON_LINE="17 6 * * 1 cd $ROOT && PATH=/usr/bin:/bin /usr/bin/python3 scripts/currency_scan.py >> $LOG_DIR/currency_scan.log 2>&1 # CUR-4 weekly currency scan"

mkdir -p "$LOG_DIR"

# Remove any prior currency_scan line, then append the canonical one.
( crontab -l 2>/dev/null | grep -v "currency_scan.py" ; echo "$CRON_LINE" ) | crontab -

echo "currency scan cron installed:"
crontab -l | grep "currency_scan.py"

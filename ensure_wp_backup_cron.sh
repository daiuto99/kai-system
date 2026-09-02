#!/bin/bash
# WP-20.6 · Daily Cloudways WP-fleet backup-policy scan cron installer (idempotent).
# Runs the read-only reader (scripts/wp_backup_scan.py) once a day on the worker HOST —
# it needs the Cloudways API email+token in kai-system/secrets, which the kai-worker-api
# container deliberately does not hold. It writes vault/00_System/wp_backup_state.json;
# the MAINTAIN board (/wordpress/health) reads that and maps each site to its server.
# On-demand runs are just `python3 scripts/wp_backup_scan.py`. Safe to re-run — de-dupes.
set -euo pipefail

ROOT="/home/leo/kai-system"
LOG_DIR="$ROOT/logs"
# Daily 06:47 UTC (off the top-of-hour and off the currency scan's 06:17).
CRON_LINE="47 6 * * * cd $ROOT && PATH=/usr/bin:/bin /usr/bin/python3 scripts/wp_backup_scan.py >> $LOG_DIR/wp_backup_scan.log 2>&1 # WP-20.6 daily backup-policy scan"

mkdir -p "$LOG_DIR"

# Remove any prior wp_backup_scan line, then append the canonical one.
( crontab -l 2>/dev/null | grep -v "wp_backup_scan.py" ; echo "$CRON_LINE" ) | crontab -

echo "wp backup scan cron installed:"
crontab -l | grep "wp_backup_scan.py"

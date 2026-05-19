#!/bin/bash
# KAI host-level backup guardian — runs every 5 minutes via cron
# 1. Ensures the backup cron entry is always present (self-healing)
# 2. Processes backup trigger files written by containers (run-on-demand)

BACKUP_SCRIPT="/home/leo/kai-system/backup.sh"
BACKUP_CRON_LINE="0 2 * * * /home/leo/kai-system/backup.sh >> /home/leo/backups/backup.log 2>&1"
TRIGGER_FILE="/home/leo/vault/00_System/backup_trigger"
LOG="/home/leo/kai-system/logs/backup_guardian.log"

mkdir -p "$(dirname "$LOG")"

# 1. Ensure backup cron is present
if ! crontab -l 2>/dev/null | grep -qF "$BACKUP_SCRIPT"; then
    (crontab -l 2>/dev/null; echo "$BACKUP_CRON_LINE") | crontab -
    echo "[$(date '+%Y%m%d_%H%M%S')] RESTORED backup cron entry" >> "$LOG"
fi

# 2. Process trigger file if present
if [ -f "$TRIGGER_FILE" ]; then
    echo "[$(date '+%Y%m%d_%H%M%S')] Trigger file found — running backup" >> "$LOG"
    rm -f "$TRIGGER_FILE"
    bash "$BACKUP_SCRIPT" >> /home/leo/backups/backup.log 2>&1
    EXIT=$?
    if [ $EXIT -eq 0 ]; then
        echo "[$(date '+%Y%m%d_%H%M%S')] Triggered backup completed OK" >> "$LOG"
    else
        echo "[$(date '+%Y%m%d_%H%M%S')] Triggered backup FAILED (exit $EXIT)" >> "$LOG"
    fi
fi

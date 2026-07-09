#!/bin/bash
# KAI backup — Plane DB + vault
# Runs daily via cron. Keeps 7 days of Plane dumps, vault is full rsync.

set -e
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$HOME/backups"
LOG="$BACKUP_DIR/backup.log"

echo "[$TIMESTAMP] Starting backup" >> "$LOG"

# Plane DB dump
PLANE_FILE="$BACKUP_DIR/plane/plane_${TIMESTAMP}.sql.gz"
docker exec -e PGPASSWORD=plane_kai_2026 plane-db pg_dump -U plane plane | gzip > "$PLANE_FILE"
echo "[$TIMESTAMP] Plane DB: $PLANE_FILE ($(du -sh "$PLANE_FILE" | cut -f1))" >> "$LOG"

# Rotate — keep 7 days
find "$BACKUP_DIR/plane/" -name "plane_*.sql.gz" -mtime +7 -delete

# Vault rsync (preserves structure, deletes removed files)
rsync -a --delete "$HOME/vault/" "$BACKUP_DIR/vault/"
echo "[$TIMESTAMP] Vault synced ($(du -sh "$BACKUP_DIR/vault/" | cut -f1))" >> "$LOG"

echo "[$TIMESTAMP] Backup complete" >> "$LOG"

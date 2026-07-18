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

# Vault rsync — --ignore-errors so root-owned files do not abort the run.
# stderr (skipped-file errors) is captured to a temp file; any non-empty
# result is promoted to a named skip manifest and reported to Slack #devops.
SKIP_TMP=$(mktemp)
rsync -a --delete --ignore-errors "$HOME/vault/" "$BACKUP_DIR/vault/" 2>"$SKIP_TMP" || true
VAULT_SIZE=$(du -sh "$BACKUP_DIR/vault/" | cut -f1)
SKIP_COUNT=$(wc -l < "$SKIP_TMP" | xargs)

if [ "$SKIP_COUNT" -gt 0 ]; then
    SKIP_MANIFEST="$BACKUP_DIR/skip_manifest_${TIMESTAMP}.txt"
    cp "$SKIP_TMP" "$SKIP_MANIFEST"
    echo "[$TIMESTAMP] Vault synced (${VAULT_SIZE}) — WARNING: $SKIP_COUNT file(s) skipped" >> "$LOG"
    while IFS= read -r line; do
        echo "  $line" >> "$LOG"
    done < "$SKIP_TMP"
    echo "[$TIMESTAMP] Skip manifest written: $SKIP_MANIFEST" >> "$LOG"

    SLACK_TOKEN=$(cat "$HOME/kai-system/secrets/slack_bot_token.txt" 2>/dev/null || echo "")
    if [ -n "$SLACK_TOKEN" ]; then
        curl -s -X POST "https://slack.com/api/chat.postMessage" \
            -H "Authorization: Bearer $SLACK_TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"channel\":\"#devops\",\"text\":\":warning: *Vault backup partial* — $SKIP_COUNT file(s) skipped (root-owned or unreadable). Fix: S5R-24. Skip manifest: \`$SKIP_MANIFEST\`\",\"username\":\"DevOps\",\"icon_url\":\"https://kai.sonicink.space/avatar-devops.png\"}" \
            >> "$LOG" 2>&1 || true
    else
        echo "[$TIMESTAMP] WARNING: slack_bot_token not found — Slack alert not sent" >> "$LOG"
    fi
else
    echo "[$TIMESTAMP] Vault synced (${VAULT_SIZE}) — no skips" >> "$LOG"
fi
rm -f "$SKIP_TMP"

# Git-authoritative stores — bare sonicink repo + kai-system (compact, cloneable
# git bundles). Added 2026-07-18 to close the backup gap (these were unbacked). Keep 7 days.
GIT_BK="$BACKUP_DIR/git"
mkdir -p "$GIT_BK"
if git -C /mnt/storage/git/sonicink.git bundle create "$GIT_BK/sonicink_${TIMESTAMP}.bundle" --all >> "$LOG" 2>&1; then
    echo "[$TIMESTAMP] sonicink bare repo bundled: $GIT_BK/sonicink_${TIMESTAMP}.bundle" >> "$LOG"
else
    echo "[$TIMESTAMP] WARNING: sonicink bare-repo bundle FAILED" >> "$LOG"
fi
if git -C /home/leo/kai-system bundle create "$GIT_BK/kai-system_${TIMESTAMP}.bundle" --all >> "$LOG" 2>&1; then
    echo "[$TIMESTAMP] kai-system bundled: $GIT_BK/kai-system_${TIMESTAMP}.bundle" >> "$LOG"
else
    echo "[$TIMESTAMP] WARNING: kai-system bundle FAILED" >> "$LOG"
fi
find "$GIT_BK" -name "*.bundle" -mtime +7 -delete

echo "[$TIMESTAMP] Backup complete" >> "$LOG"

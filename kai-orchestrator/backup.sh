#!/bin/sh
# Nightly backup of orchestrator.db to vault
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DEST="/vault/00_System/backups/orchestrator_${TIMESTAMP}.db"
mkdir -p /vault/00_System/backups
cp /data/orchestrator/orchestrator.db "$DEST" 2>/dev/null && echo "Backup: $DEST" || echo 'No DB yet'
# Keep last 30 backups
ls -t /vault/00_System/backups/orchestrator_*.db 2>/dev/null | tail -n +31 | xargs rm -f

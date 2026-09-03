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
# result is promoted to a named skip manifest and reported via the notify()
# gateway (dashboard audience — a backup-skip is DevOps's to log, not Leo's
# to be pushed; Rule B). Slack retired (AR-5 / KAI-1127).
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

    KAI_SECRETS_DIR="$HOME/kai-system/secrets" python3 - "$SKIP_COUNT" "$SKIP_MANIFEST" >> "$LOG" 2>&1 <<'PY' || echo "[$TIMESTAMP] WARNING: notify() gateway alert failed — see above" >> "$LOG"
import sys
sys.path.insert(0, "/home/leo/kai-system/shared")
from notify_gateway import tg_alert
count, manifest = sys.argv[1], sys.argv[2]
delivered = tg_alert(
    f"Vault backup partial — {count} file(s) skipped. Fix: S5R-24. Skip manifest: {manifest}",
    source="backup.sh", kind="alert",
    cause="root-owned or unreadable vault files (see skip manifest)",
    dedup_key=f"backup-skip:{manifest}",
)
# Non-zero exit so the shell fallback logs a WARNING on a failed delivery.
sys.exit(0 if delivered else 1)
PY
else
    echo "[$TIMESTAMP] Vault synced (${VAULT_SIZE}) — no skips" >> "$LOG"
fi
rm -f "$SKIP_TMP"

# Git-authoritative store — bare sonicink repo bundle (compact, cloneable). This is
# the SOLE off-worker copy of sonicink history (it is NOT on GitHub). kai-system is
# intentionally NOT bundled: it lives on GitHub (daiuto99/kai-system), so an offsite
# bundle is redundant (dropped 2026-09-03, c2-security). Keep 7 days.
GIT_BK="$BACKUP_DIR/git"
mkdir -p "$GIT_BK"
if git -C /mnt/storage/git/sonicink.git bundle create "$GIT_BK/sonicink_${TIMESTAMP}.bundle" --all >> "$LOG" 2>&1; then
    echo "[$TIMESTAMP] sonicink bare repo bundled: $GIT_BK/sonicink_${TIMESTAMP}.bundle" >> "$LOG"
else
    echo "[$TIMESTAMP] WARNING: sonicink bare-repo bundle FAILED" >> "$LOG"
fi
find "$GIT_BK" -name "*.bundle" -mtime +7 -delete

# --- Qdrant (advisor memory, 31 collections; was UNBACKED — audit #01, S1-B3).
# Full snapshot via the API, downloaded out of the container. Each ~3.5G so keep
# only 2. The tier3bench_* benchmark collections bloat this (follow-up cleanup). ---
mkdir -p "$BACKUP_DIR/qdrant"
QSNAP=$(curl -s -X POST http://localhost:6333/snapshots 2>>"$LOG" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("result",{}).get("name",""))' 2>>"$LOG" || true)
if [ -n "$QSNAP" ]; then
    QFILE="$BACKUP_DIR/qdrant/qdrant_${TIMESTAMP}.snapshot"
    if curl -sf "http://localhost:6333/snapshots/$QSNAP" -o "$QFILE" 2>>"$LOG"; then
        echo "[$TIMESTAMP] Qdrant snapshot: $QFILE ($(du -sh "$QFILE" | cut -f1))" >> "$LOG"
    else
        echo "[$TIMESTAMP] WARNING: Qdrant snapshot download FAILED" >> "$LOG"
    fi
    curl -s -X DELETE "http://localhost:6333/snapshots/$QSNAP" >/dev/null 2>&1 || true
else
    echo "[$TIMESTAMP] WARNING: Qdrant snapshot create FAILED" >> "$LOG"
fi
ls -1t "$BACKUP_DIR/qdrant/"qdrant_*.snapshot 2>/dev/null | tail -n +3 | xargs -r rm -f || true

# --- n8n (workflows + credentials sqlite bind mount; was UNBACKED — audit #01).
# tar db+wal+shm; SQLite replays the WAL on restore. Keep 7. ---
mkdir -p "$BACKUP_DIR/n8n"
N8N_FILE="$BACKUP_DIR/n8n/n8n_${TIMESTAMP}.tar.gz"
if tar czf "$N8N_FILE" --ignore-failed-read -C "$HOME/kai-system/n8n-data" \
        database.sqlite database.sqlite-wal database.sqlite-shm 2>>"$LOG"; then
    echo "[$TIMESTAMP] n8n sqlite: $N8N_FILE ($(du -sh "$N8N_FILE" | cut -f1))" >> "$LOG"
else
    echo "[$TIMESTAMP] WARNING: n8n backup FAILED" >> "$LOG"
fi
find "$BACKUP_DIR/n8n/" -name "n8n_*.tar.gz" -mtime +7 -delete || true

# --- buzz-postgres (buzz DB; was UNBACKED — audit #01). pg_dump. Keep 7. ---
mkdir -p "$BACKUP_DIR/buzz"
BUZZ_FILE="$BACKUP_DIR/buzz/buzz_${TIMESTAMP}.sql.gz"
if docker exec buzz-postgres pg_dump -U buzz buzz 2>>"$LOG" | gzip > "$BUZZ_FILE"; then
    echo "[$TIMESTAMP] buzz-postgres: $BUZZ_FILE ($(du -sh "$BUZZ_FILE" | cut -f1))" >> "$LOG"
else
    echo "[$TIMESTAMP] WARNING: buzz-postgres backup FAILED" >> "$LOG"
fi
find "$BACKUP_DIR/buzz/" -name "buzz_*.sql.gz" -mtime +7 -delete || true

# --- Host config (not in git): crontab, live compose, package list, samba conf.
# GitHub holds kai-system code+in-repo config; this captures host-level config so a
# bare-metal rebuild is possible (2026-09-03, c2-security — Leo: system+config must
# be backed up). ---
CFG="$BACKUP_DIR/config"
mkdir -p "$CFG"
crontab -l > "$CFG/crontab.txt" 2>/dev/null || echo "(no crontab)" > "$CFG/crontab.txt"
cp "$HOME/kai-system/docker-compose.yml" "$CFG/docker-compose.yml" 2>/dev/null || true
dpkg-query -W -f='${Package} ${Version}
' > "$CFG/dpkg-packages.txt" 2>/dev/null || true
cp /etc/samba/smb.conf "$CFG/smb.conf" 2>/dev/null || true
echo "[$TIMESTAMP] host config captured -> $CFG" >> "$LOG"

# --- Secrets (ENCRYPTED). Plaintext secrets must NEVER leave in a backup; encrypt
# with a Leo-placed key so the offsite copy is useless without it. The key lives in
# ~/.kai/secrets (NOT inside the backed-up tree) and is never itself backed up.
# Skips cleanly until Leo arms it via scp (2026-09-03, c2-security). ---
SECKEY="$HOME/.kai/secrets/offsite_backup_key"
if [ -f "$SECKEY" ]; then
    SEC_ENC="$CFG/secrets_${TIMESTAMP}.tar.gz.enc"
    if tar czf - -C "$HOME/kai-system" secrets 2>>"$LOG" | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$SECKEY" -out "$SEC_ENC" 2>>"$LOG"; then
        echo "[$TIMESTAMP] secrets encrypted -> $SEC_ENC ($(du -sh "$SEC_ENC"|cut -f1))" >> "$LOG"
    else
        echo "[$TIMESTAMP] WARNING: secrets encryption FAILED" >> "$LOG"
    fi
    find "$CFG" -name 'secrets_*.tar.gz.enc' -mtime +7 -delete 2>/dev/null || true
else
    echo "[$TIMESTAMP] secrets capture skipped — no offsite_backup_key placed (Leo arms via scp)" >> "$LOG"
fi

# --- Offsite transport (S1-B3, audit #01 Track 3). STAGED-DISABLED until offsite.env
# enables it (a GATE). No-op + logs when disabled; never aborts the local backup. ---
bash "$HOME/kai-system/scripts/offsite_sync.sh" || echo "[$TIMESTAMP] WARNING: offsite_sync non-zero" >> "$LOG"

echo "[$TIMESTAMP] Backup complete" >> "$LOG"

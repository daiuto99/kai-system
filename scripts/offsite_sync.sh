#!/bin/bash
# Offsite backup transport — S1-B3 (audit #01, Track 3).
# rsync ~/backups -> an off-worker host over the tailnet. STAGED-DISABLED by default:
# a no-op until ~/kai-system/offsite.env sets OFFSITE_ENABLED=1. Enabling the offsite
# transport is a designated GATE (Leo authorizes; the worker's ~/.ssh/id_ed25519.pub
# must first be installed in the target's authorized_keys). See AUTONOMOUS_SPRINT_MODEL.md B3.
# Writes ~/backups/.offsite_result ("OK <ts> <dest>" | "FAIL <ts> <reason>") which the
# green_baseline offsite_freshness probe reads (RED on FAIL/stale, WARN when disabled).
set -u
BACKUP_DIR="$HOME/backups"
LOG="$BACKUP_DIR/backup.log"
STAMP="$BACKUP_DIR/.offsite_result"
TS=$(date +%Y%m%d_%H%M%S)
ENV_FILE="$HOME/kai-system/offsite.env"

log(){ echo "[$TS] offsite: $1" >> "$LOG"; }

if [ ! -f "$ENV_FILE" ]; then
    log "no offsite.env — transport STAGED-DISABLED (awaiting gate), skipping"
    exit 0
fi
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
if [ "${OFFSITE_ENABLED:-0}" != "1" ]; then
    log "OFFSITE_ENABLED!=1 — transport disabled, skipping"
    exit 0
fi

: "${OFFSITE_USER:?offsite.env missing OFFSITE_USER}"
: "${OFFSITE_HOST:?offsite.env missing OFFSITE_HOST}"
: "${OFFSITE_DIR:?offsite.env missing OFFSITE_DIR}"
OFFSITE_SSH_KEY="${OFFSITE_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_OPTS="-i $OFFSITE_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"

# Preflight: target reachable + dest dir present. A sleeping/offline mini fails here
# (recorded FAIL) rather than a half-written mirror.
if ! ssh $SSH_OPTS "${OFFSITE_USER}@${OFFSITE_HOST}" "mkdir -p '$OFFSITE_DIR'" 2>>"$LOG"; then
    echo "FAIL $TS target-unreachable:${OFFSITE_HOST}" > "$STAMP"
    log "FAIL — ${OFFSITE_HOST} unreachable (target offline?)"
    exit 1
fi

# Incremental mirror. No -z: payloads are already-compressed gz/snapshots/bundles.
# .offsite_result is worker-local state, excluded.
if rsync -a --delete -e "ssh $SSH_OPTS" \
        --exclude '.offsite_result' \
        "$BACKUP_DIR/" "${OFFSITE_USER}@${OFFSITE_HOST}:${OFFSITE_DIR}/" >>"$LOG" 2>&1; then
    echo "OK $TS ${OFFSITE_HOST}:${OFFSITE_DIR}" > "$STAMP"
    log "OK — mirrored to ${OFFSITE_HOST}:${OFFSITE_DIR}"
else
    echo "FAIL $TS rsync-error:${OFFSITE_HOST}" > "$STAMP"
    log "FAIL — rsync error to ${OFFSITE_HOST}"
    exit 1
fi

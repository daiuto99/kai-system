#!/bin/bash
# Offsite backup transport — S1-B3 (audit #01, Track 3).
# rsync ~/backups -> an off-worker host over the tailnet. STAGED-DISABLED by default:
# a no-op until ~/kai-system/offsite.env sets OFFSITE_ENABLED=1. Enabling the offsite
# transport is a designated GATE (Leo authorizes; the worker's ~/.ssh/id_ed25519.pub
# must first be installed in the target's authorized_keys). See AUTONOMOUS_SPRINT_MODEL.md B3.
# Writes ~/backups/.offsite_result ("OK <ts> <dest>" | "FAIL <ts> <reason>") which the
# green_baseline offsite_freshness probe reads (RED on FAIL/stale, WARN when disabled).
#
# Nap-tolerance (offsite-retry bug, same class as the fleet_heartbeat fix KAI-1176): a
# napping-but-soon-reachable mini no longer records FAIL on the FIRST ssh miss. The
# preflight+rsync is wrapped in a retry/backoff loop (OFFSITE_RETRIES attempts,
# sleep = OFFSITE_BACKOFF * attempt), so a transient nap self-heals inside one run; a
# managed mid-morning re-run cron self-heals a longer overnight nap. FAIL is recorded
# only after every attempt is exhausted — so a single 02:06 nap can no longer strand
# offsite_freshness RED with no recovery path and block the close CI gate.
set -u
BACKUP_DIR="$HOME/backups"
LOG="$BACKUP_DIR/backup.log"
STAMP="$BACKUP_DIR/.offsite_result"
ENV_FILE="$HOME/kai-system/offsite.env"

log(){ echo "[$(date +%Y%m%d_%H%M%S)] offsite: $1" >> "$LOG"; }

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
OFFSITE_RETRIES="${OFFSITE_RETRIES:-3}"     # total attempts before recording FAIL
OFFSITE_BACKOFF="${OFFSITE_BACKOFF:-60}"    # base backoff seconds; sleep = BACKOFF * attempt_no
SSH_OPTS="-i $OFFSITE_SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"

# One full attempt: preflight (target reachable + dest dir) then incremental mirror.
# rc 0 = success; 2 = target unreachable (napping/offline mini); 3 = rsync error.
# No -z: payloads are already-compressed gz/snapshots/bundles. .offsite_result is
# worker-local state, excluded.
attempt_sync(){
    ssh $SSH_OPTS "${OFFSITE_USER}@${OFFSITE_HOST}" "mkdir -p '$OFFSITE_DIR'" 2>>"$LOG" || return 2
    rsync -a --delete -e "ssh $SSH_OPTS" --exclude '.offsite_result' \
        "$BACKUP_DIR/" "${OFFSITE_USER}@${OFFSITE_HOST}:${OFFSITE_DIR}/" >>"$LOG" 2>&1 || return 3
    return 0
}

rc=2; reason="target-unreachable:${OFFSITE_HOST}"; a=1
while [ "$a" -le "$OFFSITE_RETRIES" ]; do
    attempt_sync; rc=$?
    [ "$rc" -eq 0 ] && break
    if [ "$rc" -eq 3 ]; then reason="rsync-error:${OFFSITE_HOST}"; else reason="target-unreachable:${OFFSITE_HOST}"; fi
    if [ "$a" -lt "$OFFSITE_RETRIES" ]; then
        s=$(( OFFSITE_BACKOFF * a ))
        log "attempt $a/$OFFSITE_RETRIES failed ($reason) — retrying in ${s}s (napping mini?)"
        sleep "$s"
    fi
    a=$(( a + 1 ))
done

TS=$(date +%Y%m%d_%H%M%S)
if [ "$rc" -eq 0 ]; then
    echo "OK $TS ${OFFSITE_HOST}:${OFFSITE_DIR}" > "$STAMP"
    log "OK — mirrored to ${OFFSITE_HOST}:${OFFSITE_DIR} (attempt $a/$OFFSITE_RETRIES)"
    exit 0
else
    echo "FAIL $TS $reason" > "$STAMP"
    log "FAIL — $reason after $OFFSITE_RETRIES attempt(s)"
    exit 1
fi

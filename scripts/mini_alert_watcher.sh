#!/bin/bash
# S1-B4 (audit #03) — off-box mini alert watcher (independent 2nd-channel watchdog).
#
# Runs ON kai-mini, OFF the worker box, via cron. The worker cannot reliably alert
# about its OWN total death (if the worker or its Telegram route is dead, no page ever
# leaves). This watcher is the net for exactly that: an independent host + independent
# sender that pages Leo when the worker goes dark.
#
# SANCTIONED off-box raw Telegram sender: it cannot import shared/notify_gateway (a
# different host, no /shared mount, and no working Python here — /usr/bin/python3 needs
# Command Line Tools). It therefore uses curl directly — the same architectural
# exception class as the hermes allowlist entry in check_notify_chokepoint.py. Pure
# bash + curl: macOS stock (no CLT), and portable to the planned Linux reimage (only
# `nc` / cron install differ; -mmin find is portable).
#
# Liveness signals — both observed LOCALLY on the mini, needing NO worker credential:
#   1. Offsite freshness — the worker pushes ~/backups here nightly (S1-B3). If that
#      STOPS (no file touched in OFFSITE_STALE_HOURS), the worker's backup+offsite
#      pipeline is dead: a strong worker-degraded signal, seen from off-box.
#   2. Worker-api reachability — a TCP connect to the worker's :8001 over the tailnet.
# Pages on a CONFIRMED, threshold-crossed failure, deduped (a standing outage pages
# once, then a recovery resets). Token+chat are read from mini-local secrets; if absent
# the page is STAGED to the log (the one gate: place the token on the mini).
set -u

STATE_DIR="$HOME/.kai"
STATE="$STATE_DIR/mini_watcher_state"          # sourceable KEY=val
LOG="$STATE_DIR/mini_watcher.log"
SECRETS_DIR="$STATE_DIR/secrets"
OFFSITE="$HOME/kai-offsite-backups"
WORKER_HOST="100.78.94.80"
WORKER_PORT="8001"
OFFSITE_STALE_HOURS=30                          # nightly push + margin
FAIL_THRESHOLD=2                                # consecutive fails before paging (anti-flap)

mkdir -p "$STATE_DIR"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
log() { echo "[$(ts)] $1" >> "$LOG"; }

# ── signal 1: offsite freshness (portable -mmin; exists a file newer than threshold?) ──
offsite_ok=0
if [ -d "$OFFSITE" ]; then
    fresh=$(find "$OFFSITE" -type f ! -name '.*' -mmin -$((OFFSITE_STALE_HOURS*60)) 2>/dev/null | head -1)
    [ -n "$fresh" ] && offsite_ok=1
fi

# ── signal 2: worker :8001 reachability (TCP) ──
worker_ok=0
if command -v nc >/dev/null 2>&1; then
    nc -z -G 6 "$WORKER_HOST" "$WORKER_PORT" >/dev/null 2>&1 && worker_ok=1
else
    timeout 6 bash -c "echo > /dev/tcp/$WORKER_HOST/$WORKER_PORT" 2>/dev/null && worker_ok=1
fi

# ── 2nd-channel page (sanctioned off-box raw send; see header) ──
# rc: 0 = sent, 1 = send failed, 2 = staged (no mini-local token/chat)
page() {
    local msg="$1" token chat code
    token=$(cat "$SECRETS_DIR/telegram_bot_token" 2>/dev/null | tr -d '\r\n')
    chat=$(cat "$SECRETS_DIR/telegram_allowed_chat_ids" 2>/dev/null | tr ',\n' '\n\n' | grep -m1 . | tr -d '[:space:]')
    if [ -z "$token" ] || [ -z "$chat" ]; then
        log "PAGE STAGED (no mini-local token/chat — gate pending): $msg"
        return 2
    fi
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
        -d "chat_id=$chat" --data-urlencode "text=$msg" \
        "https://api.telegram.org/bot${token}/sendMessage" 2>/dev/null)
    if [ "$code" = "200" ]; then log "PAGED ok (2nd-channel): $msg"; return 0; fi
    log "PAGE FAILED http=$code"; return 1
}

# ── load prior state ──
CONSECUTIVE_FAIL=0; PAGED=0
[ -f "$STATE" ] && . "$STATE" 2>/dev/null

if [ "$worker_ok" -eq 1 ] && [ "$offsite_ok" -eq 1 ]; then
    [ "${PAGED:-0}" -eq 1 ] && log "RECOVERED — worker :$WORKER_PORT reachable + offsite fresh"
    CONSECUTIVE_FAIL=0; PAGED=0
else
    CONSECUTIVE_FAIL=$((CONSECUTIVE_FAIL+1))
    log "CHECK FAIL #$CONSECUTIVE_FAIL — worker_ok=$worker_ok offsite_ok=$offsite_ok"
    if [ "$CONSECUTIVE_FAIL" -ge "$FAIL_THRESHOLD" ] && [ "${PAGED:-0}" -eq 0 ]; then
        page "[mini-watchdog] KAI worker appears DOWN — worker:${WORKER_PORT} reachable=${worker_ok}, offsite fresh=${offsite_ok} @ $(ts). Off-box 2nd-channel alert from kai-mini."
        rc=$?
        [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ] && PAGED=1   # sent or staged -> handled; rc=1 retries next run
    fi
fi

# ── persist state ──
cat > "$STATE" <<EOF
CONSECUTIVE_FAIL=$CONSECUTIVE_FAIL
PAGED=$PAGED
LAST_CHECK="$(ts)"
LAST_WORKER_OK=$worker_ok
LAST_OFFSITE_OK=$offsite_ok
EOF
log "state: worker_ok=$worker_ok offsite_ok=$offsite_ok consec=$CONSECUTIVE_FAIL paged=$PAGED"
exit 0

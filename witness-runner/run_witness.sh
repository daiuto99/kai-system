#!/usr/bin/env bash
# KAI-1399 — off-worker external-witness RUNNER (kai-sensor).
# Runs the alert_delivery journey NATIVELY off the worker: Telegram mints the
# message_id receipt, so GREEN is asserted from outside the boundary under test and
# the worker cannot promote itself. Stamps /home/leo/backups/.alert_heartbeat locally
# and appends to the witness ledger; the worker's collector pulls this stamp.
set -u
W=/home/leo/kai-witness
export KAI_SECRETS_DIR="$W/env"
export KAI_NOTIFY_LOG="$W/ledger/notify_log.jsonl"
export KAI_NOTIFY_DEDUP="$W/ledger/notify_dedup.json"
TS="$(date -u +%Y-%m-%dT%H:%M:%S%z)"

# Mutual liveness: the sensor attests the worker is reachable this cycle.
if ping -c1 -W2 100.78.94.80 >/dev/null 2>&1; then WORKER=reachable; else WORKER=UNREACHABLE; fi

OUT="$("$W/venv/bin/python" "$W/scripts/alert_delivery_heartbeat.py" 2>&1)"; RC=$?

# Ledger line (single JSON object, receipt already recorded in the stamp).
LEDGER_OUT="$(printf '%s' "$OUT" | tr '"' "'" | tr '\n' ' ')"
printf '{"ts":"%s","journey":"alert_delivery","rc":%s,"worker":"%s","out":"%s"}\n' \
  "$TS" "$RC" "$WORKER" "$LEDGER_OUT" >> "$W/ledger/witness_ledger.jsonl"

printf '%s\n' "$OUT"
exit "$RC"

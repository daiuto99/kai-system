#!/usr/bin/env bash
# KAI-1399 — worker-side collector for the off-worker witness runner (kai-sensor).
# Pulls the sensor's alert_delivery verdict and transcribes it into the worker's
# baseline stamp (~/backups/.alert_heartbeat) ONLY if the sensor verdict is fresh —
# proving the OFF-WORKER runner actually ran this cycle. A dead/stalled sensor leaves
# the worker stamp to age out -> baseline goes RED (mutual liveness: the worker attests
# the sensor ran). The worker NEVER mints a verdict here; it copies a Telegram-minted
# receipt that was collected off the worker.
set -u
SENSOR="leo@100.123.222.102"
KEY="/home/leo/.ssh/kai_worker"
MAXAGE=7200                                   # 2h; sensor runs hourly. Older => runner stalled.
REMOTE="/home/leo/backups/.alert_heartbeat"
LOCAL="/home/leo/backups/.alert_heartbeat"

INFO="$(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=8 "$SENSOR" \
        "stat -c %Y $REMOTE 2>/dev/null; echo '---'; cat $REMOTE 2>/dev/null")" || {
  echo "collector: sensor unreachable — leaving worker stamp to age (RED if it crosses 36h)"; exit 1; }

MTIME="$(printf '%s\n' "$INFO" | sed -n '1p')"
CONTENT="$(printf '%s\n' "$INFO" | sed -n '/^---$/,$p' | tail -n +2)"
[ -n "$MTIME" ] && [ -n "$CONTENT" ] || { echo "collector: no sensor stamp"; exit 1; }

NOW="$(date +%s)"
AGE=$(( NOW - MTIME ))

# Freshness must track the RECEIPT time embedded in the stamp body, not just the file
# mtime — a touched/rewritten file carrying a STALE 'GREEN' body must not read as fresh
# (Codex F1). Require BOTH the file mtime AND the embedded ISO receipt timestamp (2nd
# token, fractional seconds stripped for portable parsing) to be within MAXAGE.
CTS="$(printf '%s' "$CONTENT" | awk '{print $2}' | sed 's/\.[0-9]*//')"
CEPOCH="$(date -d "$CTS" +%s 2>/dev/null || echo 0)"
[ "$CEPOCH" != "0" ] || { echo "collector: unparseable receipt timestamp ($CTS) — refusing"; exit 1; }
CAGE=$(( NOW - CEPOCH ))

# A future-dated stamp (mtime OR embedded ts) makes age NEGATIVE, which would slip past a
# bare '> MAXAGE' test and keep a recopied GREEN perpetually "fresh" without a real receipt
# this cycle (Codex F2). Bound freshness on BOTH sides: reject anything older than MAXAGE or
# more than SKEW in the future. SKEW tolerates minor NTP drift; a larger future offset means
# a bad clock or a forged/stuck stamp.
SKEW=300
for pair in "file:$AGE" "receipt:$CAGE"; do
  lbl="${pair%%:*}"; val="${pair#*:}"
  if [ "$val" -gt "$MAXAGE" ] || [ "$val" -lt "-$SKEW" ]; then
    echo "collector: sensor verdict not fresh ($lbl ${val}s; window -${SKEW}s..${MAXAGE}s) — NOT transcribing"; exit 1
  fi
done

printf '%s\n' "$CONTENT" > "$LOCAL"
echo "collector: transcribed off-worker alert verdict (file ${AGE}s / receipt ${CAGE}s): $CONTENT"

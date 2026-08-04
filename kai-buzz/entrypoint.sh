#!/bin/bash
# kai-buzz: the Buzz agent surface, productionized from the ~/buzz-eval spike.
# Runs the advisor DM bridges + the approval poller in one container. If any process
# dies, exit so compose (restart: always) relaunches the whole set — the container
# replaces the host watchdog.sh cron+nohup.
set -u
cd /app
echo "[kai-buzz] starting advisor bridges + approval poller ($(date -u +%FT%TZ))"

python3 -u agents_bridge.py KAI          &
python3 -u agents_bridge.py Sky          &
python3 -u agents_bridge.py Roads        &
python3 -u agents_bridge.py Reclamation  &
python3 -u buzz_approve.py               &

# Wait for ANY child to exit, then fail so the container restarts the full set.
wait -n
echo "[kai-buzz] a process exited — exiting for compose restart"
exit 1

#!/bin/bash
# kai-buzz: the Buzz agent surface, productionized from the ~/buzz-eval spike.
# Runs the advisor DM bridges + the approval poller in one container. If any process
# dies, exit so compose (restart: always) relaunches the whole set — the container
# replaces the host watchdog.sh cron+nohup.
set -u
cd /app
echo "[kai-buzz] starting advisor bridges + approval poller ($(date -u +%FT%TZ))"

python3 -u kai_dm.py                     &   # KAI = NIP-17 1:1 DM agent (was: agents_bridge.py KAI channel) — always-on server-side
python3 -u sky_dm.py                     &   # Sky = NIP-17 1:1 DM agent (the original proof)
python3 -u roads_dm.py                   &   # Roads = NIP-17 1:1 DM agent (was: agents_bridge.py Roads channel)
python3 -u coach_dm.py                   &   # Coach = NIP-17 1:1 DM agent (was: agents_bridge.py Coach channel)
python3 -u agents_bridge.py GearTalk     &
python3 -u agents_bridge.py GearTalkSky  &
python3 -u agents_bridge.py KAIProbe     &   # KAI-1142 round-trip probe responder (echo backend, isolated channel)
python3 -u buzz_approve.py               &

# Wait for ANY child to exit, then fail so the container restarts the full set.
wait -n
echo "[kai-buzz] a process exited — exiting for compose restart"
exit 1

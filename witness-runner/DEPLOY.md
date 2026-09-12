# Off-worker witness runner — deploy (KAI-1399)

The external-witness **runner** lives OFF the worker on **kai-sensor** (100.123.222.102),
per `docs/TRUST_INVARIANT_EXTERNAL_WITNESS_DESIGN.md` §2. This dir is the version-controlled
deploy source; the live copy is on kai-sensor.

## Topology
- **kai-sensor** `/home/leo/kai-witness/`:
  - `run_witness.sh` — runner (cron `30 * * * *`). Runs `alert_delivery` natively:
    sends a real Telegram message (Telegram mints the `message_id` receipt), stamps
    `/home/leo/backups/.alert_heartbeat`, appends `ledger/witness_ledger.jsonl`, records
    worker reachability (mutual liveness).
  - `shared/witness.py`, `shared/notify_gateway.py`, `scripts/alert_delivery_heartbeat.py`
    — copied 1:1 from `kai-system` (source of truth for that code).
  - `env/telegram_bot_token.txt`, `env/telegram_allowed_chat_ids.txt` — chmod 600,
    scp'd worker→sensor (never via Mac). `KAI_SECRETS_DIR` points here.
  - `venv/` — python venv with `httpx`.
- **worker** `kai-system/scripts/witness_collector.sh` — collector (cron `40 * * * *`).
  Pulls the sensor stamp over `worker→sensor` SSH (`~/.ssh/kai_worker`) and transcribes it
  into the worker baseline stamp **only if fresh (<2h)**. A stalled sensor → worker stamp
  ages → baseline RED at 36h. The worker never mints the verdict.

## Reprovision the sensor from this source
```bash
# from the worker (worker→sensor SSH):
S=leo@100.123.222.102 ; K=~/.ssh/kai_worker
ssh -i $K -o IdentitiesOnly=yes $S 'mkdir -p /home/leo/kai-witness/{shared,scripts,env,ledger} /home/leo/backups; \
  python3 -m venv /home/leo/kai-witness/venv && /home/leo/kai-witness/venv/bin/pip -q install httpx'
scp -i $K -o IdentitiesOnly=yes kai-system/shared/witness.py          $S:/home/leo/kai-witness/shared/
scp -i $K -o IdentitiesOnly=yes kai-system/shared/notify_gateway.py   $S:/home/leo/kai-witness/shared/
scp -i $K -o IdentitiesOnly=yes kai-system/scripts/alert_delivery_heartbeat.py $S:/home/leo/kai-witness/scripts/
scp -i $K -o IdentitiesOnly=yes kai-system/witness-runner/run_witness.sh $S:/home/leo/kai-witness/
scp -i $K -o IdentitiesOnly=yes kai-system/secrets/telegram_bot_token.txt        $S:/home/leo/kai-witness/env/
scp -i $K -o IdentitiesOnly=yes kai-system/secrets/telegram_allowed_chat_ids.txt $S:/home/leo/kai-witness/env/
ssh -i $K -o IdentitiesOnly=yes $S 'chmod 600 /home/leo/kai-witness/env/*.txt; chmod +x /home/leo/kai-witness/run_witness.sh'
```

## RESIDUAL (tracked follow-up)
`advisor_knowledge` is `docker exec`-bound to `kai-council-api` on the worker, so its driver
stays worker-side for now. Moving it off-worker needs a worker-side journey-trigger endpoint
or sensor→worker exec access; the latter (and the push-based "sensor writes the worker stamp"
variant) requires adding the sensor key to the worker `authorized_keys` — a mode-lock L16
privilege boundary = Leo's hand / escalation. The pull design avoids that boundary.

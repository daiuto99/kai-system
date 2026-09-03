# TROUBLESHOOTING — `kai-scheduler`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-scheduler`.

**Role.** Scheduler — cron jobs, backups, and the LIVE Telegram inbound long-poll (project_telegram_inbound_transport).

## 1. Is it healthy?
No healthcheck defined. Check it is running: `docker inspect -f '{{.State.Status}}' kai-scheduler`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-scheduler
docker logs --tail 100 -f kai-scheduler   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-scheduler   # rebuild + restart (code changed)
docker compose restart kai-scheduler          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-scheduler`.

## 4. Dependencies
- Needs (fix these first if down): `docker-socket-proxy`, `kai-worker-api`
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| Telegram inbound dead | long-poll loop crashed | Restart kai-scheduler; the webhook path in worker-api is dead by design — inbound is here. |
| backups not running | scheduler down or git config mounts missing | Check logs; verify the `.git/config` RO mounts resolve. |

**Notes.** No compose healthcheck. Depends on docker-socket-proxy + kai-worker-api. Mounts plane RO + backups RO.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

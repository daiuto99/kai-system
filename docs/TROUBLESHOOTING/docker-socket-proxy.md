# TROUBLESHOOTING — `docker-socket-proxy`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `docker-socket-proxy`.

**Role.** Read-only docker.sock proxy (:2375 internal) — lets worker-api/scheduler query docker without raw socket access.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec docker-socket-proxy sh -c "nc -z 127.0.0.1 2375 || exit 1"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' docker-socket-proxy`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 docker-socket-proxy
docker logs --tail 100 -f docker-socket-proxy   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build docker-socket-proxy   # rebuild + restart (code changed)
docker compose restart docker-socket-proxy          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/docker-socket-proxy`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| worker-api/scheduler can't see containers | proxy down | Restart it FIRST — worker-api and scheduler depend on it. |

**Notes.** Mounts docker.sock RO. A dependency for kai-worker-api and kai-scheduler.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

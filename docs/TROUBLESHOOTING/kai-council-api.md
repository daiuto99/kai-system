# TROUBLESHOOTING — `kai-council-api`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-council-api`.

**Role.** Council/advisor API (:8002) — advisor routing, DMs, intake. Depends on worker-api.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-council-api sh -c "curl -f http://localhost:8002/health"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-council-api`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-council-api
docker logs --tail 100 -f kai-council-api   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-council-api   # rebuild + restart (code changed)
docker compose restart kai-council-api          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-council-api`.

## 4. Dependencies
- Needs (fix these first if down): `kai-worker-api`
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| advisor endpoints 5xx | worker-api down (dependency) | Fix kai-worker-api first, then restart this. |
| advisor replies empty/stale | buzz-shim backend (:4001) down | Check kai-buzz-shim health — native advisors are backed by it (project_buzz_advisor_backend). |

**Notes.** Mounts vault RW. Depends on kai-worker-api.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

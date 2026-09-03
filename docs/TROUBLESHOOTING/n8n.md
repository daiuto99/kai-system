# TROUBLESHOOTING — `n8n`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-n8n`.

**Role.** n8n automation (:5678) — workflows. Container name is kai-n8n.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-n8n sh -c "wget -qO- http://localhost:5678/healthz"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-n8n`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 n8n
docker logs --tail 100 -f kai-n8n   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build n8n   # rebuild + restart (code changed)
docker compose restart n8n          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/n8n`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `100.78.94.80:5678:5678`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| workflows not firing | n8n down or workflow deactivated | Check /healthz; verify the workflow is active in the UI. |

**Notes.** Container name kai-n8n (not n8n). Mounts vault RW + ./n8n-data.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

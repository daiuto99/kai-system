# TROUBLESHOOTING — `kai-orchestrator`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-orchestrator`.

**Role.** Orchestrator (:8003 internal) — hostops deploy rail, approval gates, WordPress build-draft, devops self-modify.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-orchestrator sh -c "curl -f http://localhost:8003/health"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-orchestrator`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-orchestrator
docker logs --tail 100 -f kai-orchestrator   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-orchestrator   # rebuild + restart (code changed)
docker compose restart kai-orchestrator          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-orchestrator`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| hostops rail canary fails | deploy-key secret not resolving end-to-end | Check `/run/hostops-deploy-keys` mount + kai_publish_gate_secret; see KAI-1166 rail canary. |
| gates stuck pending | resolver not polling | Inspect `/orchestrator/gates/{id}/resolve`; restart orchestrator. |

**Notes.** Mounts kai-system RW + hostops secrets. Note: its internal port is 8003 (same as mcp-api's internal health path but a different container).

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

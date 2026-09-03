# TROUBLESHOOTING — `kai-mcp-api`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-mcp-api`.

**Role.** MCP tool API (:8003) — exposes KAI tools (tasks, vault, wordpress, etc.) to agents.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-mcp-api sh -c "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8003/health\", timeout=5)' 2>/dev/null || exit 1"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-mcp-api`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-mcp-api
docker logs --tail 100 -f kai-mcp-api   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-mcp-api   # rebuild + restart (code changed)
docker compose restart kai-mcp-api          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-mcp-api`.

## 4. Dependencies
- Needs (fix these first if down): `kai-worker-api`
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| tools unavailable to agents | mcp-api down or worker-api dep down | Restart; confirm kai-worker-api healthy first. |

**Notes.** Depends on kai-worker-api.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

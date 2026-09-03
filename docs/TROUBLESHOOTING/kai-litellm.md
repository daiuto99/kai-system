# TROUBLESHOOTING — `kai-litellm`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-litellm`.

**Role.** LiteLLM gateway (:4000) — model routing incl. qwen-mid -> kai-mini with worker fallback.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-litellm sh -c "python3 -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:4000/health/liveliness\", timeout=5)' 2>/dev/null || exit 1"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-litellm`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-litellm
docker logs --tail 100 -f kai-litellm   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d kai-litellm            # (re)create from image
docker compose restart kai-litellm          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/kai-litellm`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `127.0.0.1:4000:4000`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| qwen-mid 5xx / no route | kai-mini down and fallback misconfigured | Check `/v1/models` exposes qwen-mid + qwen-mid-worker; verify litellm/config.yaml routing (project_langfuse_observability instrument point). |
| config change not taking | config.yaml is a RO mount | Edit `~/kai-system/litellm/config.yaml` then restart the container. |

**Notes.** Health probe hits /health/liveliness. Config is a RO file mount.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

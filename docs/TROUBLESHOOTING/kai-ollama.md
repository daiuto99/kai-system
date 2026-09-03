# TROUBLESHOOTING — `kai-ollama`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-ollama`.

**Role.** Local Ollama (:11434) — local inference models.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-ollama sh -c "ollama list"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-ollama`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-ollama
docker logs --tail 100 -f kai-ollama   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d kai-ollama            # (re)create from image
docker compose restart kai-ollama          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/kai-ollama`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `127.0.0.1:11434:11434`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| models missing | volume not mounted / models not pulled | `docker exec kai-ollama ollama list`; re-pull if empty. |

**Notes.** Health probe is `ollama list`. Data in named volume ollama_data.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

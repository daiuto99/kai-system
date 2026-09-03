# TROUBLESHOOTING — `kai-buzz-shim`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-buzz-shim`.

**Role.** OpenAI-compatible shim (:4001) backing native Buzz advisors (kai/sky/roads/coach).

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-buzz-shim sh -c "python3 -c import urllib.request; urllib.request.urlopen('http://localhost:4001/v1/models', timeout=5)"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-buzz-shim`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-buzz-shim
docker logs --tail 100 -f kai-buzz-shim   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-buzz-shim   # rebuild + restart (code changed)
docker compose restart kai-buzz-shim          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-buzz-shim`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `127.0.0.1:4001:4001`, `100.78.94.80:4001:4001`
- Restart policy: `always`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| Buzz DMs silent for days | shim orphaned by a container cleanup sweep | This exact failure caused an 11-day DM outage (KAI-1108). It is NOT the orphaned agents_bridge.py. Restart the shim and confirm `/v1/models` responds. |

**Notes.** restart=always. Health probe hits /v1/models. See project_buzz_advisor_backend.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

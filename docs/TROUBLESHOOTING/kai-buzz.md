# TROUBLESHOOTING — `kai-buzz`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-buzz`.

**Role.** Buzz agent runtime — desktop-app-bound, DM-only native agents.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' kai-buzz 2>/dev/null || docker inspect -f '{{.State.Status}}' kai-buzz
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-buzz
docker logs --tail 100 -f kai-buzz   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-buzz   # rebuild + restart (code changed)
docker compose restart kai-buzz          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-buzz`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: none published
- Restart policy: `always`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| agents unresponsive | runtime crashed | restart=always should recover it; if crash-looping, read logs for auth/session errors. |

**Notes.** restart=always. No compose healthcheck. Mounts buzz-agent + vault RW.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

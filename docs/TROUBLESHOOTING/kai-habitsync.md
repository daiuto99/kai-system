# TROUBLESHOOTING — `kai-habitsync`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-habitsync`.

**Role.** HabitSync (:6842) — habit tracking backend.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:6842/
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-habitsync
docker logs --tail 100 -f kai-habitsync   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d kai-habitsync            # (re)create from image
docker compose restart kai-habitsync          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/kai-habitsync`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `127.0.0.1:6842:6842`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| habits not syncing | container stopped | Restart; data in named volume habitsync_data. |

**Notes.** Third-party image. Bound to 127.0.0.1.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

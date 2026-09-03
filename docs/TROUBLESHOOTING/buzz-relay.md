# TROUBLESHOOTING — `buzz-relay`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `buzz-relay`.

**Role.** Buzz relay (:3000) — message transport for Buzz.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:3000/
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 buzz-relay
docker logs --tail 100 -f buzz-relay   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d buzz-relay            # (re)create from image
docker compose restart buzz-relay          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/buzz-relay`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `127.0.0.1:3000:3000`, `100.78.94.80:3000:3000`
- Restart policy: `always`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| Buzz transport down | relay container stopped | restart=always; if image pull needed, note it is a pinned tag (buzz-relay:head-eed74bd). |

**Notes.** restart=always. Pinned image tag — do not `latest` it blindly.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

# TROUBLESHOOTING — `buzz-hostproxy`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `buzz-hostproxy`.

**Role.** nginx host proxy fronting buzz-relay (config: buzz-relay/hostproxy.conf).

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
docker exec buzz-hostproxy nginx -t
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 buzz-hostproxy
docker logs --tail 100 -f buzz-hostproxy   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d buzz-hostproxy            # (re)create from image
docker compose restart buzz-hostproxy          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/buzz-hostproxy`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: none published
- Restart policy: `always`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| proxy 502 | relay upstream down or bad nginx conf | Fix buzz-relay first; validate conf with `nginx -t`. |

**Notes.** restart=always. Reloads config from a RO mount — edit hostproxy.conf then restart.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

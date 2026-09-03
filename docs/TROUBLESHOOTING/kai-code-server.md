# TROUBLESHOOTING — `kai-code-server`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-code-server`.

**Role.** code-server IDE (:8443) — browser VS Code over Tailscale.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:8443/
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-code-server
docker logs --tail 100 -f kai-code-server   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d kai-code-server            # (re)create from image
docker compose restart kai-code-server          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/kai-code-server`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `100.78.94.80:8443:8080`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| IDE unreachable | container stopped | Restart; mounts /home/leo RW so edits persist. |

**Notes.** Third-party image. Tailscale-bound :8443.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

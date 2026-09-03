# TROUBLESHOOTING — `kai-web`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-web`.

**Role.** Dashboard SPA + nginx (:3001->80, :8080). Tailscale-only; public :3001/chat returns 403 by design.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:3001/
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-web
docker logs --tail 100 -f kai-web   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-web   # rebuild + restart (code changed)
docker compose restart kai-web          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-web`.

## 4. Dependencies
- Needs (fix these first if down): `kai-worker-api`, `kai-council-api`
- Ports: `3001:80`, `8080:8080`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| dashboard 502/blank | worker-api or council-api down (nginx upstreams) | Fix the API deps first; then `docker compose up -d --build kai-web`. |
| stale UI after deploy | browser/nginx cache | Hard-reload; confirm the build actually rebuilt (no cache) via `--build`. |

**Notes.** No compose healthcheck. Depends on kai-worker-api + kai-council-api. Public 403 on /chat is intended (project_stable_gotchas).

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

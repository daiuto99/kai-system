# TROUBLESHOOTING — `kai-worker-api`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-worker-api`.

**Role.** The spine API (:8001) — session brief/close engine, Plane bridge, vault I/O, system health. If this is down, sessions cannot boot or close.

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-worker-api sh -c "curl -fs http://localhost:8001/health && getent hosts kai-worker-api >/dev/null"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-worker-api`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-worker-api
docker logs --tail 100 -f kai-worker-api   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-worker-api   # rebuild + restart (code changed)
docker compose restart kai-worker-api          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-worker-api`.

## 4. Dependencies
- Needs (fix these first if down): `docker-socket-proxy`
- Ports: `100.78.94.80:8001:8001`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| `/session/brief` returns 401 | Missing/rotated worker auth | Verify `~/kai-system/secrets/kai_worker_auth.txt` exists and matches the caller; auth is Basic user:pass. Fail-closed 401 is correct for unauthenticated calls. |
| brief returns `warmboot_required: true` and stays true after warmboot | sync_plane_state.py failing silently | Run `python3 ~/kai-system/sync_plane_state.py warmboot` on the worker and read its stderr; check Plane reachability. |
| close aborts with vault ENOSPC / disk_pressure RED | Worker root disk near-full (see project_worker_disk_close_gotcha) | Reclaim per the disk playbook before retrying close; `df -h /`. |
| healthcheck failing but process up | DNS self-resolve leg (`getent hosts kai-worker-api`) failing | Restart the container; if persistent, check the compose network. |

**Notes.** Mounts vault RW and sonicink RO. Depends on docker-socket-proxy. Sole writer of /session/brief.last_close.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

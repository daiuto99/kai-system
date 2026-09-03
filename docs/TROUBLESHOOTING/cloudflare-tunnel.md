# TROUBLESHOOTING — `cloudflare-tunnel`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `cloudflare-tunnel`.

**Role.** Cloudflare tunnel — public ingress for kai.sonicink.space / n8n.sonicink.space.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
docker logs --tail 20 cloudflare-tunnel  # look for 'Registered tunnel connection'
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 cloudflare-tunnel
docker logs --tail 100 -f cloudflare-tunnel   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d cloudflare-tunnel            # (re)create from image
docker compose restart cloudflare-tunnel          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/cloudflare-tunnel`.

## 4. Dependencies
- Needs (fix these first if down): `kai-worker-api`, `kai-council-api`
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| public URL 5xx | tunnel disconnected or upstream API down | Check tunnel logs for connection; verify worker-api/council-api healthy. |

**Notes.** No healthcheck. Depends on kai-worker-api + kai-council-api. Pinned cloudflared version.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

# TROUBLESHOOTING — `tailscale`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-tailscale`.

**Role.** Tailscale node (kai-tailscale) — the private network the whole system rides on.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
docker exec kai-tailscale tailscale status | head -5
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 tailscale
docker logs --tail 100 -f kai-tailscale   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d tailscale            # (re)create from image
docker compose restart tailscale          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/tailscale`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| worker unreachable over tailnet | node key expired or authkey stale | Check node key expiry (baseline tracks it); reauth with a fresh authkey in secrets/tailscale_authkey.txt. |

**Notes.** Container name kai-tailscale. Authkey is a RO secret mount.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

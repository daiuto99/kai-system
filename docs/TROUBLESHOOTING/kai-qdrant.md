# TROUBLESHOOTING — `kai-qdrant`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-qdrant`.

**Role.** Qdrant vector DB (:6333) — embeddings/knowledge store.

## 1. Is it healthy?
No compose healthcheck — probe manually:
```bash
curl -fsS http://localhost:6333/healthz
```

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-qdrant
docker logs --tail 100 -f kai-qdrant   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d kai-qdrant            # (re)create from image
docker compose restart kai-qdrant          # restart only
```
Or via the API rail (audited): `POST /admin/redeploy/kai-qdrant`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: `127.0.0.1:6333:6333`
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| qdrant unreachable | container stopped or volume issue | Restart; data is in named volume qdrant-data — do not delete it. |

**Notes.** No compose healthcheck; probe /healthz manually. Bound to 127.0.0.1 only.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

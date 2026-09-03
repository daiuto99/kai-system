# TROUBLESHOOTING — `kai-voice-stt`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-voice-stt`.

**Role.** Speech-to-text service (:8005 internal).

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-voice-stt sh -c "python3 -c import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8005/health').status==200 else 1)"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-voice-stt`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-voice-stt
docker logs --tail 100 -f kai-voice-stt   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-voice-stt   # rebuild + restart (code changed)
docker compose restart kai-voice-stt          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-voice-stt`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| STT 5xx | model not loaded | Check /models mount + logs; restart. |

**Notes.** Health probe hits :8005/health. Mounts voice-models RW.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

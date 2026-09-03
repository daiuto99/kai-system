# TROUBLESHOOTING — `kai-voice-tts`

> Agent-facing repair doc. Read this and act — do not re-derive. Container: `kai-voice-tts`.

**Role.** Text-to-speech service (:8006 internal).

## 1. Is it healthy?
Compose healthcheck (what docker uses):
```bash
docker exec kai-voice-tts sh -c "python3 -c import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8006/health').status==200 else 1)"
```
Or check docker's own verdict: `docker inspect -f '{{.State.Health.Status}}' kai-voice-tts`

## 2. Logs
```bash
cd ~/kai-system
docker compose logs --tail 100 kai-voice-tts
docker logs --tail 100 -f kai-voice-tts   # follow
```

## 3. Restart / redeploy
```bash
cd ~/kai-system
docker compose up -d --build kai-voice-tts   # rebuild + restart (code changed)
docker compose restart kai-voice-tts          # restart only (no code change)
```
Or via the API rail (audited): `POST /admin/redeploy/kai-voice-tts`.

## 4. Dependencies
- No compose `depends_on`.
- Ports: none published
- Restart policy: `unless-stopped`

## 5. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| TTS 5xx | model not loaded | Check /models mount + logs; restart. |

**Notes.** Health probe hits :8006/health. Mounts voice-models RW.

## 6. Escalate
If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.

---
_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._

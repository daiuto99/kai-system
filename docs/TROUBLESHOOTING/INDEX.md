# TROUBLESHOOTING — service repair docs (KAI-1276)

Agent-facing repair docs, one per compose service. A session or Hermes hitting a
sick service reads the matching file and self-repairs from it instead of
re-deriving. Grounded in the live `docker-compose.yml`.

**Fast triage:** `cd ~/kai-system && docker compose ps` — then open the file for any
service that is not `healthy`/`running`.

| Service | Role |
|---|---|
| [`buzz-hostproxy`](buzz-hostproxy.md) | nginx host proxy fronting buzz-relay (config: buzz-relay/hostproxy.conf). |
| [`buzz-relay`](buzz-relay.md) | Buzz relay (:3000) |
| [`cloudflare-tunnel`](cloudflare-tunnel.md) | Cloudflare tunnel |
| [`docker-socket-proxy`](docker-socket-proxy.md) | Read-only docker.sock proxy (:2375 internal) |
| [`kai-buzz`](kai-buzz.md) | Buzz agent runtime |
| [`kai-buzz-shim`](kai-buzz-shim.md) | OpenAI-compatible shim (:4001) backing native Buzz advisors (kai/sky/roads/coach). |
| [`kai-code-server`](kai-code-server.md) | code-server IDE (:8443) |
| [`kai-council-api`](kai-council-api.md) | Council/advisor API (:8002) |
| [`kai-habitsync`](kai-habitsync.md) | HabitSync (:6842) |
| [`kai-litellm`](kai-litellm.md) | LiteLLM gateway (:4000) |
| [`kai-mcp-api`](kai-mcp-api.md) | MCP tool API (:8003) |
| [`kai-ollama`](kai-ollama.md) | Local Ollama (:11434) |
| [`kai-orchestrator`](kai-orchestrator.md) | Orchestrator (:8003 internal) |
| [`kai-qdrant`](kai-qdrant.md) | Qdrant vector DB (:6333) |
| [`kai-scheduler`](kai-scheduler.md) | Scheduler |
| [`kai-voice-stt`](kai-voice-stt.md) | Speech-to-text service (:8005 internal). |
| [`kai-voice-tts`](kai-voice-tts.md) | Text-to-speech service (:8006 internal). |
| [`kai-web`](kai-web.md) | Dashboard SPA + nginx (:3001->80, :8080) |
| [`kai-worker-api`](kai-worker-api.md) | The spine API (:8001) |
| [`n8n`](n8n.md) | n8n automation (:5678) |
| [`tailscale`](tailscale.md) | Tailscale node (kai-tailscale) |

## Doc contract
Each file: **1** health probe · **2** logs · **3** restart/redeploy · **4** dependencies · **5** common failures (symptom→cause→fix) · **6** escalate.
When you diagnose a new failure, add a row to that service's §5 — the docs get
smarter every incident.

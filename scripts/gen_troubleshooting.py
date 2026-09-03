#!/usr/bin/env python3
"""Regenerate agent-facing TROUBLESHOOTING docs from the live docker-compose.yml (KAI-1276).
Usage:  python3 scripts/gen_troubleshooting.py   # run from ~/kai-system
Reads ./docker-compose.yml, writes docs/TROUBLESHOOTING/<service>.md + INDEX.md.
Curated per-service failure knowledge lives in META below — extend it as incidents teach you.
"""
import json, os, sys, yaml

def load_facts():
    d = yaml.safe_load(open("docker-compose.yml"))
    out = {}
    for name, cfg in (d.get("services", {}) or {}).items():
        hc = cfg.get("healthcheck", {}) or {}
        dep = cfg.get("depends_on")
        if isinstance(dep, dict): dep = list(dep.keys())
        out[name] = {
            "image": "build" if cfg.get("build") else cfg.get("image"),
            "restart": cfg.get("restart"), "ports": cfg.get("ports"),
            "health_test": hc.get("test"), "depends": dep,
            "container_name": cfg.get("container_name") or name,
        }
    return out

FACTS = load_facts()
COMPOSE = "cd ~/kai-system"
OUT = "docs/TROUBLESHOOTING"
os.makedirs(OUT, exist_ok=True)

META = {
 "kai-worker-api": {
  "role": "The spine API (:8001) — session brief/close engine, Plane bridge, vault I/O, system health. If this is down, sessions cannot boot or close.",
  "failures": [
    ("`/session/brief` returns 401", "Missing/rotated worker auth", "Verify `~/kai-system/secrets/kai_worker_auth.txt` exists and matches the caller; auth is Basic user:pass. Fail-closed 401 is correct for unauthenticated calls."),
    ("brief returns `warmboot_required: true` and stays true after warmboot", "sync_plane_state.py failing silently", "Run `python3 ~/kai-system/sync_plane_state.py warmboot` on the worker and read its stderr; check Plane reachability."),
    ("close aborts with vault ENOSPC / disk_pressure RED", "Worker root disk near-full (see project_worker_disk_close_gotcha)", "Reclaim per the disk playbook before retrying close; `df -h /`."),
    ("healthcheck failing but process up", "DNS self-resolve leg (`getent hosts kai-worker-api`) failing", "Restart the container; if persistent, check the compose network."),
  ],
  "notes": "Mounts vault RW and sonicink RO. Depends on docker-socket-proxy. Sole writer of /session/brief.last_close.",
 },
 "kai-council-api": {
  "role": "Council/advisor API (:8002) — advisor routing, DMs, intake. Depends on worker-api.",
  "failures": [
    ("advisor endpoints 5xx", "worker-api down (dependency)", "Fix kai-worker-api first, then restart this."),
    ("advisor replies empty/stale", "buzz-shim backend (:4001) down", "Check kai-buzz-shim health — native advisors are backed by it (project_buzz_advisor_backend)."),
  ],
  "notes": "Mounts vault RW. Depends on kai-worker-api.",
 },
 "kai-web": {
  "role": "Dashboard SPA + nginx (:3001->80, :8080). Tailscale-only; public :3001/chat returns 403 by design.",
  "manual_health": "curl -fsS -o /dev/null -w '%{http_code}\\n' http://localhost:3001/",
  "failures": [
    ("dashboard 502/blank", "worker-api or council-api down (nginx upstreams)", "Fix the API deps first; then `docker compose up -d --build kai-web`."),
    ("stale UI after deploy", "browser/nginx cache", "Hard-reload; confirm the build actually rebuilt (no cache) via `--build`."),
  ],
  "notes": "No compose healthcheck. Depends on kai-worker-api + kai-council-api. Public 403 on /chat is intended (project_stable_gotchas).",
 },
 "kai-orchestrator": {
  "role": "Orchestrator (:8003 internal) — hostops deploy rail, approval gates, WordPress build-draft, devops self-modify.",
  "failures": [
    ("hostops rail canary fails", "deploy-key secret not resolving end-to-end", "Check `/run/hostops-deploy-keys` mount + kai_publish_gate_secret; see KAI-1166 rail canary."),
    ("gates stuck pending", "resolver not polling", "Inspect `/orchestrator/gates/{id}/resolve`; restart orchestrator."),
  ],
  "notes": "Mounts kai-system RW + hostops secrets. Note: its internal port is 8003 (same as mcp-api's internal health path but a different container).",
 },
 "kai-mcp-api": {
  "role": "MCP tool API (:8003) — exposes KAI tools (tasks, vault, wordpress, etc.) to agents.",
  "failures": [
    ("tools unavailable to agents", "mcp-api down or worker-api dep down", "Restart; confirm kai-worker-api healthy first."),
  ],
  "notes": "Depends on kai-worker-api.",
 },
 "kai-scheduler": {
  "role": "Scheduler — cron jobs, backups, and the LIVE Telegram inbound long-poll (project_telegram_inbound_transport).",
  "failures": [
    ("Telegram inbound dead", "long-poll loop crashed", "Restart kai-scheduler; the webhook path in worker-api is dead by design — inbound is here."),
    ("backups not running", "scheduler down or git config mounts missing", "Check logs; verify the `.git/config` RO mounts resolve."),
  ],
  "notes": "No compose healthcheck. Depends on docker-socket-proxy + kai-worker-api. Mounts plane RO + backups RO.",
 },
 "kai-buzz-shim": {
  "role": "OpenAI-compatible shim (:4001) backing native Buzz advisors (kai/sky/roads/coach).",
  "failures": [
    ("Buzz DMs silent for days", "shim orphaned by a container cleanup sweep", "This exact failure caused an 11-day DM outage (KAI-1108). It is NOT the orphaned agents_bridge.py. Restart the shim and confirm `/v1/models` responds."),
  ],
  "notes": "restart=always. Health probe hits /v1/models. See project_buzz_advisor_backend.",
 },
 "kai-buzz": {
  "role": "Buzz agent runtime — desktop-app-bound, DM-only native agents.",
  "manual_health": "docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' kai-buzz 2>/dev/null || docker inspect -f '{{.State.Status}}' kai-buzz",
  "failures": [
    ("agents unresponsive", "runtime crashed", "restart=always should recover it; if crash-looping, read logs for auth/session errors."),
  ],
  "notes": "restart=always. No compose healthcheck. Mounts buzz-agent + vault RW.",
 },
 "buzz-relay": {
  "role": "Buzz relay (:3000) — message transport for Buzz.",
  "manual_health": "curl -fsS -o /dev/null -w '%{http_code}\\n' http://localhost:3000/",
  "failures": [
    ("Buzz transport down", "relay container stopped", "restart=always; if image pull needed, note it is a pinned tag (buzz-relay:head-eed74bd)."),
  ],
  "notes": "restart=always. Pinned image tag — do not `latest` it blindly.",
 },
 "buzz-hostproxy": {
  "role": "nginx host proxy fronting buzz-relay (config: buzz-relay/hostproxy.conf).",
  "manual_health": "docker exec buzz-hostproxy nginx -t",
  "failures": [
    ("proxy 502", "relay upstream down or bad nginx conf", "Fix buzz-relay first; validate conf with `nginx -t`."),
  ],
  "notes": "restart=always. Reloads config from a RO mount — edit hostproxy.conf then restart.",
 },
 "kai-litellm": {
  "role": "LiteLLM gateway (:4000) — model routing incl. qwen-mid -> kai-mini with worker fallback.",
  "failures": [
    ("qwen-mid 5xx / no route", "kai-mini down and fallback misconfigured", "Check `/v1/models` exposes qwen-mid + qwen-mid-worker; verify litellm/config.yaml routing (project_langfuse_observability instrument point)."),
    ("config change not taking", "config.yaml is a RO mount", "Edit `~/kai-system/litellm/config.yaml` then restart the container."),
  ],
  "notes": "Health probe hits /health/liveliness. Config is a RO file mount.",
 },
 "kai-ollama": {
  "role": "Local Ollama (:11434) — local inference models.",
  "failures": [
    ("models missing", "volume not mounted / models not pulled", "`docker exec kai-ollama ollama list`; re-pull if empty."),
  ],
  "notes": "Health probe is `ollama list`. Data in named volume ollama_data.",
 },
 "kai-qdrant": {
  "role": "Qdrant vector DB (:6333) — embeddings/knowledge store.",
  "manual_health": "curl -fsS http://localhost:6333/healthz",
  "failures": [
    ("qdrant unreachable", "container stopped or volume issue", "Restart; data is in named volume qdrant-data — do not delete it."),
  ],
  "notes": "No compose healthcheck; probe /healthz manually. Bound to 127.0.0.1 only.",
 },
 "n8n": {
  "role": "n8n automation (:5678) — workflows. Container name is kai-n8n.",
  "failures": [
    ("workflows not firing", "n8n down or workflow deactivated", "Check /healthz; verify the workflow is active in the UI."),
  ],
  "notes": "Container name kai-n8n (not n8n). Mounts vault RW + ./n8n-data.",
 },
 "docker-socket-proxy": {
  "role": "Read-only docker.sock proxy (:2375 internal) — lets worker-api/scheduler query docker without raw socket access.",
  "failures": [
    ("worker-api/scheduler can't see containers", "proxy down", "Restart it FIRST — worker-api and scheduler depend on it."),
  ],
  "notes": "Mounts docker.sock RO. A dependency for kai-worker-api and kai-scheduler.",
 },
 "cloudflare-tunnel": {
  "role": "Cloudflare tunnel — public ingress for kai.sonicink.space / n8n.sonicink.space.",
  "manual_health": "docker logs --tail 20 cloudflare-tunnel  # look for 'Registered tunnel connection'",
  "failures": [
    ("public URL 5xx", "tunnel disconnected or upstream API down", "Check tunnel logs for connection; verify worker-api/council-api healthy."),
  ],
  "notes": "No healthcheck. Depends on kai-worker-api + kai-council-api. Pinned cloudflared version.",
 },
 "tailscale": {
  "role": "Tailscale node (kai-tailscale) — the private network the whole system rides on.",
  "manual_health": "docker exec kai-tailscale tailscale status | head -5",
  "failures": [
    ("worker unreachable over tailnet", "node key expired or authkey stale", "Check node key expiry (baseline tracks it); reauth with a fresh authkey in secrets/tailscale_authkey.txt."),
  ],
  "notes": "Container name kai-tailscale. Authkey is a RO secret mount.",
 },
 "kai-voice-stt": {
  "role": "Speech-to-text service (:8005 internal).",
  "failures": [
    ("STT 5xx", "model not loaded", "Check /models mount + logs; restart."),
  ],
  "notes": "Health probe hits :8005/health. Mounts voice-models RW.",
 },
 "kai-voice-tts": {
  "role": "Text-to-speech service (:8006 internal).",
  "failures": [
    ("TTS 5xx", "model not loaded", "Check /models mount + logs; restart."),
  ],
  "notes": "Health probe hits :8006/health. Mounts voice-models RW.",
 },
 "kai-code-server": {
  "role": "code-server IDE (:8443) — browser VS Code over Tailscale.",
  "manual_health": "curl -fsS -o /dev/null -w '%{http_code}\\n' http://localhost:8443/",
  "failures": [
    ("IDE unreachable", "container stopped", "Restart; mounts /home/leo RW so edits persist."),
  ],
  "notes": "Third-party image. Tailscale-bound :8443.",
 },
 "kai-habitsync": {
  "role": "HabitSync (:6842) — habit tracking backend.",
  "manual_health": "curl -fsS -o /dev/null -w '%{http_code}\\n' http://localhost:6842/",
  "failures": [
    ("habits not syncing", "container stopped", "Restart; data in named volume habitsync_data."),
  ],
  "notes": "Third-party image. Bound to 127.0.0.1.",
 },
}


def restart_block(svc, facts):
    is_build = facts.get("image") == "build"
    lines = [f"```bash", COMPOSE]
    if is_build:
        lines.append(f"docker compose up -d --build {svc}   # rebuild + restart (code changed)")
        lines.append(f"docker compose restart {svc}          # restart only (no code change)")
    else:
        lines.append(f"docker compose up -d {svc}            # (re)create from image")
        lines.append(f"docker compose restart {svc}          # restart only")
    lines.append("```")
    lines.append(f"Or via the API rail (audited): `POST /admin/redeploy/{svc}`.")
    return "\n".join(lines)


def health_block(svc, facts, meta):
    test = facts.get("health_test")
    cn = facts.get("container_name") or svc
    out = []
    if test:
        if test[0] in ("CMD-SHELL",):
            cmd = test[1]
        elif test[0] == "CMD":
            cmd = " ".join(test[1:])
        else:
            cmd = " ".join(test)
        out.append("Compose healthcheck (what docker uses):")
        out.append(f"```bash\ndocker exec {cn} sh -c {json.dumps(cmd)}\n```")
        out.append(f"Or check docker's own verdict: `docker inspect -f '{{{{.State.Health.Status}}}}' {cn}`")
    elif meta.get("manual_health"):
        out.append("No compose healthcheck — probe manually:")
        out.append(f"```bash\n{meta['manual_health']}\n```")
    else:
        out.append(f"No healthcheck defined. Check it is running: `docker inspect -f '{{{{.State.Status}}}}' {cn}`")
    return "\n".join(out)


def render(svc, facts):
    meta = META.get(svc, {})
    cn = facts.get("container_name") or svc
    role = meta.get("role", "(role undocumented — fill in)")
    deps = facts.get("depends") or []
    ports = facts.get("ports") or []
    md = []
    md.append(f"# TROUBLESHOOTING — `{svc}`")
    md.append("")
    md.append(f"> Agent-facing repair doc. Read this and act — do not re-derive. Container: `{cn}`.")
    md.append("")
    md.append(f"**Role.** {role}")
    md.append("")
    md.append("## 1. Is it healthy?")
    md.append(health_block(svc, facts, meta))
    md.append("")
    md.append("## 2. Logs")
    md.append(f"```bash\n{COMPOSE}\ndocker compose logs --tail 100 {svc}\ndocker logs --tail 100 -f {cn}   # follow\n```")
    md.append("")
    md.append("## 3. Restart / redeploy")
    md.append(restart_block(svc, facts))
    md.append("")
    md.append("## 4. Dependencies")
    if deps:
        md.append(f"- Needs (fix these first if down): {', '.join('`'+d+'`' for d in deps)}")
    else:
        md.append("- No compose `depends_on`.")
    md.append(f"- Ports: {', '.join('`'+p+'`' for p in ports) if ports else 'none published'}")
    md.append(f"- Restart policy: `{facts.get('restart')}`")
    md.append("")
    md.append("## 5. Common failures")
    fails = meta.get("failures", [])
    if fails:
        md.append("| Symptom | Likely cause | Fix |")
        md.append("|---|---|---|")
        for s, c, f in fails:
            md.append(f"| {s} | {c} | {f} |")
    else:
        md.append("_None catalogued yet. When you diagnose one, add it here (that is the point of this doc)._")
    if meta.get("notes"):
        md.append("")
        md.append(f"**Notes.** {meta['notes']}")
    md.append("")
    md.append("## 6. Escalate")
    md.append("If a restart/redeploy does not resolve it, or the fix touches secrets/deploy config: file a `[BUG]` in Plane with the symptom + logs, keep working what is not blocked, and surface one line to Leo. Do not silently retry destructive actions.")
    md.append("")
    md.append("---")
    md.append("_Generated for KAI-1276. Facts sourced live from `docker-compose.yml`; keep in sync when the service changes._")
    md.append("")
    return "\n".join(md)


services = list(FACTS.keys())
for svc in services:
    with open(os.path.join(OUT, f"{svc}.md"), "w") as fh:
        fh.write(render(svc, FACTS[svc]))

# INDEX
idx = ["# TROUBLESHOOTING — service repair docs (KAI-1276)", "",
       "Agent-facing repair docs, one per compose service. A session or Hermes hitting a",
       "sick service reads the matching file and self-repairs from it instead of",
       "re-deriving. Grounded in the live `docker-compose.yml`.", "",
       "**Fast triage:** `cd ~/kai-system && docker compose ps` — then open the file for any",
       "service that is not `healthy`/`running`.", "",
       "| Service | Role |", "|---|---|"]
for svc in sorted(services):
    role = META.get(svc, {}).get("role", "")
    role_short = role.split(" — ")[0].split(". ")[0][:90]
    idx.append(f"| [`{svc}`]({svc}.md) | {role_short} |")
idx += ["", "## Doc contract", "Each file: **1** health probe · **2** logs · **3** restart/redeploy · "
        "**4** dependencies · **5** common failures (symptom→cause→fix) · **6** escalate.",
        "When you diagnose a new failure, add a row to that service's §5 — the docs get",
        "smarter every incident.", ""]
with open(os.path.join(OUT, "INDEX.md"), "w") as fh:
    fh.write("\n".join(idx))

print(f"Wrote {len(services)} service docs + INDEX.md to {OUT}")
print("Services:", ", ".join(sorted(services)))

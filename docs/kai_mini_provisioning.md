# kai-mini (Intel mini) — provisioning notes

Node: `kai-mini` @ tailnet `100.85.243.2` (Ubuntu 24.04, reimaged from macOS — KAI-1190;
re-enrolled KAI-1191). Role: always-on advisor / local-inference node. Serves **qwen-mid**
via ollama; litellm on the worker routes `qwen-mid` PRIMARY → `http://100.85.243.2:11434`
with `qwen-mid-worker` fallback (`kai-system/litellm/config.yaml`).

## ollama — tailnet-only bind (HARDEN, [MR3] 2026-08-22)

Stock ollama defaults to `OLLAMA_HOST=0.0.0.0:11434` → listens on **every** interface
(LAN + tailnet), unauthenticated. Restricted to the tailnet interface only via a systemd
drop-in, so it is reachable over Tailscale (litellm route) but not on the LAN/physical NIC.

`/etc/systemd/system/ollama.service.d/override.conf`:

```ini
[Unit]
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Environment="OLLAMA_HOST=100.85.243.2:11434"
```

Apply:

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
sudo ss -tlnp | grep 11434   # expect: LISTEN 100.85.243.2:11434  (NOT *:11434)
```

`After=tailscaled.service` guards boot ordering (bind needs the tailnet IP assigned first).
If ollama ever fails to bind on boot, `qwen-mid` degrades to the worker via the litellm
fallback rather than breaking — but the mini's ollama should be repaired.

Reproduce this on any reimage of the node.

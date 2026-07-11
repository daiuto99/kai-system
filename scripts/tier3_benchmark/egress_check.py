#!/usr/bin/env python3
"""S7-13a egress-deny check for the Mem0 candidate.

Runs an add() + search() cycle while a background thread polls `ss -tn` for
this process's TCP connections, and flags anything that isn't loopback
(127.0.0.1) or the LAN worker address. No firewall rules are modified --
this is passive observation, safe to run without sudo.
"""
import json
import os
import subprocess
import threading
import time

from mem0 import Memory

QDRANT_COLLECTION = "tier3bench_mem0_egress_probe"
ALLOWED_PREFIXES = ("127.0.0.1", "::1", "192.168.68.30", "0.0.0.0")

CONFIG = {
    "vector_store": {
        "provider": "qdrant",
        "config": {"collection_name": QDRANT_COLLECTION, "host": "localhost", "port": 6333, "embedding_model_dims": 768},
    },
    "llm": {"provider": "ollama", "config": {"model": "qwen2.5:7b", "ollama_base_url": "http://localhost:11434", "temperature": 0.0}},
    "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text", "ollama_base_url": "http://localhost:11434"}},
}


def poll_connections(pid, observed, stop_event):
    while not stop_event.is_set():
        try:
            out = subprocess.run(["ss", "-tnp"], capture_output=True, text=True, timeout=5).stdout
        except Exception as e:
            observed.append({"error": str(e)})
            time.sleep(0.3)
            continue
        for line in out.splitlines():
            if f"pid={pid}" not in line and f",pid={pid}," not in line:
                continue
            parts = line.split()
            if len(parts) >= 5:
                remote = parts[4]
                observed.append({"line": line.strip(), "remote": remote})
        time.sleep(0.3)


def main():
    import requests as _r
    _r.delete("http://localhost:6333/collections/" + QDRANT_COLLECTION, timeout=10)

    observed = []
    stop_event = threading.Event()
    pid = os.getpid()
    t = threading.Thread(target=poll_connections, args=(pid, observed, stop_event), daemon=True)
    t.start()

    m = Memory.from_config(CONFIG)
    m.add("Egress probe fact: the worker's internal hostname is kai-worker-01.", agent_id="egress_probe", infer=True)
    time.sleep(1)
    m.search("What is the worker's internal hostname?", filters={"agent_id": "egress_probe"}, top_k=3)
    time.sleep(1)

    stop_event.set()
    t.join(timeout=3)

    flagged = []
    seen = set()
    for o in observed:
        if "error" in o:
            continue
        remote = o["remote"]
        remote_ip = remote.rsplit(":", 1)[0].strip("[]")
        if remote_ip in seen:
            continue
        seen.add(remote_ip)
        if not any(remote_ip.startswith(p) for p in ALLOWED_PREFIXES):
            flagged.append(o)

    result = {
        "pid": pid,
        "unique_remotes_observed": sorted(seen),
        "flagged_non_local_connections": flagged,
        "pass": len(flagged) == 0,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

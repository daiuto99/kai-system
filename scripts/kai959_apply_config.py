#!/usr/bin/env python3
"""KAI-959: apply the locked hardened default profile to Hermes config.yaml.
Line-based insertion under `terminal:` so trailing comment blocks are preserved.
Idempotent: re-running does not duplicate keys. Backs up once."""
import shutil, os, sys

P = "/home/leo/.hermes/config.yaml"
BAK = P + ".pre-KAI959"

src = open(P).read()
lines = src.splitlines()

# Idempotency guard
if any(l.strip().startswith("docker_run_as_host_user:") for l in lines):
    print("ALREADY-APPLIED: docker_run_as_host_user present; no change.")
    sys.exit(0)

if not os.path.exists(BAK):
    shutil.copy(P, BAK)
    print(f"BACKUP: {BAK}")

block = [
    "  docker_run_as_host_user: true",
    "  docker_network: true",
    "  docker_extra_args:",
    "  - --read-only",
    "  - --network",
    "  - hermes-ember",
]

out, inserted = [], False
for l in lines:
    out.append(l)
    if not inserted and l.rstrip() == "terminal:":
        out.extend(block)
        inserted = True

if not inserted:
    print("ERROR: no `terminal:` section found; aborting.", file=sys.stderr)
    sys.exit(2)

open(P, "w").write("\n".join(out) + "\n")

# Verify it still parses as YAML and the keys landed under terminal.
import yaml
cfg = yaml.safe_load(open(P))
t = cfg.get("terminal", {})
assert t.get("docker_run_as_host_user") is True, "key not under terminal"
assert t.get("docker_network") is True
assert t.get("docker_extra_args") == ["--read-only", "--network", "hermes-ember"], t.get("docker_extra_args")
print("APPLIED + PARSED OK: terminal.docker_extra_args =", t.get("docker_extra_args"))

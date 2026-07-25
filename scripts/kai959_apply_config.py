#!/usr/bin/env python3
"""KAI-959: apply the locked hardened default profile to Hermes config.yaml.
Line-based insertion under `terminal:` so trailing comment blocks are preserved.
Idempotent (YAML-parsed guard, not a loose text match — finding #4). Validates a
staged copy BEFORE replacing the live file. Backs up once."""
import shutil, os, sys, tempfile, yaml

P = "/home/leo/.hermes/config.yaml"
BAK = P + ".pre-KAI959"

EXPECT_EXTRA = ["--read-only", "--network", "hermes-ember"]

def hardened(path):
    """True iff terminal.* already carries the exact hardened profile."""
    t = (yaml.safe_load(open(path)) or {}).get("terminal", {})
    ex = t.get("docker_extra_args", []) or []
    return (t.get("docker_run_as_host_user") is True
            and t.get("docker_network") is True
            and "--read-only" in ex
            and ex.count("--network") == 1
            and ex[ex.index("--network") + 1] == "hermes-ember")

# Idempotency: parse the real config, not a substring.
if hardened(P):
    print("ALREADY-APPLIED: terminal.* already hardened; no change.")
    sys.exit(0)

lines = open(P).read().splitlines()
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
    print("ERROR: no `terminal:` section found; aborting (no write).", file=sys.stderr)
    sys.exit(2)

# Stage + validate BEFORE touching the live file.
fd, staged = tempfile.mkstemp(suffix=".yaml")
os.close(fd)
open(staged, "w").write("\n".join(out) + "\n")
try:
    if not hardened(staged):
        print("ERROR: staged config did not validate as hardened; live file untouched.", file=sys.stderr)
        sys.exit(3)
    if not os.path.exists(BAK):
        shutil.copy(P, BAK)
        print(f"BACKUP: {BAK}")
    shutil.move(staged, P)
finally:
    if os.path.exists(staged):
        os.remove(staged)

t = (yaml.safe_load(open(P)) or {}).get("terminal", {})
print("APPLIED + VALIDATED: terminal.docker_extra_args =", t.get("docker_extra_args"))

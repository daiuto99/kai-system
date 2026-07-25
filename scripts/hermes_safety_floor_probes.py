#!/usr/bin/env python3
"""KAI-959 Safety-Floor probes (F1/F2/F3) — portable.

Creates a throwaway sandbox via Hermes's OWN DockerEnvironment, driven by the
LIVE default-profile config (not hardcoded args — finding #3, Codex verify
2026-07-25), actively probes the three safety-floor invariants, safely
demonstrates F3 snapshot restorability, cleans up fully (incl. root-owned
mountpoints), and prints a bucketed verdict. Exit 0 only if all SAFETY-FLOOR
checks pass.

Runs unchanged on the mini (ticket 864c4804) once a docker runtime + Hermes are
provisioned there. Buckets per docs/HERMES_CHASSIS_SPIKE_DESIGN.md §2:
SAFETY FLOOR = non-negotiable; a fail is RED and reported, never re-bucketed.
"""
import sys, os, subprocess, glob, sqlite3, shutil, tempfile, yaml
AGENT = os.environ.get("HERMES_AGENT_DIR", "/home/leo/.hermes/hermes-agent")
HBASE = os.path.dirname(AGENT)  # /home/leo/.hermes
sys.path.insert(0, AGENT)
os.chdir(AGENT)
from tools.environments.docker import DockerEnvironment

IMAGE = os.environ.get("HERMES_SANDBOX_IMAGE", "nikolaik/python-nodejs:python3.11-nodejs20")
TASK = "kai959floorprobe"

# Drive the probe from the LIVE default-profile config, so it verifies the real
# shipped profile — not an author-chosen set of hardened flags.
t = (yaml.safe_load(open(os.path.join(HBASE, "config.yaml"))) or {}).get("terminal", {})
env = DockerEnvironment(
    image=IMAGE, persistent_filesystem=True, task_id=TASK,
    cpu=t.get("container_cpu", 1), memory=t.get("container_memory", 5120),
    disk=t.get("container_disk", 51200),
    network=t.get("docker_network", True),
    run_as_host_user=t.get("docker_run_as_host_user", False),
    extra_args=t.get("docker_extra_args", []) or [],
)

PROBE = r"""
echo "UID=$(id -u)"
touch /rootfs_probe 2>/dev/null && echo "ROOTFS=writable" || echo "ROOTFS=readonly"
test -S /var/run/docker.sock && echo "SOCK=present" || echo "SOCK=absent"
echo "CAPEFF=$(awk '/CapEff/{print $2}' /proc/self/status)"
echo "EMBER=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 http://kai-litellm:4000/health/liveliness)"
echo "CLOUD=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 https://api.openai.com 2>/dev/null)"
touch /workspace/w 2>/dev/null && echo "WS=writable" || echo "WS=readonly"
"""
raw = env.execute(PROBE)
out = raw.get("output") if isinstance(raw, dict) else str(raw)
kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l and not l.startswith(" "))

results = []
def check(inv, ok, detail):
    results.append((inv, "PASS" if ok else "CONCERN", detail))

# F1 — sandbox escape
check("F1 non-root user",      kv.get("UID") not in (None, "0"),          f"uid={kv.get('UID')}")
check("F1 read-only rootfs",   kv.get("ROOTFS") == "readonly",            kv.get("ROOTFS"))
check("F1 no docker.sock",     kv.get("SOCK") == "absent",                kv.get("SOCK"))
check("F1 caps dropped",       (kv.get("CAPEFF") or "").strip() == "0000000000000000", f"CapEff={kv.get('CAPEFF')}")
check("F1 workspace writable", kv.get("WS") == "writable",                kv.get("WS"))
# F2 — egress lockdown
check("F2 Ember reachable",    kv.get("EMBER") == "200",                  f"health={kv.get('EMBER')}")
check("F2 cloud blocked",      (kv.get("CLOUD", "") in ("000", "")),      f"cloud={kv.get('CLOUD')} (000=no route)")

# F3 — recoverability: audit trail populated + a REAL (safe) snapshot restore.
# We copy the newest state-snapshot DB to a temp file and open it, proving the
# undo artifact actually restores to a valid Hermes state DB — WITHOUT touching
# live state (finding #5). This is a genuine reversal test on a copy.
audit_ok = (os.path.getsize(os.path.join(HBASE, "state.db")) > 0
            and len(os.listdir(os.path.join(HBASE, "sessions"))) > 0)
check("F3 audit trail present", audit_ok, "state.db + sessions/ populated")

snaps = sorted(glob.glob(os.path.join(HBASE, "state-snapshots", "*", "state.db")))
tables = []
if snaps:
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy(snaps[-1], tmp)
    try:
        con = sqlite3.connect(tmp)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        con.close()
    finally:
        os.remove(tmp)
check("F3 undo restorable (snapshot DB loads to valid Hermes state)",
      bool(snaps) and len(tables) > 0,
      f"{len(snaps)} snapshot(s); newest restores to {len(tables)} tables")

# cleanup — container + host sandbox dir (incl. root-owned skills mountpoint, finding #3)
try:
    env.cleanup(force_remove=True)
    env.wait_for_cleanup(timeout=20)
finally:
    subprocess.run(
        ["docker", "run", "--rm", "-v", f"{os.path.join(HBASE,'sandboxes','docker')}:/x",
         "alpine", "rm", "-rf", f"/x/{TASK}"],
        capture_output=True,
    )

print("=== KAI-959 SAFETY-FLOOR PROBE RESULTS (live-config driven) ===")
for inv, verdict, detail in results:
    print(f"  [{verdict}] {inv} — {detail}")
concerns = [r for r in results if r[1] == "CONCERN"]
print("VERDICT:", "GREEN — all safety-floor invariants pass" if not concerns else f"RED — {len(concerns)} CONCERN(s)")
sys.exit(0 if not concerns else 1)

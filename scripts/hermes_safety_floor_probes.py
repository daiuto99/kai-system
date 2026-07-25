#!/usr/bin/env python3
"""KAI-959 Safety-Floor probes (F1/F2/F3) — portable.

Creates a throwaway sandbox via Hermes's OWN DockerEnvironment under the live
config, actively probes the three safety-floor invariants, prints a bucketed
verdict, and cleans up. Exit 0 only if all SAFETY-FLOOR checks pass.

Runs unchanged on the mini (ticket 864c4804) once a docker runtime + Hermes are
provisioned there. Buckets per docs/HERMES_CHASSIS_SPIKE_DESIGN.md §2:
SAFETY FLOOR = non-negotiable; a fail is RED and reported, never re-bucketed.
"""
import sys, os, json, subprocess
AGENT = os.environ.get("HERMES_AGENT_DIR", "/home/leo/.hermes/hermes-agent")
sys.path.insert(0, AGENT)
os.chdir(AGENT)
from tools.environments.docker import DockerEnvironment

IMAGE = os.environ.get("HERMES_SANDBOX_IMAGE", "nikolaik/python-nodejs:python3.11-nodejs20")
TASK = "kai959floorprobe"

env = DockerEnvironment(
    image=IMAGE, persistent_filesystem=True, task_id=TASK,
    cpu=1, memory=5120, disk=51200, network=True, run_as_host_user=True,
    extra_args=["--read-only", "--network", "hermes-ember"],
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
check("F1 caps dropped",       kv.get("CAPEFF") in ("0000000000000000", "0000000000000000\n"), f"CapEff={kv.get('CAPEFF')}")
# F2 — egress lockdown
check("F2 Ember reachable",    kv.get("EMBER") == "200",                  f"health={kv.get('EMBER')}")
check("F2 cloud blocked",      kv.get("CLOUD", "") in ("000", ""),        f"cloud={kv.get('CLOUD')} (000=no route)")
# F1 functional — writable workspace bind survives read-only rootfs
check("F1 workspace writable", kv.get("WS") == "writable",                kv.get("WS"))

# F3 — recoverability (audit trail + undo path present & populated)
hbase = os.path.dirname(AGENT)  # /home/leo/.hermes
audit_ok = (os.path.getsize(os.path.join(hbase, "state.db")) > 0
            and len(os.listdir(os.path.join(hbase, "sessions"))) > 0)
undo_ok = os.path.isdir(os.path.join(hbase, "state-snapshots")) and \
          len(os.listdir(os.path.join(hbase, "state-snapshots"))) > 0
check("F3 audit trail present", audit_ok, "state.db + sessions/ populated")
check("F3 undo path present",   undo_ok,  "state-snapshots/ restorable")

# cleanup
try:
    env.cleanup(force_remove=True)
    env.wait_for_cleanup(timeout=20)
except Exception as e:
    print(f"(cleanup warning: {e})")

print("=== KAI-959 SAFETY-FLOOR PROBE RESULTS ===")
for inv, verdict, detail in results:
    print(f"  [{verdict}] {inv} — {detail}")
concerns = [r for r in results if r[1] == "CONCERN"]
print("VERDICT:", "GREEN — all safety-floor invariants pass" if not concerns else f"RED — {len(concerns)} CONCERN(s)")
sys.exit(0 if not concerns else 1)

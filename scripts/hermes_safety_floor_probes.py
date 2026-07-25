#!/usr/bin/env python3
"""KAI-959 Safety-Floor probes (F1/F2/F3) — portable.

Creates a throwaway sandbox via Hermes's OWN DockerEnvironment, driven by the
LIVE default-profile config, actively probes the three safety-floor invariants,
performs a REAL snapshot mutate->restore->verify reversal (safely, on a temp DB
seeded from the newest state-snapshot), and cleans up fail-closed (cleanup always
runs and residue removal is verified). Exit 0 only if all SAFETY-FLOOR checks pass.

Runs unchanged on the mini (ticket 864c4804) once docker + Hermes are provisioned.
Buckets per docs/HERMES_CHASSIS_SPIKE_DESIGN.md §2: SAFETY FLOOR = non-negotiable.
"""
import sys, os, subprocess, glob, sqlite3, shutil, tempfile, yaml
AGENT = os.environ.get("HERMES_AGENT_DIR", "/home/leo/.hermes/hermes-agent")
HBASE = os.path.dirname(AGENT)
sys.path.insert(0, AGENT)
os.chdir(AGENT)
from tools.environments.docker import DockerEnvironment

IMAGE = os.environ.get("HERMES_SANDBOX_IMAGE", "nikolaik/python-nodejs:python3.11-nodejs20")
TASK = "kai959floorprobe"

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

results = []
def check(inv, ok, detail):
    results.append((inv, "PASS" if ok else "CONCERN", detail))

def _tables(db):
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()

residue_removed = None
try:
    raw = env.execute(PROBE)
    out = raw.get("output") if isinstance(raw, dict) else str(raw)
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l and not l.startswith(" "))

    # F1 — sandbox escape
    check("F1 non-root user",      kv.get("UID") not in (None, "0"),          f"uid={kv.get('UID')}")
    check("F1 read-only rootfs",   kv.get("ROOTFS") == "readonly",            kv.get("ROOTFS"))
    check("F1 no docker.sock",     kv.get("SOCK") == "absent",                kv.get("SOCK"))
    check("F1 caps dropped",       (kv.get("CAPEFF") or "").strip() == "0000000000000000", f"CapEff={kv.get('CAPEFF')}")
    check("F1 workspace writable", kv.get("WS") == "writable",                kv.get("WS"))
    # F2 — egress lockdown
    check("F2 Ember reachable",    kv.get("EMBER") == "200",                  f"health={kv.get('EMBER')}")
    check("F2 cloud blocked",      kv.get("CLOUD", "") in ("000", ""),        f"cloud={kv.get('CLOUD')} (000=no route)")

    # F3 — audit trail populated
    audit_ok = (os.path.getsize(os.path.join(HBASE, "state.db")) > 0
                and len(os.listdir(os.path.join(HBASE, "sessions"))) > 0)
    check("F3 audit trail present", audit_ok, "state.db + sessions/ populated")

    # F3 — REAL reversal: seed a temp DB from the newest snapshot, mutate it,
    # restore the snapshot over it, and prove the mutation is gone. Safe: only a
    # temp copy is touched; live state is never modified. (Finding #5.)
    snaps = sorted(glob.glob(os.path.join(HBASE, "state-snapshots", "*", "state.db")))
    reversal_ok, detail = False, "no state-snapshot found"
    if snaps:
        work = tempfile.mktemp(suffix=".db")
        try:
            shutil.copy(snaps[-1], work)
            base = _tables(work)
            con = sqlite3.connect(work)
            con.execute("CREATE TABLE kai959_reversal_probe(x)")
            con.execute("INSERT INTO kai959_reversal_probe VALUES (1)")
            con.commit(); con.close()
            mutated = "kai959_reversal_probe" in _tables(work)
            shutil.copy(snaps[-1], work)              # the undo operation
            after = _tables(work)
            reversal_ok = mutated and "kai959_reversal_probe" not in after and after == base
            detail = f"mutated={mutated}, restored_clean={'kai959_reversal_probe' not in after}, tables={len(after)}"
        finally:
            if os.path.exists(work):
                os.remove(work)
    check("F3 undo reversal (mutate -> restore -> mutation gone)", reversal_ok, detail)
finally:
    # Fail-closed cleanup: ALWAYS runs, even if the probe raised. (New issue, Codex.)
    try:
        env.cleanup(force_remove=True)
        env.wait_for_cleanup(timeout=20)
    except Exception as e:
        print(f"(cleanup warning: container teardown: {e})")
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{os.path.join(HBASE,'sandboxes','docker')}:/x",
         "alpine", "rm", "-rf", f"/x/{TASK}"],
        capture_output=True, text=True,
    )
    residue_removed = (r.returncode == 0) and not os.path.exists(
        os.path.join(HBASE, "sandboxes", "docker", TASK))

if residue_removed is False:
    check("probe cleanup (no residue)", False, "sandbox dir remained after cleanup")

print("=== KAI-959 SAFETY-FLOOR PROBE RESULTS (live-config driven) ===")
for inv, verdict, detail in results:
    print(f"  [{verdict}] {inv} — {detail}")
concerns = [r for r in results if r[1] == "CONCERN"]
print("VERDICT:", "GREEN — all safety-floor invariants pass" if not concerns else f"RED — {len(concerns)} CONCERN(s)")
sys.exit(0 if not concerns else 1)

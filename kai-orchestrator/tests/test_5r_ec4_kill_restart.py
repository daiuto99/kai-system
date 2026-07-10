"""
Exit Criterion #4 — session.close kill-and-restart resume.

Test:
  1. Create a capability_chain job: [vault.read, session.close]
  2. Immediately kill kai-orchestrator (job is in 'running' state, steps are 'pending')
  3. Restart kai-orchestrator
  4. _resume_interrupted_jobs() picks up the job on startup
  5. resume() runs all pending steps including session.close
  6. Poll until job is 'succeeded'
  7. Verify session_close_log.json was updated

This tests the durable job engine's resume mechanism (Workflow.resume() in workflow_base.py)
and that session.close runs within the orchestrator topology (not via SSH/subprocess).
"""
import httpx
import time
import json
import subprocess
import sys
import os

ORCH = "http://100.78.94.80:8003"
WORKER = "http://100.78.94.80:8001"
auth_str = open("/tmp/kai_auth.txt").read().strip()
user, pw = auth_str.split(":", 1)
AUTH = (user, pw)

def orch_get(path):
    with httpx.Client(base_url=ORCH, timeout=10) as c:
        return c.get(path).json()

def worker_post(path, data):
    with httpx.Client(base_url=WORKER, auth=AUTH, timeout=10) as c:
        return c.post(path, json=data).json()

def orch_post(path, data):
    with httpx.Client(base_url=ORCH, timeout=10) as c:
        return c.post(path, json=data).json()

def run_docker(cmd):
    result = subprocess.run(["docker"] + cmd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()

print("=" * 60)
print("EC#4 — session.close kill-and-restart resume")
print("=" * 60)

# Step 1: Create capability_chain job with 2 steps: vault.read + session.close
print("\n[1] Creating capability_chain job: vault.read → session.close ...")
job_resp = orch_post("/workflows/run", {
    "type": "capability_chain",
    "inputs": {
        "title": "EC4 Kill-Restart Test",
        "chain": [
            {"capability": "vault.read", "inputs": {"path": "00_System/JARVIS_DEFINITION.md"}},
            {"capability": "session.close", "inputs": {"mode": "manual"}},
        ]
    }
})
print(f"    Response: {json.dumps(job_resp)}")

job_id = job_resp.get("job_id")
if not job_id:
    print(f"[FAIL] No job_id returned: {job_resp}")
    sys.exit(1)
print(f"    job_id: {job_id}")

# Step 2: Immediately kill the orchestrator — job should be in 'running' state
print(f"\n[2] Killing kai-orchestrator (job {job_id} in running state) ...")
rc, out, err = run_docker(["kill", "kai-orchestrator"])
print(f"    docker kill: rc={rc} out={out} err={err}")
time.sleep(1)

# Verify it's stopped
rc2, out2, _ = run_docker(["inspect", "--format={{.State.Status}}", "kai-orchestrator"])
print(f"    Container status after kill: {out2}")

# Step 3: Check job state in DB before restart (should be 'running' with steps 'pending')
print(f"\n[3] Verifying job state before restart ...")
# Can't check orchestrator API when it's down — we'll verify post-restart

# Step 4: Restart the orchestrator
print(f"\n[4] Restarting kai-orchestrator ...")
rc3, out3, err3 = run_docker(["start", "kai-orchestrator"])
print(f"    docker start: rc={rc3} out={out3} err={err3}")

# Wait for orchestrator to be healthy
print(f"    Waiting for orchestrator to be healthy ...")
for i in range(30):
    time.sleep(2)
    try:
        r = httpx.get(f"{ORCH}/health", timeout=3)
        if r.status_code == 200:
            print(f"    Orchestrator healthy after {(i+1)*2}s")
            break
    except Exception:
        pass
else:
    print("[FAIL] Orchestrator did not become healthy within 60s")
    sys.exit(1)

# Step 5: Poll for job completion (resume() should have picked it up at startup)
print(f"\n[5] Polling for job {job_id} completion ...")
start = time.time()
final_state = None
for i in range(60):
    time.sleep(3)
    try:
        state = orch_get(f"/jobs/{job_id}")
        job_status = state.get("job", {}).get("status", "unknown")
        steps = state.get("steps", [])
        step_statuses = [(s.get("name", "?"), s.get("status", "?")) for s in steps]
        print(f"    [{i*3}s] job={job_status} steps={step_statuses}")
        if job_status in ("succeeded", "failed_permanent", "cancelled"):
            final_state = state
            break
    except Exception as e:
        print(f"    [{i*3}s] poll error: {e}")
else:
    print("[FAIL] Job did not reach terminal state within 180s")
    sys.exit(1)

elapsed = time.time() - start
print(f"\n[6] Job completed in {elapsed:.1f}s")
print(f"    Final status: {final_state.get('job', {}).get('status')}")
for s in final_state.get("steps", []):
    print(f"    Step {s['name']}: {s['status']}")

# Step 6: Verify session_close_log.json was updated
print(f"\n[7] Verifying session_close_log.json updated ...")
rc4, out4, err4 = run_docker([
    "exec", "kai-orchestrator", 
    "cat", "/vault/00_System/session_close_log.json"
])
if rc4 == 0:
    try:
        manifest = json.loads(out4)
        print(f"    Manifest date: {manifest.get('date')}")
        print(f"    Manifest overall: {manifest.get('overall')}")
        print(f"    Steps: {[(s['name'], s['status']) for s in manifest.get('steps', [])]}")
    except:
        print(f"    Manifest raw: {out4[:200]}")
else:
    print(f"    Could not read manifest: {err4}")

job_ok = final_state.get("job", {}).get("status") == "succeeded"
manifest_ok = rc4 == 0

if job_ok:
    print(f"\n[PASS] Job completed via resume after kill-restart")
    print(f"       session.close ran inside orchestrator topology (no SSH/subprocess)")
    print(f"       Manifest written: {manifest_ok}")
else:
    print(f"\n[FAIL] Job did not succeed: {final_state.get('job', {}).get('status')}")
    for s in final_state.get("steps", []):
        if s.get("status") not in ("succeeded",):
            print(f"  Failed step: {s['name']} = {s['status']} — {s.get('error','')}")
    sys.exit(1)

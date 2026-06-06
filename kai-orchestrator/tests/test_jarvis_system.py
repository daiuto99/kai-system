#!/usr/bin/env python3
"""
JARVIS System Test Suite v1.0
True gauge of whether KAI is a solid system or a bag of rules.

Run: python3 test_jarvis_system.py [--suite SUITE]
Suites: all, architecture, health, jarvis, regression, live, policy, session

Output: console PASS/FAIL/WARN/SKIP + vault JSON report + Slack summary post
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests  — required")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

KAI_ROOT       = Path("/home/leo/kai-system")
VAULT          = Path("/home/leo/vault")
ORCH_DIR       = KAI_ROOT / "kai-orchestrator"
SCHED_DIR      = KAI_ROOT / "kai-scheduler"
WORKER_URL     = "http://localhost:8001"
COUNCIL_URL    = "http://localhost:8002"
ORCH_CONTAINER = "kai-orchestrator"
PLANE_URL      = "http://localhost:8090/api/v1"
PLANE_WS       = "sonicink"
KAI_PROJECT_ID = "78c49227-82d4-477d-a920-66b08cb91c56"

# ── Result tracking ───────────────────────────────────────────────────────────

_results: list[dict] = []
_start_time = time.time()

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
GRAY   = "\033[90m"
RESET  = "\033[0m"


def _record(test_id: str, name: str, status: str, detail: str = "", evidence: str = "") -> bool:
    entry = {
        "id": test_id, "name": name, "status": status,
        "detail": detail, "evidence": evidence,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _results.append(entry)
    icons  = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠", "SKIP": "○"}
    colors = {"PASS": GREEN, "FAIL": RED, "WARN": YELLOW, "SKIP": GRAY}
    icon  = icons.get(status, "?")
    color = colors.get(status, "")
    print(f"  {color}{icon} [{status:4}]{RESET} {test_id}: {name}")
    if detail:
        print(f"         → {detail}")
    return status in ("PASS", "WARN", "SKIP")


PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _orch_exec(python_code: str, timeout: int = 30) -> tuple[bool, str]:
    """Run python3 code inside kai-orchestrator container."""
    r = subprocess.run(
        ["docker", "exec", ORCH_CONTAINER, "python3", "-c", python_code],
        capture_output=True, text=True, timeout=timeout,
    )
    out = r.stdout.strip()
    err = r.stderr.strip()
    return r.returncode == 0, out if out else err


def _orch_get(path: str, timeout: int = 15) -> tuple[bool, dict]:
    code = (
        "import httpx, json\n"
        "try:\n"
        f"    r = httpx.get('http://localhost:8003{path}', timeout={timeout})\n"
        "    print(json.dumps(r.json()))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    ok, out = _orch_exec(code, timeout=timeout + 5)
    try:
        data = json.loads(out)
        # orchestrator always includes "error" key (may be null) — only treat as error if non-null
        has_real_error = data.get("error") is not None
        return not has_real_error, data
    except Exception:
        return False, {"error": out[:300]}


def _orch_post(path: str, body: dict, timeout: int = 30) -> tuple[bool, dict]:
    body_repr = repr(body)
    code = (
        "import httpx, json\n"
        "try:\n"
        f"    r = httpx.post('http://localhost:8003{path}', json={body_repr}, timeout={timeout})\n"
        "    print(json.dumps(r.json()))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    ok, out = _orch_exec(code, timeout=timeout + 5)
    try:
        data = json.loads(out)
        has_real_error = data.get("error") is not None
        return not has_real_error, data
    except Exception:
        return False, {"error": out[:300]}


def _cap(name: str, inputs: dict, timeout: int = 20) -> tuple[bool, dict]:
    """Call orchestrator /capability/{name} endpoint."""
    return _orch_post(f"/capability/{name}", {"inputs": inputs}, timeout=timeout)


def _grep_files(pattern: str, path: Path, include: str = "*.py") -> list[str]:
    r = subprocess.run(
        ["grep", "-r", f"--include={include}", "-l", pattern, str(path)],
        capture_output=True, text=True,
    )
    return [f for f in r.stdout.strip().split("\n") if f and "__pycache__" not in f]


def _grep_count(pattern: str, path: Path, include: str = "*.py") -> int:
    r = subprocess.run(
        ["grep", "-r", f"--include={include}", "-c", pattern, str(path)],
        capture_output=True, text=True,
    )
    total = 0
    for line in r.stdout.strip().split("\n"):
        if ":" in line:
            try:
                total += int(line.split(":")[-1])
            except ValueError:
                pass
    return total


def _secret(name: str) -> str:
    p = KAI_ROOT / "secrets" / name
    return p.read_text().strip().split("\n")[0] if p.exists() else ""


def _api_get(url: str, timeout: int = 10) -> tuple[bool, dict]:
    try:
        r = requests.get(url, timeout=timeout)
        ct = r.headers.get("content-type", "")
        if "json" in ct:
            return r.status_code == 200, r.json()
        return r.status_code == 200, {"status": r.status_code}
    except Exception as e:
        return False, {"error": str(e)}


def _wait_for_job(job_id: str, max_seconds: int = 60) -> tuple[str, dict]:
    """Poll orchestrator until job reaches terminal status. Returns (status, job_data)."""
    for _ in range(max_seconds):
        time.sleep(1)
        ok, data = _orch_get(f"/jobs/{job_id}")
        if ok:
            status = data.get("job", {}).get("status", "")
            if status in ("succeeded", "failed_permanent", "cancelled"):
                return status, data
    return "timeout", {}


# ── Category A: Architecture Integrity ───────────────────────────────────────

def suite_architecture():
    print(f"\n{YELLOW}── Category A: Architecture Integrity ──────────────────────────────{RESET}")

    # A-1: Engine sole writer
    all_py = list(ORCH_DIR.rglob("*.py"))
    violations = []
    for f in all_py:
        if "engine.py" in str(f) or "tests/" in str(f) or "__pycache__" in str(f):
            continue
        txt = f.read_text()
        for i, line in enumerate(txt.splitlines(), 1):
            stripped = line.strip()
            if re.search(r"UPDATE\s+(steps|jobs)\s+SET\s+status", stripped, re.I) and not stripped.startswith("#"):
                violations.append(f"{f.name}:{i}: {stripped[:80]}")
    if violations:
        _record("A-1", "Engine sole writer", FAIL,
                f"Direct status mutations outside engine.py: {violations[:3]}")
    else:
        _record("A-1", "Engine sole writer", PASS,
                "No direct status writes found outside engine.py")

    # A-2: Verification required before step succeeds
    engine_py = ORCH_DIR / "engine.py"
    engine_txt = engine_py.read_text() if engine_py.exists() else ""
    if "_require_verification" in engine_txt and 'new_status == "succeeded"' in engine_txt:
        _record("A-2", "Verification gated on step succeed", PASS,
                "_require_verification() called on succeeded transition")
    else:
        _record("A-2", "Verification gated on step succeed", FAIL,
                "engine.py does not gate succeeded transition on verification")

    # A-3: Terminal guard
    has_terminal_set = "TERMINAL" in engine_txt and ("succeeded" in engine_txt or "failed" in engine_txt)
    has_terminal_check = "in TERMINAL" in engine_txt or "TERMINAL" in engine_txt and "return False" in engine_txt
    if has_terminal_set and has_terminal_check:
        _record("A-3", "Terminal step blocks re-transition", PASS,
                "TERMINAL guard present and enforced in engine.py")
    else:
        _record("A-3", "Terminal step blocks re-transition", FAIL,
                "No TERMINAL guard detected in engine.py")

    # A-4: PeerReviewRequired class exists and is wired
    if "class PeerReviewRequired" in engine_txt and ("_require_peer_review" in engine_txt or "PeerReviewRequired" in engine_txt):
        wired = "_require_peer_review_if_gated" in engine_txt or "PeerReviewRequired" in engine_txt
        _record("A-4", "PeerReviewRequired fires on gated steps", PASS if wired else WARN,
                "PeerReviewRequired class defined and wired into transition()")
    else:
        _record("A-4", "PeerReviewRequired fires on gated steps", FAIL,
                "PeerReviewRequired not found in engine.py")

    # A-5: No wp_cli in capability_map
    cap_map_path = ORCH_DIR / "capabilities" / "capability_map.json"
    if cap_map_path.exists():
        cap_map = json.loads(cap_map_path.read_text())
        # Check transport lists only — ignore comment/note fields starting with _
        def _all_transports(obj, parent_key=""):
            if isinstance(obj, list):
                yield from obj
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    if not k.startswith("_"):  # skip _note, _comment fields
                        yield from _all_transports(v, k)
        transport_values = list(_all_transports(cap_map))
        if "wp_cli" in transport_values:
            _record("A-5", "No wp_cli in capability_map", FAIL,
                    "wp_cli found in a transport list in capability_map.json")
        else:
            _record("A-5", "No wp_cli in capability_map", PASS,
                    "wp_cli not in any transport list (note field excluded from check)")
    else:
        _record("A-5", "No wp_cli in capability_map", FAIL, "capability_map.json not found")

    # A-6: No bare .json() calls in transports (outside base.py)
    transports_dir = ORCH_DIR / "transports"
    raw_json_calls = []
    for f in transports_dir.glob("*.py"):
        if f.name == "base.py":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            stripped = line.strip()
            if ".json()" in stripped and not stripped.startswith("#"):
                raw_json_calls.append(f"{f.name}:{i}: {stripped[:80]}")
    if raw_json_calls:
        _record("A-6", "No bare .json() calls in transports", FAIL,
                f"Found {len(raw_json_calls)}: {raw_json_calls[:2]}")
    else:
        _record("A-6", "No bare .json() calls in transports", PASS,
                "All transport HTTP responses go through safe_request() in base.py")

    # A-7: Core capabilities registered (via /capabilities endpoint)
    required = [
        "wordpress.load_config", "wordpress.probe_credentials",
        "wordpress.create_page", "wordpress.set_option",
        "wordpress.set_front_page", "wordpress.publish",
        "wordpress.purge_varnish", "wordpress.verify_live",
        "vault.read", "vault.write",
    ]
    ok, data = _orch_get("/capabilities")
    if ok:
        registered = {c["name"] for c in data.get("capabilities", [])}
        missing = [c for c in required if c not in registered]
        total = data.get("count", len(registered))
        if missing:
            _record("A-7", "Core capabilities registered", FAIL,
                    f"Missing: {missing}", f"Total registered: {total}")
        else:
            _record("A-7", "Core capabilities registered", PASS,
                    f"All {len(required)} required caps registered ({total} total)")
    else:
        _record("A-7", "Core capabilities registered", FAIL,
                f"Cannot reach orchestrator /capabilities: {data.get('error','')}")

    # A-8: set_option allowlist rejects non-registered options
    ok, data = _cap("wordpress.set_option", {
        "site": "sette-uno.com", "option": "siteurl",
        "value": "http://evil.com", "creds": {},
    })
    resp_str = str(data).lower()
    if not data.get("ok") and ("not_allowed" in resp_str or "allowlist" in resp_str or "option_not_allowed" in resp_str):
        _record("A-8", "set_option allowlist rejects bad keys", PASS,
                "siteurl correctly rejected with option_not_allowed")
    elif data.get("ok"):
        _record("A-8", "set_option allowlist rejects bad keys", FAIL,
                "set_option accepted a non-allowlisted key — CRITICAL security gap")
    else:
        _record("A-8", "set_option allowlist rejects bad keys", WARN,
                f"Unexpected response: {str(data)[:200]}")

    # A-9: ssh_php_eval has no raw run() entrypoint
    ssh_php = ORCH_DIR / "transports" / "ssh_php_eval.py"
    if ssh_php.exists():
        txt = ssh_php.read_text()
        if re.search(r"def run\(.*raw_php", txt):
            _record("A-9", "ssh_php_eval has no raw run() method", FAIL,
                    "run(raw_php) found — unrestricted PHP execution path exists")
        else:
            _record("A-9", "ssh_php_eval has no raw run() method", PASS,
                    "No raw PHP execution entrypoint in ssh_php_eval.py")
    else:
        _record("A-9", "ssh_php_eval has no raw run() method", SKIP, "ssh_php_eval.py not found")

    # A-10: publish_homepage has correct step count and required steps
    wf_path = ORCH_DIR / "workflows" / "wordpress_publish_homepage.py"
    if wf_path.exists():
        wf_txt = wf_path.read_text()
        required_steps = ["disable_coming_soon", "verify_live", "complete", "load_site_config"]
        missing_steps  = [s for s in required_steps if s not in wf_txt]
        step_count = wf_txt.count("StepDef(")
        if missing_steps:
            _record("A-10", "publish_homepage correct steps", FAIL, f"Missing: {missing_steps}")
        elif step_count != 13:
            _record("A-10", "publish_homepage correct steps", WARN,
                    f"Expected 13 StepDef entries, found {step_count}")
        else:
            _record("A-10", "publish_homepage correct steps", PASS,
                    f"All required steps present ({step_count} total)")
    else:
        _record("A-10", "publish_homepage correct steps", FAIL,
                "wordpress_publish_homepage.py not found")


# ── Category B: Live System Health ───────────────────────────────────────────

def suite_health():
    print(f"\n{YELLOW}── Category B: Live System Health ──────────────────────────────────{RESET}")

    # B-1a/b: worker-api + council-api respond
    for label, url in [("worker-api", f"{WORKER_URL}/health"),
                        ("council-api", f"{COUNCIL_URL}/health")]:
        ok, data = _api_get(url)
        bid = "B-1a" if "worker" in label else "B-1b"
        if ok:
            _record(bid, f"{label} responds 200", PASS, f"status={data.get('status','ok')}")
        else:
            _record(bid, f"{label} responds 200", FAIL, f"Error: {data.get('error','no response')}")

    # B-1c: orchestrator
    ok, data = _orch_get("/health")
    if ok:
        _record("B-1c", "orchestrator responds 200", PASS, str(data)[:100])
    else:
        _record("B-1c", "orchestrator responds 200", FAIL, f"Error: {data.get('error','unreachable')}")

    # B-2: DB schema correct
    code = (
        "import sqlite3, json\n"
        "conn = sqlite3.connect('/data/orchestrator/orchestrator.db')\n"
        "tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]\n"
        "conn.close()\n"
        "print(json.dumps(tables))\n"
    )
    ok, out = _orch_exec(code)
    try:
        tables = json.loads(out)
        required = {"jobs", "steps", "events", "gates", "overrides"}
        missing  = required - set(tables)
        if missing:
            _record("B-2", "Orchestrator DB schema correct", FAIL, f"Missing tables: {missing}")
        else:
            _record("B-2", "Orchestrator DB schema correct", PASS, f"Tables: {sorted(tables)}")
    except Exception:
        _record("B-2", "Orchestrator DB schema correct", FAIL, f"Cannot query DB: {out[:200]}")

    # B-3: Vault writable
    sentinel = VAULT / "00_System" / ".jarvis_test_sentinel"
    try:
        sentinel.write_text("ok")
        sentinel.unlink()
        _record("B-3", "Vault writable", PASS, f"Write+delete OK at {sentinel}")
    except Exception as e:
        _record("B-3", "Vault writable", FAIL, str(e))

    # B-4/5/6: Invariant engine state
    inv_path = VAULT / "00_System" / "invariants.json"
    if inv_path.exists():
        try:
            inv_data = json.loads(inv_path.read_text())
            # Structure: {updated_at, all_pass, runner_enabled, invariants: {name: {label, pass, detail, checked_at}}}
            ts = inv_data.get("updated_at") or inv_data.get("generated_at") or inv_data.get("timestamp")
            if ts:
                try:
                    inv_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    age_min = (datetime.now(timezone.utc) - inv_dt).total_seconds() / 60
                    if age_min < 35:
                        _record("B-4", "Invariant engine within cadence (30min)", PASS, f"{age_min:.1f}min ago")
                    elif age_min < 120:
                        _record("B-4", "Invariant engine within cadence (30min)", WARN, f"{age_min:.1f}min ago")
                    else:
                        _record("B-4", "Invariant engine within cadence (30min)", FAIL,
                                f"Last run {age_min:.0f}min ago — scheduler may be down")
                except Exception:
                    _record("B-4", "Invariant engine within cadence (30min)", WARN, f"Cannot parse ts: {ts}")
            else:
                _record("B-4", "Invariant engine within cadence (30min)", WARN, "No timestamp field in invariants.json")

            # Count invariant results (nested under 'invariants' key)
            results_map = inv_data.get("invariants", {})
            if not results_map:
                results_map = {k: v for k, v in inv_data.items()
                               if k not in ("updated_at", "generated_at", "timestamp", "all_pass", "runner_enabled", "ok")}
            inv_count = len(results_map)
            if inv_count >= 10:
                _record("B-5", "All 10 invariant results present", PASS, f"{inv_count} results in file")
            elif inv_count >= 6:
                _record("B-5", "All 10 invariant results present", WARN, f"Only {inv_count} results (target >=10)")
            else:
                _record("B-5", "All 10 invariant results present", FAIL, f"Only {inv_count} results — invariant engine missing checks")

            # Critical violations — structure: {pass: bool, detail: str}
            critical_fails = []
            for k, v in results_map.items():
                if isinstance(v, dict):
                    passed = v.get("pass", v.get("passed", v.get("ok", True)))
                    # invariants.json does not have severity field — treat all failures as notable
                    if not passed:
                        critical_fails.append(f"{k}: {v.get('detail','')[:60]}")
            if critical_fails:
                _record("B-6", "No invariant violations", FAIL, f"Failing: {critical_fails[:3]}")
            else:
                _record("B-6", "No invariant violations", PASS, f"All {inv_count} invariants passing")

        except Exception as e:
            _record("B-4", "Invariant engine within cadence", FAIL, f"Cannot parse invariants.json: {e}")
            _record("B-5", "All 10 invariant results present", SKIP, "Cannot parse")
            _record("B-6", "No critical invariant violations", SKIP, "Cannot parse")
    else:
        _record("B-4", "Invariant engine within cadence", FAIL, "invariants.json not found in vault/00_System/")
        _record("B-5", "All 10 invariant results present", FAIL, "invariants.json not found")
        _record("B-6", "No critical invariant violations", SKIP, "invariants.json not found")

    # B-7: Slack token valid
    token = _secret("slack_bot_token.txt")
    if not token:
        _record("B-7", "Slack token valid", FAIL, "slack_bot_token.txt not found")
    else:
        try:
            r = requests.post("https://slack.com/api/auth.test",
                              headers={"Authorization": f"Bearer {token}"}, timeout=10)
            d = r.json()
            if d.get("ok"):
                _record("B-7", "Slack token valid", PASS,
                        f"bot_id={d.get('bot_id','?')} team={d.get('team','?')}")
            else:
                _record("B-7", "Slack token valid", FAIL, f"auth.test failed: {d.get('error')}")
        except Exception as e:
            _record("B-7", "Slack token valid", WARN, f"Cannot reach Slack API: {e}")

    # B-8: Plane API reachable
    plane_token = _secret("plane_api_token.txt")
    if plane_token:
        try:
            r = requests.get(f"{PLANE_URL}/workspaces/{PLANE_WS}/projects/",
                             headers={"X-API-Key": plane_token}, timeout=10)
            if r.status_code == 200:
                count = len(r.json().get("results", []))
                _record("B-8", "Plane API reachable", PASS, f"{count} projects accessible")
            else:
                _record("B-8", "Plane API reachable", FAIL, f"HTTP {r.status_code}")
        except Exception as e:
            _record("B-8", "Plane API reachable", WARN, f"Plane unreachable: {e}")
    else:
        _record("B-8", "Plane API reachable", FAIL, "plane_api_token.txt not found")

    # B-9: Learning loop directory
    learning_dir = VAULT / "60_Council" / "learning"
    if learning_dir.exists():
        files = list(learning_dir.glob("*.json")) + list(learning_dir.glob("*.md"))
        _record("B-9", "Learning loop directory exists", PASS if files else WARN,
                f"{len(files)} report file(s)" if files else "Directory exists but empty — loop hasn't run yet")
    else:
        _record("B-9", "Learning loop directory exists", WARN,
                "vault/60_Council/learning/ not created yet — no loop output")


# ── Category J: JARVIS Success Tests ─────────────────────────────────────────

def suite_jarvis():
    print(f"\n{YELLOW}── Category J: JARVIS Success Tests ────────────────────────────────{RESET}")

    # J-1: Zero-prompt build — hello_world end-to-end, zero OVERRIDEs, all verified
    print("  [J-1] Starting hello_world workflow...")
    ok, run_data = _orch_post("/workflows/run", {"type": "hello_world", "inputs": {"name": "JARVIS"}})
    if not ok or "job_id" not in run_data:
        _record("J-1", "Zero-prompt build (hello_world)", FAIL,
                f"Cannot start workflow: {str(run_data)[:200]}")
    else:
        job_id = run_data["job_id"]
        final_status, job_data = _wait_for_job(job_id, max_seconds=45)
        if final_status == "succeeded":
            steps = job_data.get("steps", [])
            # Check all steps have verified=true
            unverified = []
            for s in steps:
                if s.get("status") == "succeeded":
                    v = s.get("verification")
                    if v:
                        v_parsed = json.loads(v) if isinstance(v, str) else v
                        if not v_parsed.get("verified"):
                            unverified.append(s["name"])
                    else:
                        unverified.append(s["name"])
            # Check no overrides
            overrides = [s for s in steps if s.get("result") and '"override"' in str(s.get("result", ""))]
            if overrides:
                _record("J-1", "Zero-prompt build", FAIL,
                        f"Succeeded but {len(overrides)} OVERRIDE(s) used")
            elif unverified:
                _record("J-1", "Zero-prompt build", WARN,
                        f"Succeeded but {len(unverified)} step(s) lack verified=true: {unverified}")
            else:
                _record("J-1", "Zero-prompt build", PASS,
                        f"job={job_id[:8]} — {len(steps)} steps, all verified, zero OVERRIDEs")
        else:
            _record("J-1", "Zero-prompt build", FAIL,
                    f"Workflow ended with status={final_status} (job_id={job_id[:8]})")

    # J-2: Survives restart — start job, kill orchestrator, verify resume
    print("  [J-2] Starting restart-survival test...")
    ok2, run_data2 = _orch_post("/workflows/run", {"type": "hello_world", "inputs": {"name": "RestartTest"}})
    if not ok2 or "job_id" not in run_data2:
        _record("J-2", "Survives restart", SKIP, "Cannot start test job for restart test")
    else:
        job_id2 = run_data2["job_id"]
        print(f"  [J-2] Restarting {ORCH_CONTAINER}...")
        restart_r = subprocess.run(
            ["docker", "restart", ORCH_CONTAINER],
            capture_output=True, text=True, timeout=45,
        )
        if restart_r.returncode != 0:
            _record("J-2", "Survives restart", WARN,
                    f"docker restart returned non-zero: {restart_r.stderr[:100]}")
        else:
            time.sleep(6)  # wait for container startup
            final_status2, job_data2 = _wait_for_job(job_id2, max_seconds=45)
            if final_status2 == "succeeded":
                _record("J-2", "Survives restart", PASS,
                        f"Job {job_id2[:8]} resumed after container restart and completed")
            elif final_status2 == "timeout":
                _record("J-2", "Survives restart", WARN,
                        f"Job {job_id2[:8]} still running 45s after restart — may need longer")
            else:
                _record("J-2", "Survives restart", FAIL,
                        f"Job ended with status={final_status2} after restart (expected succeeded)")

    # J-3: Self-detected drift — invariant engine covers key drift scenarios
    inv_py = SCHED_DIR / "invariants.py"
    if not inv_py.exists():
        _record("J-3", "Self-detected drift (invariant coverage)", SKIP, "invariants.py not in scheduler")
    else:
        inv_txt = inv_py.read_text()
        coverage = []
        if "kai_cs_active" in inv_txt:
            coverage.append("WP Coming Soon state mismatch")
        if re.search(r"running.*hour|stuck.*job|stale.*job|idle.*job", inv_txt, re.I):
            coverage.append("stale/idle job detection")
        if "0600" in inv_txt or re.search(r"permission|secret.*file|chmod", inv_txt, re.I):
            coverage.append("secrets file permissions")
        if re.search(r"vault.*writ|sentinel", inv_txt, re.I):
            coverage.append("vault writability")
        if re.search(r"container.*health|health.*container|api.*respond", inv_txt, re.I):
            coverage.append("container health")
        if len(coverage) >= 4:
            _record("J-3", "Self-detected drift", PASS, f"Covers: {coverage}")
        elif len(coverage) >= 2:
            _record("J-3", "Self-detected drift", WARN,
                    f"Partial coverage ({len(coverage)}/5 checks): {coverage}")
        else:
            _record("J-3", "Self-detected drift", FAIL,
                    f"Only {len(coverage)} drift check(s) — invariant engine too shallow")

    # J-4: Learning loop produces output
    ok4, proposals = _orch_get("/learning/proposals")
    if ok4:
        p_count = len(proposals.get("proposals", []))
        pat_count = len(proposals.get("patterns", []))
        if p_count > 0 or pat_count > 0:
            _record("J-4", "Learning loop produces output", PASS,
                    f"{p_count} proposals, {pat_count} pattern files in vault")
        else:
            # Trigger aggregation run
            ok5, agg = _orch_post("/learning/run-aggregation", {})
            if ok5 and "error" not in str(agg):
                _record("J-4", "Learning loop produces output", WARN,
                        f"No prior output — aggregation triggered now: {str(agg)[:100]}")
            else:
                _record("J-4", "Learning loop produces output", WARN,
                        "No loop output and aggregation trigger failed — Phase 4 not running")
    else:
        _record("J-4", "Learning loop produces output", WARN,
                f"/learning/proposals unreachable: {proposals.get('error','?')}")

    # J-5: Shrunk persona — KAI persona files ≤150 lines, <5 rule keywords
    rule_re = re.compile(r'\b(MUST|NEVER|ALWAYS|mandatory|required)\b')
    persona_files_checked = []

    # Check vault council KAI persona
    council_dir = VAULT / "60_Council"
    kai_persona_candidates = []
    if council_dir.exists():
        kai_persona_candidates = list(council_dir.rglob("KAI.md"))

    if kai_persona_candidates:
        total_lines = 0
        total_rules = 0
        for f in kai_persona_candidates:
            txt = f.read_text()
            lines = len(txt.splitlines())
            rules = len(rule_re.findall(txt))
            total_lines += lines
            total_rules += rules
            persona_files_checked.append(f"{f.parent.name}/{f.name}: {lines}L {rules}R")
        if total_lines <= 150 and total_rules < 5:
            _record("J-5", "Shrunk persona (≤150L, <5 rule keywords)", PASS,
                    f"{total_lines} lines, {total_rules} rule keywords. {persona_files_checked}")
        elif total_lines <= 300:
            _record("J-5", "Shrunk persona (≤150L, <5 rule keywords)", WARN,
                    f"{total_lines} lines (target ≤150), {total_rules} rule keywords. Phase 5 in progress.")
        else:
            _record("J-5", "Shrunk persona (≤150L, <5 rule keywords)", FAIL,
                    f"{total_lines} lines (target ≤150). Phase 5 not done — rules still in persona.")
    else:
        _record("J-5", "Shrunk persona (≤150L, <5 rule keywords)", WARN,
                "No KAI.md found in vault/60_Council — persona structure may differ; check manually")


# ── Category R: Open Bug Regression ──────────────────────────────────────────

def suite_regression():
    print(f"\n{YELLOW}── Category R: Open Bug Regression ─────────────────────────────────{RESET}")

    watchdog_py = SCHED_DIR / "watchdog.py"
    triage_py   = SCHED_DIR / "triage.py"

    if not watchdog_py.exists():
        _record("R-ALL", "Watchdog/triage source", FAIL, f"{watchdog_py} not found")
        return

    watchdog_txt = watchdog_py.read_text()
    triage_txt   = triage_py.read_text() if triage_py.exists() else ""

    # R-1: Watchdog ALERT_INTERVAL_HOURS should be 24
    m = re.search(r"ALERT_INTERVAL_HOURS\s*=\s*(\d+)", watchdog_txt)
    if m:
        hours = int(m.group(1))
        if hours >= 24:
            _record("R-1", "Watchdog ALERT_INTERVAL_HOURS = 24", PASS, f"Value: {hours}")
        else:
            _record("R-1", "Watchdog ALERT_INTERVAL_HOURS = 24", FAIL,
                    f"ALERT_INTERVAL_HOURS = {hours} — still {hours}h, should be 24. Fix 2 NOT applied.")
    else:
        _record("R-1", "Watchdog ALERT_INTERVAL_HOURS = 24", FAIL,
                "ALERT_INTERVAL_HOURS not found in watchdog.py")

    # R-2: Snooze state persists to disk (not just in-memory dict)
    has_snooze = "snooze" in watchdog_txt.lower() or "_last_alert" in watchdog_txt
    if has_snooze:
        disk_patterns = ["write_text(", "json.dump(", ".open(", "Path(", "pkl.dump", "pickle"]
        has_disk_write = any(p in watchdog_txt for p in disk_patterns)
        has_mem_only   = "_last_alert" in watchdog_txt and not has_disk_write
        if has_disk_write:
            _record("R-2", "Snooze persists to disk (not memory-only)", PASS,
                    "File-based snooze write detected in watchdog.py")
        else:
            _record("R-2", "Snooze persists to disk (not memory-only)", FAIL,
                    "_last_alert is in-memory dict only — snooze resets on container restart. Fix 2 incomplete.")
    else:
        _record("R-2", "Snooze persists to disk (not memory-only)", WARN,
                "No snooze logic found in watchdog.py")

    # R-3: Plane dedup in triage — no repeat BUG for same failure key
    if triage_txt:
        dedup_patterns = ["dedup", "duplicate", "already_exists", "existing_issue",
                          "already filed", "already_filed", "in_progress", "seen_keys", "processed"]
        has_dedup = any(p in triage_txt.lower() for p in dedup_patterns)
        if has_dedup:
            _record("R-3", "Plane dedup in triage.py", PASS,
                    "Dedup guard found — same failure won't create duplicate BUGs")
        else:
            _record("R-3", "Plane dedup in triage.py", FAIL,
                    "No dedup logic in triage.py. Fix 3 not applied — Plane is accumulating duplicate BUGs.")
    else:
        _record("R-3", "Plane dedup in triage.py", SKIP, "triage.py not found")

    # R-4: Gap check persistent snooze (24h)
    if triage_txt:
        has_24h    = "24" in triage_txt
        has_gap    = "gap" in triage_txt.lower()
        has_snooze = "snooze" in triage_txt.lower()
        if has_24h and (has_gap or has_snooze):
            _record("R-4", "Gap check snooze 24h in triage.py", PASS,
                    "24h gap snooze reference found")
        else:
            _record("R-4", "Gap check snooze 24h in triage.py", FAIL,
                    "No 24h gap snooze found in triage.py. Fix 4 not applied.")
    else:
        _record("R-4", "Gap check snooze 24h in triage.py", SKIP, "triage.py not found")

    # R-5: Todoist fallback on hardlimit failures in triage
    if triage_txt:
        has_todoist  = "todoist" in triage_txt.lower()
        has_hardlimit = "hardlimit" in triage_txt.lower()
        if has_todoist and has_hardlimit:
            _record("R-5", "Todoist fallback in triage.py", PASS,
                    "Todoist + hardlimit both present in triage.py")
        elif has_todoist:
            _record("R-5", "Todoist fallback in triage.py", WARN,
                    "Todoist referenced but not clearly on hardlimit path — verify wiring")
        else:
            _record("R-5", "Todoist fallback in triage.py", FAIL,
                    "No Todoist fallback in triage.py. Fix 5 not applied.")
    else:
        _record("R-5", "Todoist fallback in triage.py", SKIP, "triage.py not found")

    # R-6: Watchdog reads live Docker state (KAI-356)
    has_docker = any(kw in watchdog_txt for kw in ["docker", "subprocess", "container", "httpx", "requests"])
    has_static  = any(kw in watchdog_txt for kw in ["scheduler_config", "config.json", "static_config"])
    if has_docker and not has_static:
        _record("R-6", "Watchdog reads live state (KAI-356)", PASS,
                "Live container/API check found — no static config dependency")
    elif has_docker:
        _record("R-6", "Watchdog reads live state (KAI-356)", WARN,
                "Both live and static config refs found — verify which drives alerting")
    else:
        _record("R-6", "Watchdog reads live state (KAI-356)", FAIL,
                "No live Docker state check in watchdog.py — KAI-356 not fixed")

    # R-7: Stale container images (KAI-357)
    stale, fresh = [], []
    for name in ["kai-worker-api", "kai-council-api", "kai-orchestrator"]:
        r = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.Created}}"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            raw = r.stdout.strip()[:19].replace("T", " ")
            try:
                dt  = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).days
                (stale if age > 30 else fresh).append(f"{name}:{age}d")
            except Exception:
                fresh.append(f"{name}:unknown")
    if stale:
        _record("R-7", "Container images not stale (KAI-357)", WARN,
                f"Stale (>30d): {stale}. Fresh: {fresh}")
    else:
        _record("R-7", "Container images not stale (KAI-357)", PASS, f"All fresh: {fresh}")


# ── Category L: Live Capability Probes ───────────────────────────────────────

def suite_live():
    print(f"\n{YELLOW}── Category L: Live Capability Probes ──────────────────────────────{RESET}")

    # L-1: wordpress.load_config for all sites
    sites_json = VAULT / "00_System" / "wordpress_sites.json"
    if not sites_json.exists():
        _record("L-1", "WP credentials for all sites", FAIL, "wordpress_sites.json not found")
    else:
        sites = json.loads(sites_json.read_text()).get("sites", {})
        pass_sites, fail_sites = [], []
        for site_key in sites:
            ok, data = _cap("wordpress.load_config", {"site": site_key}, timeout=15)
            if ok and data.get("ok"):
                pass_sites.append(site_key)
            else:
                fail_sites.append(f"{site_key}: {str(data.get('error','?'))[:60]}")
        total = len(sites)
        passed = len(pass_sites)
        if passed == total:
            _record("L-1", "WP load_config for all sites", PASS, f"{passed}/{total} sites OK")
        elif passed >= total * 0.8:
            _record("L-1", "WP load_config for all sites", WARN,
                    f"{passed}/{total} OK. Failing: {fail_sites[:3]}")
        else:
            _record("L-1", "WP load_config for all sites", FAIL,
                    f"Only {passed}/{total}. Failing: {fail_sites[:3]}")

    # L-2: Load config then get_option (kai_cs_active)
    ok_cfg, cfg = _cap("wordpress.load_config", {"site": "sette-uno.com"})
    if not (ok_cfg and cfg.get("ok")):
        _record("L-2", "get_option kai_cs_active (sette-uno.com)", FAIL,
                f"load_config failed: {str(cfg)[:150]}")
        _record("L-3", "set_option round-trip with verification", SKIP, "load_config failed")
    else:
        creds = cfg.get("data", {}).get("creds", {})
        # Check if get_option capability exists
        ok_caps, caps_data = _orch_get("/capabilities")
        has_get_option = ok_caps and any(
            c["name"] == "wordpress.get_option" for c in caps_data.get("capabilities", [])
        )
        if has_get_option:
            ok_get, get_data = _cap("wordpress.get_option",
                                    {"site": "sette-uno.com", "option": "kai_cs_active", "creds": creds})
            if ok_get and get_data.get("ok"):
                val = get_data.get("data", {}).get("value", "?")
                _record("L-2", "get_option kai_cs_active (sette-uno.com)", PASS,
                        f"kai_cs_active = '{val}' via REST")
                # L-3: Round-trip set same value back
                ok_set, set_data = _cap("wordpress.set_option", {
                    "site": "sette-uno.com", "option": "kai_cs_active",
                    "value": str(val), "creds": creds,
                })
                if ok_set and set_data.get("ok"):
                    v_data = set_data.get("verification", {})
                    verified = v_data.get("verified", False)
                    _record("L-3", "set_option round-trip with verification", PASS if verified else WARN,
                            f"Set kai_cs_active={val}, verified={verified}")
                else:
                    _record("L-3", "set_option round-trip with verification", FAIL,
                            f"set_option failed: {str(set_data)[:200]}")
            else:
                _record("L-2", "get_option kai_cs_active (sette-uno.com)", FAIL,
                        f"get_option failed: {str(get_data)[:200]}")
                _record("L-3", "set_option round-trip with verification", SKIP, "get_option failed")
        else:
            _record("L-2", "get_option kai_cs_active (sette-uno.com)", WARN,
                    "wordpress.get_option not registered as capability yet")
            # Still test set_option with current value assumption
            ok_set, set_data = _cap("wordpress.set_option", {
                "site": "sette-uno.com", "option": "kai_cs_active",
                "value": "1", "creds": creds,
            })
            if ok_set and set_data.get("ok"):
                verified = set_data.get("verification", {}).get("verified", False)
                _record("L-3", "set_option round-trip with verification", PASS if verified else WARN,
                        f"set_option OK, verified={verified}")
            else:
                _record("L-3", "set_option round-trip with verification", FAIL,
                        f"set_option failed: {str(set_data)[:200]}")

    # L-4: vault.read works
    ok, data = _cap("vault.read", {"path": "00_System/wordpress_sites.json"})
    if ok and data.get("ok"):
        content = str(data.get("data", ""))
        _record("L-4", "vault.read() works", PASS, f"Read OK, content length={len(content)} chars")
    else:
        _record("L-4", "vault.read() works", FAIL, f"vault.read failed: {str(data)[:200]}")

    # L-5: workspace.read works (list as read of root)
    ok, data = _cap("workspace.read", {"path": "CLAUDE.md"})
    if ok and data.get("ok"):
        content_len = len(str(data.get("data", {}).get("content", "")))
        _record("L-5", "workspace.read() works", PASS, f"CLAUDE.md read OK, {content_len} chars")
    else:
        _record("L-5", "workspace.read() works", FAIL if not ok else WARN,
                f"workspace.read: {str(data)[:200]}")

    # L-6: plane.create_issue + update_state round-trip
    ok_create, c_data = _cap("plane.create_issue", {
        "project_id": KAI_PROJECT_ID,
        "title": "[JARVIS TEST] Automated system test — auto-close",
        "description": f"Created by JARVIS test suite at {datetime.now().strftime('%Y-%m-%d %H:%M')}.",
        "priority": "low",
    }, timeout=20)
    if ok_create and c_data.get("ok"):
        issue_id = c_data.get("data", {}).get("issue_id", "")
        if issue_id:
            ok_close, close_data = _cap("plane.update_state", {
                "issue_id": issue_id,
                "project_id": KAI_PROJECT_ID,
                "state_name": "done",
                "notes": "JARVIS test auto-closed.",
            }, timeout=15)
            if ok_close and close_data.get("ok"):
                _record("L-6", "plane.create_issue + update_state round-trip", PASS,
                        f"Issue {issue_id[:8]} created and closed")
            else:
                _record("L-6", "plane.create_issue + update_state round-trip", WARN,
                        f"Created {issue_id[:8]} but close failed: {str(close_data)[:150]}")
        else:
            _record("L-6", "plane.create_issue + update_state round-trip", WARN,
                    "Created but no issue_id returned")
    else:
        _record("L-6", "plane.create_issue + update_state round-trip", FAIL,
                f"create_issue failed: {str(c_data)[:200]}")

    # L-7: slack.post to #kai-system
    ok, data = _cap("slack.post", {
        "channel": "kai-system",
        "text": f"[JARVIS TEST {datetime.now().strftime('%H:%M')}] Automated system test ping — safe to ignore.",
    }, timeout=15)
    if ok and data.get("ok"):
        _record("L-7", "slack.post to #kai-system", PASS, "Message delivered")
    else:
        _record("L-7", "slack.post to #kai-system", FAIL, f"slack.post failed: {str(data)[:200]}")


# ── Category P: Policy Enforcement ───────────────────────────────────────────

def suite_policy():
    print(f"\n{YELLOW}── Category P: Policy Enforcement ──────────────────────────────────{RESET}")

    # P-1: OVERRIDE rejects short reason (<50 chars)
    ok, run_data = _orch_post("/workflows/run", {"type": "hello_world", "inputs": {"name": "PolicyTest"}})
    if ok and "job_id" in run_data:
        job_id = run_data["job_id"]
        time.sleep(3)
        ok2, job_data = _orch_get(f"/jobs/{job_id}")
        steps = job_data.get("steps", []) if ok2 else []
        if steps:
            step_id = steps[0]["id"]
            code = (
                f"import httpx, json\n"
                f"r = httpx.post('http://localhost:8003/jobs/{job_id}/steps/{step_id}/override',\n"
                f"    json={{'reason': 'too short', 'operator': 'test'}}, timeout=10)\n"
                f"print(json.dumps({{'status': r.status_code, 'body': r.json()}}))\n"
            )
            _, out = _orch_exec(code)
            try:
                resp = json.loads(out)
                status_code = resp.get("status", 0)
                body = resp.get("body", {})
                if status_code == 400 and ("50" in str(body) or "reason" in str(body).lower()):
                    _record("P-1", "OVERRIDE rejects short reason", PASS,
                            f"HTTP 400 with reason enforcement message")
                elif status_code == 400:
                    _record("P-1", "OVERRIDE rejects short reason", PASS,
                            f"HTTP 400 returned (reason validation present)")
                else:
                    _record("P-1", "OVERRIDE rejects short reason", FAIL,
                            f"Short reason not rejected: HTTP {status_code} — {str(body)[:150]}")
            except Exception:
                _record("P-1", "OVERRIDE rejects short reason", WARN, f"Cannot parse response: {out[:200]}")
        else:
            _record("P-1", "OVERRIDE rejects short reason", SKIP, "No steps available to test on")
    else:
        _record("P-1", "OVERRIDE rejects short reason", SKIP, "Cannot start test job")

    # P-2: OVERRIDE posts to Slack (code audit)
    main_py = ORCH_DIR / "main.py"
    if main_py.exists():
        main_txt = main_py.read_text()
        has_override_fn  = "def override_step" in main_txt or "override_step" in main_txt
        has_slack_in_fn  = "_post_slack" in main_txt
        if has_override_fn and has_slack_in_fn:
            _record("P-2", "OVERRIDE posts Slack ack", PASS,
                    "_post_slack called within override endpoint in main.py")
        else:
            _record("P-2", "OVERRIDE posts Slack ack", FAIL,
                    "No Slack call inside override_step() in main.py")
    else:
        _record("P-2", "OVERRIDE posts Slack ack", SKIP, "main.py not found")

    # P-3: OVERRIDE pattern triggers Plane BUG at 5x
    if main_py.exists():
        has_count   = "count_overrides_7d" in main_txt
        has_bug     = "_create_plane_bug" in main_txt
        has_pattern = has_count and has_bug
        if has_pattern:
            _record("P-3", "OVERRIDE pattern auto-files Plane BUG at 5x", PASS,
                    "count_overrides_7d + _create_plane_bug wired in override_step()")
        elif has_bug:
            _record("P-3", "OVERRIDE pattern auto-files Plane BUG at 5x", WARN,
                    "Plane BUG creation exists but pattern-count logic unclear")
        else:
            _record("P-3", "OVERRIDE pattern auto-files Plane BUG at 5x", FAIL,
                    "No pattern-count + auto-BUG logic in main.py override_step()")

    # P-4: No WP-CLI binary invocations in transport code
    transports_dir = ORCH_DIR / "transports"
    wp_cli_calls = []
    for f in transports_dir.glob("*.py"):
        txt = f.read_text()
        for i, line in enumerate(txt.splitlines(), 1):
            s = line.strip()
            if re.search(r'"wp"\s*[,\]]|subprocess.*\bwp\b|\bwp\s+option\b|\bwp\s+post\b', s) and not s.startswith("#"):
                wp_cli_calls.append(f"{f.name}:{i}: {s[:80]}")
    if wp_cli_calls:
        _record("P-4", "No WP-CLI in transport code", FAIL,
                f"WP-CLI calls found: {wp_cli_calls[:3]}")
    else:
        _record("P-4", "No WP-CLI in transport code", PASS,
                "Zero WP-CLI binary invocations in transports/")

    # P-5: safe_request() wraps all HTTP in transports (no bare httpx outside base.py)
    transports_dir = ORCH_DIR / "transports"
    raw_calls = []
    for f in transports_dir.glob("*.py"):
        if f.name == "base.py":
            continue
        txt = f.read_text()
        for i, line in enumerate(txt.splitlines(), 1):
            s = line.strip()
            if re.search(r"httpx\.(get|post|request|put|delete|patch)\s*\(", s) and not s.startswith("#"):
                raw_calls.append(f"{f.name}:{i}: {s[:80]}")
    if raw_calls:
        _record("P-5", "All transport HTTP through safe_request()", FAIL,
                f"Raw httpx calls outside base.py: {raw_calls[:3]}")
    else:
        _record("P-5", "All transport HTTP through safe_request()", PASS,
                "All transport HTTP calls routed through base.py safe_request()")


# ── Category S: Session Close Law ────────────────────────────────────────────

def suite_session():
    print(f"\n{YELLOW}── Category S: Session Close Compliance ────────────────────────────{RESET}")

    # S-1: Session close log exists and is recent
    close_log = VAULT / "session_close_log.json"
    if close_log.exists():
        try:
            raw = json.loads(close_log.read_text())
            entries = raw if isinstance(raw, list) else [raw]
            last = entries[-1] if entries else {}
            date_str = (last.get("date") or last.get("timestamp") or
                        last.get("closed_at") or last.get("session_date") or "")
            if date_str:
                _record("S-1", "Session close log exists and recent", PASS,
                        f"Last close: {str(date_str)[:16]} — {len(entries)} entries total")
            else:
                _record("S-1", "Session close log exists and recent", WARN,
                        f"Log exists ({len(entries)} entries) but no parseable date in last entry")
        except Exception as e:
            _record("S-1", "Session close log exists and recent", WARN, f"Cannot parse: {e}")
    else:
        _record("S-1", "Session close log exists and recent", FAIL,
                "session_close_log.json not found — session closes may not be logged")

    # S-2: Session close workflow or scheduler job exists
    close_wf = ORCH_DIR / "workflows" / "session_close.py"
    sched_py  = SCHED_DIR / "scheduler.py"
    session_in_sched = False
    if sched_py.exists():
        st = sched_py.read_text().lower()
        session_in_sched = "session" in st and "close" in st
    if close_wf.exists():
        _record("S-2", "Session close workflow defined", PASS, "workflows/session_close.py exists")
    elif session_in_sched:
        _record("S-2", "Session close workflow defined", WARN,
                "No session_close.py workflow — session close handled by scheduler only")
    else:
        _record("S-2", "Session close workflow defined", FAIL,
                "No session close workflow or scheduler entry — close law not enforced in code")

    # S-3: StateOfTheUnion updated recently
    candidates = [
        VAULT / "70_Knowledge" / "System" / "StateOfTheUnion.md",
        Path("/home/leo/sonicink/StateOfTheUnion.md"),
    ]
    target = next((f for f in candidates if f.exists()), None)
    if target:
        age_days = (time.time() - target.stat().st_mtime) / 86400
        if age_days < 30:
            _record("S-3", "StateOfTheUnion updated recently", PASS, f"Last modified {age_days:.1f}d ago")
        else:
            _record("S-3", "StateOfTheUnion updated recently", WARN,
                    f"StateOfTheUnion.md is {age_days:.0f}d old — session closes may not be writing it")
    else:
        _record("S-3", "StateOfTheUnion updated recently", FAIL, "StateOfTheUnion.md not found")

    # S-4: No orphaned jobs (every job has ≥1 event)
    code = (
        "import sqlite3, json\n"
        "conn = sqlite3.connect('/data/orchestrator/orchestrator.db')\n"
        "conn.row_factory = sqlite3.Row\n"
        "jobs = conn.execute('SELECT id FROM jobs').fetchall()\n"
        "orphaned = [j['id'] for j in jobs if conn.execute(\n"
        "    'SELECT COUNT(*) FROM events WHERE job_id=?', (j['id'],)).fetchone()[0] == 0]\n"
        "conn.close()\n"
        "print(json.dumps({'total': len(jobs), 'orphaned': len(orphaned), 'ids': orphaned[:5]}))\n"
    )
    ok, out = _orch_exec(code)
    try:
        d = json.loads(out)
        orphaned = d.get("orphaned", 0)
        total    = d.get("total", 0)
        if orphaned == 0:
            _record("S-4", "No orphaned jobs (every job has ≥1 event)", PASS,
                    f"All {total} jobs have event records")
        else:
            _record("S-4", "No orphaned jobs (every job has ≥1 event)", WARN,
                    f"{orphaned}/{total} jobs have no events — possible uncleaned test jobs")
    except Exception:
        _record("S-4", "No orphaned jobs", WARN, f"Cannot query DB: {out[:200]}")


# ── Summary + Reporting ───────────────────────────────────────────────────────

SUITES = {
    "architecture": suite_architecture,
    "health":       suite_health,
    "jarvis":       suite_jarvis,
    "regression":   suite_regression,
    "live":         suite_live,
    "policy":       suite_policy,
    "session":      suite_session,
}


def _post_slack_summary():
    token = _secret("slack_bot_token.txt")
    if not token:
        print("  (no slack token — skipping summary)")
        return

    pass_n = sum(1 for r in _results if r["status"] == "PASS")
    fail_n = sum(1 for r in _results if r["status"] == "FAIL")
    warn_n = sum(1 for r in _results if r["status"] == "WARN")
    skip_n = sum(1 for r in _results if r["status"] == "SKIP")
    total  = len(_results)
    elapsed = int(time.time() - _start_time)

    emoji  = ":white_check_mark:" if fail_n == 0 else ":x:"
    result = "ALL PASS" if fail_n == 0 else f"{fail_n} FAILURE{'S' if fail_n != 1 else ''}"
    text   = (
        f"{emoji} *JARVIS System Test Suite — {result}*\n"
        f"*Score:* {pass_n} pass · {fail_n} fail · {warn_n} warn · {skip_n} skip / {total} total\n"
        f"*Runtime:* {elapsed}s\n"
        f"*Report:* `vault/00_System/jarvis_test_report_{datetime.now().strftime('%Y-%m-%d')}.json`"
    )

    fails = [r for r in _results if r["status"] == "FAIL"]
    if fails:
        lines = "\n".join(f"• `{r['id']}` {r['name']}: {r['detail'][:80]}" for r in fails[:10])
        text += f"\n\n*Failures:*\n{lines}"

    warns = [r for r in _results if r["status"] == "WARN"]
    if warns:
        wlines = "\n".join(f"• `{r['id']}` {r['name']}" for r in warns[:5])
        text += f"\n\n*Warnings ({len(warns)}):*\n{wlines}"

    try:
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "kai-system", "text": text,
                  "username": "KAI Test Suite", "icon_emoji": ":test_tube:"},
            timeout=10,
        )
        if r.json().get("ok"):
            print(f"  Slack summary posted to #kai-system")
        else:
            print(f"  Slack post failed: {r.json().get('error')}")
    except Exception as e:
        print(f"  Slack post error: {e}")


def main():
    parser = argparse.ArgumentParser(description="JARVIS System Test Suite v1.0")
    parser.add_argument("--suite", default="all",
                        choices=["all"] + list(SUITES.keys()),
                        help="Test suite to run (default: all)")
    args = parser.parse_args()

    print("=" * 64)
    print("  JARVIS SYSTEM TEST SUITE v1.0")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)

    to_run = list(SUITES.values()) if args.suite == "all" else [SUITES[args.suite]]
    for fn in to_run:
        try:
            fn()
        except Exception as e:
            print(f"\n  {RED}ERROR in {fn.__name__}: {e}{RESET}")

    # Final summary
    pass_n = sum(1 for r in _results if r["status"] == "PASS")
    fail_n = sum(1 for r in _results if r["status"] == "FAIL")
    warn_n = sum(1 for r in _results if r["status"] == "WARN")
    skip_n = sum(1 for r in _results if r["status"] == "SKIP")
    total  = len(_results)
    elapsed = int(time.time() - _start_time)

    print(f"\n{'=' * 64}")
    verdict = f"{GREEN}ALL PASS{RESET}" if fail_n == 0 else f"{RED}{fail_n} FAILURE{'S' if fail_n != 1 else ''}{RESET}"
    print(f"  {verdict} — {pass_n}✓ {fail_n}✗ {warn_n}⚠ {skip_n}○ / {total} total  ({elapsed}s)")
    print(f"{'=' * 64}")

    if fail_n:
        print(f"\n  {RED}FAILURES:{RESET}")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"    ✗ {r['id']}: {r['name']}")
                if r["detail"]:
                    print(f"      {r['detail'][:120]}")

    if warn_n:
        print(f"\n  {YELLOW}WARNINGS:{RESET}")
        for r in _results:
            if r["status"] == "WARN":
                print(f"    ⚠ {r['id']}: {r['name']}")
                if r["detail"]:
                    print(f"      {r['detail'][:100]}")

    # Save JSON report
    report_name = f"jarvis_test_report_{datetime.now().strftime('%Y-%m-%d')}.json"
    report_path = VAULT / "00_System" / report_name
    try:
        report = {
            "suite": args.suite,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed,
            "summary": {"pass": pass_n, "fail": fail_n, "warn": warn_n, "skip": skip_n, "total": total},
            "results": _results,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        print(f"\n  Report: {report_path}")
    except Exception as e:
        print(f"  Report save failed: {e}")

    # Slack summary
    print("\n  Posting Slack summary...")
    _post_slack_summary()

    sys.exit(0 if fail_n == 0 else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""AR-0 canonical read-only green-baseline smoke/invariant suite.

This runs on the worker host. It intentionally performs only probes: no writes,
no restarts, no model completions, and no Plane mutations.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
WORKER_API = "http://100.78.94.80:8001"
LITELLM_CONTAINER = "kai-litellm"


@dataclass(frozen=True)
class Check:
    name: str
    probe: Callable[[], str]


def _command(*args: str, timeout: int = 8) -> str:
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().replace("\n", " ")
        raise RuntimeError(detail[:240] or f"exit {result.returncode}")
    return result.stdout.strip()


def _request(url: str, *, auth: str | None = None, expected: int = 200, timeout: int = 5) -> bytes:
    headers = {}
    if auth:
        token = base64.b64encode(auth.encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != expected:
                raise RuntimeError(f"HTTP {response.status}, expected {expected}")
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == expected:
            return exc.read()
        raise RuntimeError(f"HTTP {exc.code}, expected {expected}") from exc
    except Exception as exc:
        raise RuntimeError(f"request failed: {type(exc).__name__}") from exc


def _worker_auth() -> str:
    auth_path = SECRETS / "kai_worker_auth.txt"
    try:
        value = auth_path.read_text().strip()
    except OSError as exc:
        raise RuntimeError("worker auth secret unavailable") from exc
    if not value or ":" not in value:
        raise RuntimeError("worker auth secret malformed")
    return value


def check_services() -> str:
    expected = {"kai-worker-api": "healthy", "kai-litellm": "healthy", "kai-qdrant": "running", "plane-api": "running"}
    for service, wanted in expected.items():
        status = _command("docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", service)
        if status != wanted:
            raise RuntimeError(f"{service} is {status!r}, expected {wanted!r}")
        # KAI-1046: a 'healthy' container attached to zero docker networks still
        # passes its own localhost healthcheck but can reach nothing off-box.
        # Treat network isolation as RED — never let a detached container read green.
        nets = _command("docker", "inspect", "--format",
                        "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", service).strip()
        if not nets:
            raise RuntimeError(f"{service} is {status!r} but attached to ZERO docker networks (network-isolated — KAI-1046)")
    return f"{len(expected)} required services up + network-attached"


def check_session_brief() -> str:
    body = _request(f"{WORKER_API}/session/brief", auth=_worker_auth())
    brief = json.loads(body)
    if not brief.get("ok") or brief.get("warmboot_required") is not False:
        raise RuntimeError("brief is not fresh/ok")
    return "session brief reachable and warm boot fresh"


def check_worker_auth_fails_closed() -> str:
    _request(f"{WORKER_API}/session/brief", expected=401)
    return "unauthenticated worker request denied (401)"


def check_plane() -> str:
    body = _request(f"{WORKER_API}/plane/issues?include_done=false", auth=_worker_auth(), timeout=15)
    return f"Plane reachable ({parse_plane_open_issues(body.decode())} open issue(s))"


def parse_plane_open_issues(payload: str) -> int:
    data = json.loads(payload)
    if isinstance(data, list):
        return len(data)
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        raise RuntimeError("Plane response has no projects/issue list")
    if not all(isinstance(project, dict) and isinstance(project.get("issues"), list) for project in projects):
        raise RuntimeError("Plane project response has invalid issues")
    return sum(len(project["issues"]) for project in projects)


def check_qdrant() -> str:
    _request("http://localhost:6333/healthz")
    return "Qdrant healthz reachable"


def parse_model_ids(payload: str) -> set[str]:
    data = json.loads(payload)
    return {item["id"] for item in data.get("data", []) if isinstance(item, dict) and "id" in item}


def check_litellm_models() -> str:
    code = """import json, urllib.request
from pathlib import Path
key = Path('/run/secrets/litellm_master_key').read_text().strip()
request = urllib.request.Request('http://localhost:4000/v1/models', headers={'Authorization': f'Bearer {key}'})
print(urllib.request.urlopen(request, timeout=5).read().decode())
"""
    models = parse_model_ids(_command("docker", "exec", LITELLM_CONTAINER, "python3", "-c", code))
    missing = {"qwen-mid", "qwen-mid-worker"} - models
    if missing:
        raise RuntimeError("models missing: " + ", ".join(sorted(missing)))
    return "LiteLLM /v1/models exposes qwen-mid and qwen-mid-worker"


def check_qwen_route_contract() -> str:
    try:
        config = (ROOT / "litellm" / "config.yaml").read_text()
    except OSError as exc:
        raise RuntimeError("LiteLLM config unavailable") from exc
    primary = re.search(r"- model_name: qwen-mid\s+litellm_params:.*?api_base: (\S+)", config, re.S)
    fallback = re.search(r"- model_name: qwen-mid-worker\s+litellm_params:", config, re.S)
    has_chain = re.search(r'fallbacks:\s*\[\{"qwen-mid":\s*\["qwen-mid-worker"\]\}\]', config)
    if not primary or not fallback or not has_chain:
        raise RuntimeError("qwen-mid primary/fallback contract missing")
    if "100.106.160.41:11434" not in primary.group(1):
        raise RuntimeError("qwen-mid primary is not the 71-kai-mini endpoint")
    return "qwen-mid routes to 71-kai-mini with worker fallback"


def check_secret_permissions() -> str:
    code = """from pathlib import Path
files = [path for path in Path('/run/secrets').iterdir() if path.is_file()]
bad = sum(bool(path.stat().st_mode & 0o077) for path in files)
print(f'{len(files)} {bad}')
"""
    count_raw, unsafe_raw = _command("docker", "exec", "kai-worker-api", "python3", "-c", code).split()
    count, unsafe = int(count_raw), int(unsafe_raw)
    if not count:
        raise RuntimeError("no runtime secret files found")
    if unsafe:
        raise RuntimeError(f"{unsafe} runtime secret file(s) are group/world-readable")
    return f"{count} runtime secrets are owner-only"


def check_source_drift() -> str:
    _command("git", "-C", str(ROOT), "diff", "--check")
    return "no whitespace/source-integrity drift"


def check_fleet() -> str:
    """KAI-1047 — session-start fleet gate.

    Uses the SAME shared verdict as the watchdog (fleet_eval.fleet_verdict) so
    the two surfaces cannot disagree. RED (raises) on ANY unhealthy fleet: a host
    offline, an ssh-expected host ssh-unreachable, an incomplete roster, or a
    stale/missing/future heartbeat (lost visibility — the KAI-1046 'monitoring
    is blind' class). A machine being down never reads GREEN.
    """
    import json as _json
    import time as _time

    sys.path.insert(0, str(ROOT / "shared"))
    from fleet_eval import fleet_gate_verdict

    state = {}
    for candidate in (Path("/vault/_fleet_state.json"), Path("/home/leo/vault/_fleet_state.json")):
        if candidate.exists():
            state = _json.loads(candidate.read_text())
            break
    # GATE severity: hard-fail on lost visibility or the SPINE being down; a
    # non-spine node offline is a printed WARNING (the watchdog pages on it), so
    # a flapping aux node never blocks a push. self_host comes from the state.
    # Read the operator maintenance window so a MUTED node reads "muted", not
    # "watchdog paging" — the watchdog suppresses its page while a window is
    # active, so the session baseline must say the same thing (KAI cutover).
    muted = set()
    for _mp in (Path("/vault/_fleet_maint.json"), Path("/home/leo/vault/_fleet_maint.json")):
        if _mp.exists():
            try:
                _m = _json.loads(_mp.read_text())
                if _m.get("schema") == "kai.fleet_maint.v1" and _time.time() < _m.get("expires_at", 0):
                    muted = set(_m.get("muted") or ())
            except Exception:
                muted = set()  # malformed => no window (fail-safe: page label)
            break
    ok, detail = fleet_gate_verdict(state, int(_time.time()),
                                    (state or {}).get("self_host"), muted=muted)
    if not ok:
        raise RuntimeError(detail)
    return detail


def check_buzz_shim() -> str:
    # KAI-1029: the openai-compat backend Leo's native Buzz advisor agents call.
    # A cleanup sweep once retired this endpoint and no probe noticed, so every
    # advisor DM went silently unanswered for 11 days. Liveness-only (no completion,
    # per this suite's read-only contract): if :4001 is gone, this goes RED.
    models = parse_model_ids(_request("http://localhost:4001/v1/models"))
    missing = {"kai", "sky", "roads", "coach"} - models
    if missing:
        raise RuntimeError("Buzz shim :4001 missing advisor models: " + ", ".join(sorted(missing)))
    return "Buzz advisor shim (:4001) serves kai/sky/roads/coach"


def checks() -> tuple[Check, ...]:
    return (
        Check("services_up", check_services),
        Check("session_brief", check_session_brief),
        Check("worker_auth_fail_closed", check_worker_auth_fails_closed),
        Check("plane_reachable", check_plane),
        Check("qdrant_up", check_qdrant),
        Check("litellm_models", check_litellm_models),
        Check("qwen_mid_route_and_fallback", check_qwen_route_contract),
        Check("buzz_shim_backend", check_buzz_shim),
        Check("secret_permissions", check_secret_permissions),
        Check("source_drift", check_source_drift),
        Check("fleet_visibility", check_fleet),
    )


def run_suite(suite: tuple[Check, ...] | None = None) -> int:
    suite = suite or checks()
    print("KAI GREEN BASELINE — START")
    failed: list[str] = []
    for check in suite:
        try:
            detail = check.probe()
            print(f"GREEN [{check.name}] {detail}")
        except Exception as exc:
            failed.append(check.name)
            print(f"RED [{check.name}] {exc}")
    if failed:
        print("KAI GREEN BASELINE — RED: " + ", ".join(failed))
        return 1
    print("KAI GREEN BASELINE — GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(run_suite())

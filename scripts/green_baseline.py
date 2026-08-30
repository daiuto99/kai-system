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
    # W-1 (external-witness invariant, design build-order #4): a JOURNEY grants
    # GREEN — its probe reads an unforgeable external receipt (a Telegram
    # message_id, a relay reply id, an origin-REST object). Everything else is a
    # DIAGNOSTIC: a system self-probe (a port answered, a stamp is fresh, a config
    # regex matched) that can explain WHY a journey failed but can NEVER by itself
    # grant green. This is the whole fix: green now means "a party outside the code
    # under test confirmed it," not "27 signals the system produced look fine."
    journey: bool = False


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
    if "100.85.243.2:11434" not in primary.group(1):
        raise RuntimeError("qwen-mid primary is not the kai-mini endpoint")
    return "qwen-mid routes to kai-mini with worker fallback"


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
    # per this suite's read-only contract): if :4001 is gone, this goes RED. This is a
    # DIAGNOSTIC (the endpoint is up) — NOT a claim the advisor answers Leo; that
    # journey is asserted only by the external advisor witness (W-1). It stays because
    # a dead shim endpoint is a real, RED-worthy infra failure the witness can't blame.
    models = parse_model_ids(_request("http://localhost:4001/v1/models"))
    missing = {"kai", "sky", "roads", "coach"} - models
    if missing:
        raise RuntimeError("Buzz shim :4001 missing advisor models: " + ", ".join(sorted(missing)))
    # W-1 #5: the advisor_dm_probe round-trip fold-in was REMOVED — it read the
    # self-asserting probe's "healthy round-trip" heartbeat (which passed on the ack,
    # not the answer). Health of the advisor JOURNEY is the external witness's alone.
    return "Buzz advisor shim (:4001) serves kai/sky/roads/coach"


def check_codex_verifier_auth() -> str:
    """KAI-1159 — Codex is the cross-provider verifier (Claude builds / Codex
    verifies). Its "Sign in with ChatGPT" OAuth token is single-use-refresh and
    expired silently on 2026-05-16, so the verifier sat dead for ~3 months and
    nothing noticed until a session reached for it mid-build (KAI-1154 close).

    Reads ~/.codex/auth.json — STRUCTURE ONLY, never the token material (the JWT
    exp claim is a timestamp, not a secret) — and surfaces an expired / near-expiry
    OAuth token. This is a WARN, NOT a RED: Codex is a verifier, not a runtime
    dependency, so a dead token must never turn the baseline red or block a push.
    It only has to be impossible to miss at session start. Re-auth is human-only
    (browser OAuth), so the WARN points at `codex login` on the worker + KAI-1159.
    OAuth access tokens here live 10 days, so a past exp genuinely means the CLI
    failed to refresh (our exact failure) — not mere between-refresh staleness.
    """
    import time as _time

    auth = None
    for candidate in (Path.home() / ".codex" / "auth.json",
                      Path("/home/leo/.codex/auth.json")):
        if candidate.exists():
            try:
                auth = json.loads(candidate.read_text())
            except Exception:
                return "WARN codex auth.json unreadable/malformed — verifier state unknown [KAI-1159]"
            break
    if auth is None:
        return "WARN codex auth.json absent — verifier unconfigured; `codex login` on worker [KAI-1159]"

    if (auth.get("OPENAI_API_KEY") or "").strip():
        return "codex verifier on API-key auth (non-expiring)"

    tokens = auth.get("tokens") or {}
    tok = tokens.get("access_token") or auth.get("access_token") or ""
    if tok.count(".") < 2:
        return "WARN codex OAuth token unparseable — run `codex login` on worker [KAI-1159]"
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return "WARN codex OAuth token unparseable — run `codex login` on worker [KAI-1159]"
    if not isinstance(exp, (int, float)):
        return "WARN codex OAuth token has no exp claim — run `codex login` on worker [KAI-1159]"

    remaining_h = (exp - _time.time()) / 3600.0
    if remaining_h <= 0:
        return (f"WARN codex OAuth EXPIRED {abs(remaining_h) / 24:.0f}d ago — cross-provider "
                f"verifier DOWN; `codex login` on worker [KAI-1159]")
    if remaining_h <= 48:
        return (f"WARN codex OAuth expires in {remaining_h:.0f}h — re-auth soon via "
                f"`codex login` on worker [KAI-1159]")
    return f"codex verifier OAuth valid ({remaining_h / 24:.0f}d remaining)"


# ── cron_log_error_scan (S1-B2) ──────────────────────────────────────────────
# The cron fleet (fleet_heartbeat, advisor/relay/alert probes, meta_monitor) logs
# to files no probe read — three real faults (a ~2h advisor DM outage, a codex
# relay restart loop, 74× notify_dedup PermissionError) sat invisibly in those
# logs because every other baseline check only samples the instant and they had
# self-healed between samples. This row scans the logs for RECENT anchored faults.
# W-1 #5 removed advisor_dm_probe.log + meta_monitor.log — those crons (the self-
# asserting advisor probe + the meta-monitor) were torn out with the declaration
# class, so their logs no longer advance and scanning them would flag false faults.
_CRON_LOG_FILES = (
    "relay_roundtrip_probe.log", "fleet_heartbeat.log",
    "alert_delivery_heartbeat.log",
)
# Anchored fault signatures ONLY — never bare "error"/"failed", which ride normal
# status lines ("error: none", "failed=0") and would flood false positives (the
# exact care the ticket flagged these deferred rows need).
_CRON_ERR_SIGNATURES = (
    "Traceback (most recent call last)",
    "PermissionError",
    "Error response from daemon",
    " FAIL ",
    "CRITICAL",
)
_CRON_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})")


def _scan_cron_log(path, now, window_s, tail_lines=400, untimestamped_tail=40):
    """Count RECENT fault lines in one cron log. Returns (count, sample). Never
    raises — a missing/unreadable log yields (0, None) so this can't RED a run
    (e.g. in-container CI where the host logs dir isn't mounted). A line counts
    when it carries a signature AND (its embedded ISO ts is within window_s, OR
    it has no ts but sits in the last `untimestamped_tail` lines — so a healed
    fault ages out and stops warning)."""
    from datetime import datetime as _dt, timezone as _tz
    try:
        with open(path, "r", errors="replace") as fh:
            lines = fh.readlines()[-tail_lines:]
    except OSError:
        return (0, None)
    count, sample, n = 0, None, len(lines)
    for idx, line in enumerate(lines):
        if not any(sig in line for sig in _CRON_ERR_SIGNATURES):
            continue
        m = _CRON_ISO_RE.search(line)
        if m:
            try:
                ts = _dt.strptime(f"{m.group(1)}T{m.group(2)}", "%Y-%m-%dT%H:%M:%S") \
                    .replace(tzinfo=_tz.utc).timestamp()
            except ValueError:
                ts = None
            if ts is not None and (now - ts) > window_s:
                continue  # aged out of the window
        elif idx < n - untimestamped_tail:
            continue  # untimestamped and not in the recent tail → skip
        count += 1
        if sample is None:
            sample = line.strip()
    return (count, sample)


def cron_log_error_verdict(hits) -> str:
    """Pure verdict for check_cron_log_errors (unit-tested). `hits` is a list of
    (log_name, count, sample). WARN — NEVER RED — when any scanned log shows a
    recent fault (a stale/erroring cron log is not a runtime dependency and must
    not block a session or a push); else a clean GREEN detail. Never raises."""
    hits = [h for h in hits if h[1] > 0]
    if not hits:
        return "cron logs clean (no recent faults in scanned jobs)"
    parts = "; ".join(f"{name}:{count}" for name, count, _ in hits)
    sample = next((h[2] for h in hits if h[2]), "")
    tail = f" — e.g. {sample[:120]}" if sample else ""
    return f"WARN cron log faults (last 6h): {parts}{tail} [S1-B2]"


def check_cron_log_errors() -> str:
    """S1-B2 registry row — scans the cron-driven job logs for recent anchored
    faults (Traceback/PermissionError/daemon-restart/FAIL/CRITICAL). WARN only."""
    import os
    import time as _time
    log_dir = Path(os.environ.get("KAI_CRON_LOG_DIR", "/home/leo/kai-system/logs"))
    window_s = int(os.environ.get("KAI_CRON_LOG_WINDOW", "21600"))  # 6h
    now = _time.time()
    hits = []
    for name in _CRON_LOG_FILES:
        cnt, sample = _scan_cron_log(log_dir / name, now, window_s)
        if cnt:
            hits.append((name, cnt, sample))
    return cron_log_error_verdict(hits)


def host_hygiene_verdict(security, total, reboot_required, zombies, cache_age_days) -> str:
    """Pure verdict logic for check_host_hygiene (unit-tested directly). WARN text
    on any hygiene concern, else a clean GREEN detail. Never raises."""
    warns = []
    if security is None:
        warns.append("update counts unavailable")
    elif security > 0:
        warns.append(f"{security} security update(s) pending (of {total})")
    if reboot_required:
        warns.append("reboot-required")
    if zombies:
        warns.append(f"{zombies} zombie/defunct proc(s)")
    if cache_age_days is not None and cache_age_days > 3:
        warns.append(f"apt cache {cache_age_days:.0f}d stale (counts may understate)")
    if warns:
        return "WARN host hygiene: " + "; ".join(warns) + " [KAI-1161]"
    return "host hygiene clean (0 security pending, no reboot, no zombies)"


def check_host_hygiene() -> str:
    """KAI-1161 — nothing watched OS hygiene: a session found 9 pending security
    updates + a defunct zombie via the LOGIN BANNER, not any probe (same blind-spot
    class as the KAI-1159 codex-auth gap). Surfaces, as a WARN and NEVER a RED (host
    hygiene is not a runtime dependency and must not block a session or a push):
      - pending updates total;security via Ubuntu's canonical apt-check (cached apt
        state; no sudo, no network — never runs `apt update`)
      - /var/run/reboot-required
      - defunct/zombie process count
      - apt-cache staleness (>3d => the counts may understate reality)
    """
    import subprocess as _sp
    import time as _time

    total = security = None
    try:
        r = _sp.run(["/usr/lib/update-notifier/apt-check"], capture_output=True, text=True, timeout=20)
        raw = (r.stderr or r.stdout).strip()
        if ";" in raw:
            total, security = (int(x) for x in raw.split(";")[:2])
    except Exception:
        pass

    reboot_required = Path("/var/run/reboot-required").exists()

    zombies = 0
    try:
        stat = _sp.run(["ps", "-eo", "stat="], capture_output=True, text=True, timeout=8).stdout
        zombies = sum(1 for line in stat.splitlines() if line.strip().startswith("Z"))
    except Exception:
        zombies = 0

    cache_age_days = None
    try:
        stamp = Path("/var/lib/apt/periodic/update-success-stamp")
        if stamp.exists():
            cache_age_days = (_time.time() - stamp.stat().st_mtime) / 86400.0
    except Exception:
        cache_age_days = None

    return host_hygiene_verdict(security, total, reboot_required, zombies, cache_age_days)


def disk_pressure_eval(disk_pct, inode_pct, mem_avail_pct, swap_pct):
    """Pure verdict for check_disk_pressure (unit-tested). Returns (severity, detail)
    with severity in {"red","warn","green"}. RED-capable because disk/inode/memory
    exhaustion is a runtime threat (takes down Plane DB, Qdrant, Docker + co-located
    backups at once) — unlike the WARN-only hygiene/verifier probes."""
    reds, warns = [], []
    if disk_pct is not None:
        if disk_pct >= 92: reds.append(f"root disk {disk_pct:.0f}%")
        elif disk_pct >= 80: warns.append(f"root disk {disk_pct:.0f}%")
    if inode_pct is not None:
        if inode_pct >= 92: reds.append(f"inodes {inode_pct:.0f}%")
        elif inode_pct >= 80: warns.append(f"inodes {inode_pct:.0f}%")
    if mem_avail_pct is not None:
        if mem_avail_pct <= 3: reds.append(f"mem avail {mem_avail_pct:.0f}%")
        elif mem_avail_pct <= 10: warns.append(f"mem avail {mem_avail_pct:.0f}%")
    if swap_pct is not None and swap_pct >= 50:
        warns.append(f"swap {swap_pct:.0f}%")
    if reds:
        return ("red", "; ".join(reds + warns))
    if warns:
        return ("warn", "WARN disk/mem pressure: " + "; ".join(warns) + " [S1-B2]")
    parts = []
    if disk_pct is not None: parts.append(f"disk {disk_pct:.0f}%")
    if inode_pct is not None: parts.append(f"inodes {inode_pct:.0f}%")
    if mem_avail_pct is not None: parts.append(f"mem avail {mem_avail_pct:.0f}%")
    return ("green", " / ".join(parts) + " — ok")


def check_disk_pressure() -> str:
    """S1-B2 (audit #06) — root FS sat at 84% with no probe; ENOSPC would down Plane,
    Qdrant, Docker and the co-located backups. Probes root disk %, inode %, memory-
    available %, and swap %. RED (raises) on exhaustion thresholds; WARN otherwise."""
    import os
    import shutil

    disk_pct = inode_pct = mem_avail_pct = swap_pct = None
    try:
        du = shutil.disk_usage("/")
        disk_pct = du.used / du.total * 100 if du.total else None
    except Exception:
        pass
    try:
        st = os.statvfs("/")
        inode_pct = (st.f_files - st.f_ffree) / st.f_files * 100 if st.f_files else None
    except Exception:
        pass
    try:
        info = {}
        for line in open("/proc/meminfo"):
            k, _, v = line.partition(":")
            info[k] = int(v.strip().split()[0])
        if info.get("MemTotal"):
            mem_avail_pct = info.get("MemAvailable", 0) / info["MemTotal"] * 100
        if info.get("SwapTotal"):
            swap_pct = (info["SwapTotal"] - info.get("SwapFree", 0)) / info["SwapTotal"] * 100
    except Exception:
        pass

    severity, detail = disk_pressure_eval(disk_pct, inode_pct, mem_avail_pct, swap_pct)
    if severity == "red":
        raise RuntimeError(detail + " [S1-B2]")
    return detail


# W-1 #5: check_devops_custodian_runner + devops_runner_liveness_eval were DELETED —
# they were the "runner-liveness-of-the-runner" (who-watches-the-watcher) self-
# referential health declaration the invariant explicitly retires. A custodian runner
# that stalls no longer produces its remediation receipts; under the three-state gate
# that absence is UNKNOWN, not a false green — the honest signal, without a meta-watcher
# asserting the autonomy layer is "live". Design §4 (tear-out).


def check_container_roster() -> str:
    """S1-B2 (audit #08) — only 4 of 34 containers were gated, so a silently-exited
    service or a restart-loop (kai-tailscale rc=5) read as "Up" to every check.
    Enumerate all compose-managed containers (buzz/kai-system/plane): RED if any
    service that should be up is not running (a one-shot that exited 0, e.g.
    plane-migrator, is NOT a failure); WARN on elevated RestartCount (>=3)."""
    import subprocess as _sp

    PROJECTS = {"buzz", "kai-system", "plane"}
    try:
        out = _sp.run(
            ["docker", "ps", "-a", "--format",
             "{{.Names}}\t{{.Label \"com.docker.compose.project\"}}"],
            capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return "WARN container roster unavailable (docker unreachable) [S1-B2]"

    names = [ln.split("\t")[0] for ln in out.splitlines()
             if len(ln.split("\t")) >= 2 and ln.split("\t")[1] in PROJECTS]
    if not names:
        return "WARN no compose-managed containers found [S1-B2]"

    try:
        fmt = "{{.Name}}|{{.State.Status}}|{{.State.ExitCode}}|{{.HostConfig.RestartPolicy.Name}}|{{.RestartCount}}"
        insp = _sp.run(["docker", "inspect", "-f", fmt, *names],
                       capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return "WARN container inspect failed [S1-B2]"

    down, hot, checked = [], [], 0
    for line in insp.splitlines():
        parts = line.strip().lstrip("/").split("|")
        if len(parts) != 5:
            continue
        name, status, exit_code, policy, rc = parts
        checked += 1
        if status != "running":
            expects_up = policy in ("always", "unless-stopped")
            if expects_up or exit_code != "0":
                down.append(f"{name}={status}({exit_code})")
        elif rc.isdigit() and int(rc) >= 3:
            hot.append(f"{name} rc={rc}")

    if down:
        raise RuntimeError(f"{len(down)} managed container(s) down: " + ", ".join(down[:6]))
    if hot:
        return "WARN elevated restart counts: " + "; ".join(hot) + " [S1-B2]"
    return f"{checked} compose-managed containers healthy (one-shots excluded), restarts nominal"


def check_backup_freshness() -> str:
    """S1-B3/B2 (audit #01,#16) — backups and their WARNING log were unwatched.
    Newest Plane dump age + a scan of backup.log's tail for failures + stale temp
    artifacts. WARN (not RED): a stale backup is not an immediate runtime failure,
    but it must be impossible to miss (the offsite/restore work is B3 proper)."""
    import re as _re
    import time as _time
    from pathlib import Path as _Path

    base = None
    for cand in (_Path.home() / "backups", _Path("/home/leo/backups")):
        if cand.exists():
            base = cand
            break
    if base is None:
        return "WARN backups dir absent [S1-B3]"

    warns = []
    # Every store backup.sh writes must be fresh; a silently-failing
    # qdrant/n8n/buzz backup was exactly the audit #01 blind spot.
    STORES = {"plane": "*.sql.gz", "qdrant": "*.snapshot",
              "n8n": "*.tar.gz", "buzz": "*.sql.gz"}
    for store, pattern in STORES.items():
        sdir = base / store
        files = list(sdir.glob(pattern)) if sdir.exists() else []
        newest = max((f.stat().st_mtime for f in files), default=None)
        if newest is None:
            warns.append(f"{store}: no backup")
        else:
            age_h = (_time.time() - newest) / 3600
            if age_h > 26:
                warns.append(f"{store} {age_h:.0f}h old")

    log = base / "backup.log"
    if log.exists():
        try:
            tail = "".join(log.read_text(errors="ignore").splitlines(keepends=True)[-40:])
            if _re.search(r"WARNING|FAILED|ERROR|Traceback", tail):
                warns.append("backup.log tail shows WARNING/FAILED")
        except Exception:
            pass

    stale = list(base.glob("skip_manifest_*.txt"))
    if stale:
        warns.append(f"{len(stale)} stale skip_manifest artifact(s)")

    if warns:
        return "WARN backups: " + "; ".join(warns) + " [S1-B3]"
    return "backups fresh across plane/qdrant/n8n/buzz, log clean"


def expiry_severity(days, warn_days, red_days):
    """Pure verdict for an expiry-days value (unit-tested). None -> warn (unknown)."""
    if days is None:
        return "warn"
    if days <= red_days:
        return "red"
    if days <= warn_days:
        return "warn"
    return "green"


def check_tailscale_key_expiry() -> str:
    """S1-B2 (audit #04/#06) — the Tailscale node key expiry was unprobed; when it
    lapses, `ssh kai` AND the tailnet-bound worker-api :8001 both die at once, and
    re-auth is manual. RED (raises) <=7d because it is runtime-critical access; WARN
    <=14d. Read via the kai-tailscale container (host has no tailscale CLI)."""
    import json as _json
    import subprocess as _sp
    import time as _time
    from datetime import datetime as _dt

    try:
        out = _sp.run(["docker", "exec", "kai-tailscale", "tailscale", "status", "--json"],
                      capture_output=True, text=True, timeout=15).stdout
        key_expiry = (_json.loads(out).get("Self") or {}).get("KeyExpiry")
    except Exception:
        return "WARN tailscale key expiry unavailable [S1-B2]"
    if not key_expiry:
        return "tailscale node key: no expiry set (non-expiring)"
    try:
        exp = _dt.fromisoformat(key_expiry.replace("Z", "+00:00")).timestamp()
    except Exception:
        return "WARN tailscale key expiry unparseable [S1-B2]"

    days = (exp - _time.time()) / 86400
    sev = expiry_severity(days, 14, 7)
    if sev == "red":
        raise RuntimeError(f"tailscale node key expires in {days:.0f}d — lapse kills ssh + worker-api :8001 [S1-B2]")
    if sev == "warn":
        return f"WARN tailscale node key expires in {days:.0f}d — re-auth soon [S1-B2]"
    return f"tailscale node key valid ({days:.0f}d)"


def check_public_tls() -> str:
    """S1-B2 (audit #20) — TLS/cert monitoring covered one hostname; the n8n webhook
    cert could break while the old probe stayed green. Check every public endpoint's
    cert expiry via a plain ssl socket. RED (raises) <=3d (renewal has failed); WARN
    <=14d or unreachable."""
    import socket as _socket
    import ssl as _ssl
    import time as _time
    from datetime import datetime as _dt, timezone as _tz

    ENDPOINTS = ["kai.sonicink.space", "n8n.sonicink.space"]
    ctx = _ssl.create_default_context()
    reds, warns, oks = [], [], []
    for host in ENDPOINTS:
        try:
            with ctx.wrap_socket(_socket.create_connection((host, 443), timeout=8),
                                 server_hostname=host) as sock:
                not_after = sock.getpeercert()["notAfter"]
            exp = _dt.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=_tz.utc).timestamp()
            days = (exp - _time.time()) / 86400
            sev = expiry_severity(days, 14, 3)
            if sev == "red":
                reds.append(f"{host} {days:.0f}d")
            elif sev == "warn":
                warns.append(f"{host} {days:.0f}d")
            else:
                oks.append(f"{host} {days:.0f}d")
        except Exception:
            warns.append(f"{host} unreachable")

    if reds:
        raise RuntimeError("public TLS cert near-expiry: " + "; ".join(reds + warns) + " [S1-B2]")
    if warns:
        return "WARN public TLS: " + "; ".join(warns) + " [S1-B2]"
    return "public TLS certs valid (" + ", ".join(oks) + ")"


def check_backup_verify() -> str:
    """S1-B3 (audit #01) — backups were never restore-tested. verify_backups.sh
    (weekly cron) integrity-checks every store (gzip/tar + sqlite PRAGMA) and
    stamps ~/backups/.verify_result. RED if the last verify FAILED (backups may
    not restore); WARN if never run or stale (>8d)."""
    import time as _time
    from pathlib import Path as _Path

    base = None
    for cand in (_Path.home() / "backups", _Path("/home/leo/backups")):
        if cand.exists():
            base = cand
            break
    if base is None:
        return "WARN backups dir absent [S1-B3]"

    stamp = base / ".verify_result"
    if not stamp.exists():
        return "WARN backup verify never run (no .verify_result) [S1-B3]"
    try:
        result = stamp.read_text().strip().split()[0]
    except Exception:
        return "WARN backup verify result unreadable [S1-B3]"

    age_d = (_time.time() - stamp.stat().st_mtime) / 86400
    if result == "FAIL":
        raise RuntimeError(f"backup verify FAILED {age_d:.0f}d ago — backups may not restore [S1-B3]")
    if age_d > 8:
        return f"WARN backup verify stale ({age_d:.0f}d — weekly expected) [S1-B3]"
    return f"backup verify PASS ({age_d:.0f}d ago)"


def offsite_freshness_verdict(enabled: bool, result, age_h):
    """Pure verdict for the offsite backup copy — S1-B3 (audit #01, "RED freshness probe").
    enabled: offsite.env sets OFFSITE_ENABLED=1. result: first token of
    ~/backups/.offsite_result ('OK'|'FAIL') or None if never run. age_h: hours since
    the stamp, or None. WARN while the transport is still gated/disabled (a real but
    non-runtime gap); RED once enabled and the offsite copy has failed or gone stale
    (>36h) — that is DR protection actually lapsed."""
    if not enabled:
        return "warn", "offsite transport not enabled (staged, awaiting gate) [S1-B3]"
    if result == "FAIL":
        return "red", "offsite sync FAILED — no current offsite copy [S1-B3]"
    if result is None or age_h is None:
        return "warn", "offsite sync never run (cron pending) [S1-B3]"
    if age_h > 36:
        return "red", f"offsite copy stale ({age_h:.0f}h — DR protection lapsed) [S1-B3]"
    return "green", f"offsite copy fresh ({age_h:.0f}h ago)"


def check_offsite_freshness() -> str:
    """S1-B3 (audit #01, Track 3) — the offsite copy is the disaster-recovery leg;
    an unwatched offsite is as good as none. Reads offsite.env (enabled?) + the
    ~/backups/.offsite_result stamp offsite_sync.sh writes. See offsite_freshness_verdict."""
    import time as _time
    from pathlib import Path as _Path

    base = None
    for cand in (_Path.home() / "backups", _Path("/home/leo/backups")):
        if cand.exists():
            base = cand
            break
    if base is None:
        return "WARN backups dir absent [S1-B3]"

    enabled = False
    for cand in (_Path.home() / "kai-system" / "offsite.env",
                 _Path("/home/leo/kai-system/offsite.env")):
        if cand.exists():
            try:
                for line in cand.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("OFFSITE_ENABLED="):
                        enabled = line.split("=", 1)[1].strip().strip('"').strip("'") == "1"
            except Exception:
                pass
            break

    stamp = base / ".offsite_result"
    result, age_h = None, None
    if stamp.exists():
        try:
            result = stamp.read_text().strip().split()[0]
        except Exception:
            result = None
        age_h = (_time.time() - stamp.stat().st_mtime) / 3600

    sev, detail = offsite_freshness_verdict(enabled, result, age_h)
    if sev == "red":
        raise RuntimeError(detail)
    if sev == "warn":
        return "WARN " + detail
    return detail


def alert_delivery_verdict(result, age_h):
    """Pure verdict for the alert-channel delivery heartbeat — S1-B4 (audit #03),
    W-1 reference witness. `result` is the first token of ~/backups/.alert_heartbeat:
    the three-state receipt verdict 'GREEN'|'UNKNOWN'|'RED' (legacy 'OK'|'FAIL' still
    honoured), or None. age_h: hours since the stamp, or None.

    GREEN is granted ONLY when the heartbeat recorded a real Telegram message_id
    receipt for its run (verdict GREEN/OK) AND the proof is fresh — an external
    party confirmed delivery. RED when it FAILED its receipt (RED/FAIL) or the proof
    is stale (>36h): an alert channel we cannot prove delivers is the silent-outage
    risk B4 exists to kill. UNKNOWN (the witness could not attest) blocks too — no
    receipt is never GREEN — surfaced as RED in this two-state baseline until the
    three-state headline lands (design build order #4). WARN when it never ran."""
    if result is None or age_h is None:
        return "warn", "alert-delivery heartbeat never run [S1-B4]"
    if result in ("RED", "FAIL"):
        return "red", "alert channel FAILED delivery receipt — pages may not reach Leo [S1-B4]"
    if result == "UNKNOWN":
        return "red", "alert channel UNKNOWN — no external delivery receipt [S1-B4]"
    if age_h > 36:
        return "red", f"alert-delivery heartbeat stale ({age_h:.0f}h — channel unproven) [S1-B4]"
    if result not in ("GREEN", "OK"):
        return "red", f"alert-delivery receipt unrecognised ({result!r}) — channel unproven [S1-B4]"
    return "green", f"alert channel delivery-verified ({age_h:.0f}h ago)"


def check_alert_delivery() -> str:
    """S1-B4 (audit #03) — the daily alert_delivery_heartbeat proves Telegram can
    actually DELIVER (message_id receipt), then stamps ~/backups/.alert_heartbeat.
    See alert_delivery_verdict."""
    import time as _time
    from pathlib import Path as _Path

    stamp = None
    for base in (_Path.home() / "backups", _Path("/home/leo/backups")):
        cand = base / ".alert_heartbeat"
        if cand.exists():
            stamp = cand
            break

    result, age_h = None, None
    if stamp is not None:
        try:
            result = stamp.read_text().strip().split()[0]
        except Exception:
            result = None
        age_h = (_time.time() - stamp.stat().st_mtime) / 3600

    sev, detail = alert_delivery_verdict(result, age_h)
    if sev == "red":
        raise RuntimeError(detail)
    if sev == "warn":
        return "WARN " + detail
    return detail


_RAIL_CANARY_SCRIPT = r"""
import os, sys
BASE = "/run/hostops-payload-secrets"
try:
    from hostops_identity import HostOpsSecretResolver
except Exception as e:
    print("CANARY_IMPORTERR %s: %s" % (type(e).__name__, str(e)[:120])); sys.exit(0)
pick = None
try:
    for site in sorted(os.listdir(BASE)):
        d = os.path.join(BASE, site)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if os.path.isfile(os.path.join(d, name)):
                pick = (site, name); break
        if pick:
            break
except FileNotFoundError:
    print("CANARY_EMPTY store-absent"); sys.exit(0)
if not pick:
    print("CANARY_EMPTY no-payload"); sys.exit(0)
site, name = pick
try:
    b = HostOpsSecretResolver().resolve(site, name)   # returns bytes; only len() is ever printed
    print("CANARY_OK %s %s %d" % (site, name, len(b)))
except Exception as e:
    print("CANARY_FAIL %s %s: %s" % (site, type(e).__name__, str(e)[:120]))
"""


def check_hostops_rail_canary() -> str:
    """KAI-1166 [S1-B1] — the approve-and-execute rail (KAI-984) is the execution
    path for every credential move. Audit F2 read it as "un-executable" because the
    payload store looks empty from the host — but /run/hostops-payload-secrets is a
    bind-mount that is only populated inside the orchestrator container. Nothing
    exercised the rail end-to-end, so a real resolve regression (bad mount, wrong
    owner/mode, moved module) could rot silently until a live secret move failed.

    This canary execs the resolver INSIDE kai-orchestrator against the first staged
    payload and asserts it resolves to bytes. Secret material never leaves the
    container — only its byte length is printed. RED if the rail cannot resolve a
    payload (no payload staged, or resolve raises); WARN only if the container/docker
    is unreachable (that failure is already owned by services_up / container_roster).
    """
    try:
        result = subprocess.run(
            ["docker", "exec", "kai-orchestrator", "python3", "-c", _RAIL_CANARY_SCRIPT],
            text=True, capture_output=True, timeout=20, check=False,
        )
    except Exception as exc:
        return f"WARN hostops rail canary could not run (docker unreachable: {type(exc).__name__}) [KAI-1166]"

    out = (result.stdout or "").strip().splitlines()
    token = out[-1].strip() if out else ""
    if result.returncode != 0 and not token:
        err = (result.stderr or "").strip().replace("\n", " ")
        if "No such container" in err or "Cannot connect" in err or "not running" in err:
            return "WARN hostops rail canary skipped — kai-orchestrator not running [KAI-1166]"
        return f"WARN hostops rail canary error: {err[:160] or 'exit ' + str(result.returncode)} [KAI-1166]"

    if token.startswith("CANARY_OK"):
        parts = token.split()
        site = parts[1] if len(parts) > 1 else "?"
        secret = parts[2] if len(parts) > 2 else "?"
        nbytes = parts[3] if len(parts) > 3 else "?"
        return f"rail resolved {site}/{secret} ({nbytes}B) end-to-end in kai-orchestrator [KAI-1166]"
    if token.startswith("CANARY_EMPTY"):
        raise RuntimeError(
            "hostops rail has NO resolvable payload staged (" + token.split(maxsplit=1)[-1]
            + ") — rail un-executable [KAI-1166]")
    if token.startswith("CANARY_FAIL"):
        raise RuntimeError("hostops rail cannot resolve payload — " + token[len("CANARY_FAIL "):] + " [KAI-1166]")
    if token.startswith("CANARY_IMPORTERR"):
        raise RuntimeError("hostops rail resolver import failed in-container — " + token[len("CANARY_IMPORTERR "):] + " [KAI-1166]")
    return f"WARN hostops rail canary unexpected output: {token[:160] or '(empty)'} [KAI-1166]"


def check_cloudways_auth() -> str:
    """S1-B5 (audit F5) — the Cloudways API token (WP fleet host API) was unprobed;
    the audit read it as dead (403) with detection left to Leo. It authenticates now,
    but a silent lapse would blind WP fleet host ops with no warning. Best-effort OAuth
    token request: GREEN on 200, WARN on 401/403 (rotate) or any transport failure.
    WARN never RED — the WP fleet is not the runtime spine, so a dead fleet token must
    never turn the baseline red or block a push; it only has to be impossible to miss.
    The token is sent in a POST body only and is never logged or returned."""
    import urllib.parse as _up

    try:
        email = (SECRETS / "cloudways_account_email.txt").read_text().strip()
        api_key = (SECRETS / "cloudways_api_token.txt").read_text().strip()
    except Exception:
        return "WARN cloudways credentials unreadable — WP fleet host API unverifiable [S1-B5]"
    if not email or not api_key:
        return "WARN cloudways credentials empty — WP fleet host API unverifiable [S1-B5]"

    data = _up.urlencode({"email": email, "api_key": api_key}).encode()
    # Cloudways' WAF 403s the default Python-urllib User-Agent — send a real one, else
    # the probe false-WARNs on a perfectly valid token (verified: UA=None→403, UA set→200).
    request = urllib.request.Request(
        "https://api.cloudways.com/api/v1/oauth/access_token", data=data, method="POST",
        headers={"User-Agent": "KAI-green-baseline/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status == 200:
                return "cloudways API token valid (WP fleet host API reachable) [S1-B5]"
            return f"WARN cloudways auth returned HTTP {response.status} — check token [S1-B5]"
    except urllib.error.HTTPError as exc:
        return f"WARN cloudways API token rejected (HTTP {exc.code}) — rotate token [S1-B5]"
    except Exception as exc:
        return f"WARN cloudways auth unreachable ({type(exc).__name__}) — WP fleet host API unverifiable [S1-B5]"


def credential_registry_verdict(registry: dict, present: list[str]) -> tuple[str, str]:
    """Pure verdict (unit-tested) over a readiness registry + the credentials actually
    on disk. This is the inventory-first guard the audit demanded (Fable F6): every
    credential must be enumerated, so coverage — not the memory of a past incident —
    decides what is watched.

    RED  → a runtime-critical registered credential's backing file is absent.
    WARN → a credential exists on disk but matches no registry row (unregistered drift).
    GREEN → full surface enumerated, all registered, every runtime-critical one present.
    """
    creds = registry.get("credentials", []) or []
    names = {row.get("name") for row in creds}
    compiled = []
    for pat in registry.get("patterns", []) or []:
        try:
            compiled.append(re.compile(pat["pattern"]))
        except (re.error, KeyError, TypeError):
            continue

    def covered(name: str) -> bool:
        return name in names or any(rx.match(name) for rx in compiled)

    present_set = set(present)
    unregistered = sorted(n for n in present_set if not covered(n))
    critical = {row["name"] for row in creds
                if row.get("criticality") == "runtime-critical" and row.get("name")}
    missing_critical = sorted(critical - present_set)

    if missing_critical:
        return "red", (f"{len(missing_critical)} runtime-critical credential(s) MISSING from "
                       f"secrets/: {', '.join(missing_critical[:5])} [S1-B2]")
    if unregistered:
        return "warn", (f"{len(unregistered)} credential(s) on disk not in readiness registry: "
                        f"{', '.join(unregistered[:6])} — add a row [S1-B2]")
    return "green", (f"{len(present_set)} credentials enumerated; all registered, "
                     f"{len(critical)} runtime-critical present [S1-B2]")


def check_credential_registry() -> str:
    """S1-B2 (audit #02 program item 2, Fable F6) — the green baseline was built
    corpse-by-corpse: one probe per credential that had already burned a session, and
    no top-down enumeration of the dependency surface. This probe reads a declarative
    registry (scripts/readiness_registry.json) and asserts the credential inventory is
    complete and accounted-for: every secrets/*.txt is registered (else WARN), every
    runtime-critical credential is present (else RED). Expiry/liveness for the expiring
    subset stays in the dedicated probes; this owns coverage, not freshness."""
    import glob as _glob

    registry_path = Path(__file__).resolve().parent / "readiness_registry.json"
    try:
        registry = json.loads(registry_path.read_text())
    except Exception as exc:
        return f"WARN readiness registry unreadable ({type(exc).__name__}) [S1-B2]"

    present = sorted(Path(p).stem for p in _glob.glob(str(SECRETS / "*.txt")))
    if not present:
        return "WARN no credential files found under secrets/ — registry cannot be verified [S1-B2]"

    sev, detail = credential_registry_verdict(registry, present)
    if sev == "red":
        raise RuntimeError(detail)
    if sev == "warn":
        return "WARN " + detail
    return detail


# In-container leak-guard: run INSIDE kai-orchestrator (the DB + the /jobs endpoint
# are container-local). It is the durable tripwire for the 443fb11e / L18 class —
# a WP app-password once flowed from load_config into steps.result and was served
# in cleartext by GET /jobs/{id}, landing in a session transcript. The generic
# redaction chokepoint cannot know secret VALUES; this probe can, so it reads the
# real app-passwords and asserts none appear in what the endpoint actually serves.
#
# Method — value-based, on real data: (1) load the live app-password values from
# /run/wp_secrets; (2) find the "at-risk" jobs whose RAW stored data still contains
# one (legacy pre-fix rows are permanent regression fixtures); (3) GET each through
# the live /jobs/{id} and assert the value is NOT in the served response. If the
# serve-path redaction ever regresses, a real credential reappears here and the row
# goes RED. Scope note: this watches the EXPOSURE surface (what a session reads),
# which is the actual leak vector; the persist-layer scrub is locked by unit tests.
# Secret material never leaves the container — only sentinels + counts are printed.
_LEAKGUARD_SCRIPT = r"""
import json, os, sqlite3, sys, urllib.request

DB = "/data/orchestrator/orchestrator.db"
BASE = "http://localhost:8003"

# ── load live secret VALUES; any unreadable source downgrades the run to WARN ──
# /run/wp_secrets is the canonical WP app-password dir and is REQUIRED: if it is
# missing/unreadable the inventory is incomplete and the run must not read clean.
# /run/secrets is an OPTIONAL augment (may legitimately be absent).
REQUIRED_DIRS = ("/run/wp_secrets",)
OPTIONAL_DIRS = ("/run/secrets",)
values = set()
skipped = 0
for base, required in [(d, True) for d in REQUIRED_DIRS] + [(d, False) for d in OPTIONAL_DIRS]:
    try:
        names = os.listdir(base)
    except OSError:
        if required:
            skipped += 1  # a required source we could not read → not confirmable
        continue
    for name in names:
        if "app_password" in name or "password" in name or name.startswith("wp_"):
            try:
                v = open(os.path.join(base, name)).read().strip()
            except OSError:
                skipped += 1
                continue
            if "\x00" in v:
                skipped += 1
                continue
            if len(v) >= 8:
                values.add(v)
if skipped:
    # a required secret source was unreadable — inventory incomplete, cannot
    # claim clean regardless of what the readable sources yielded.
    print("LEAKGUARD_SKIPPED 0 0")
    sys.exit(0)
if not values:
    print("LEAKGUARD_NOSECRETS")
    sys.exit(0)

# Search raw storage for BOTH the literal value and its JSON-escaped inner form —
# stored blobs are JSON, so a value with an escapable char is escaped there. This
# keeps at-risk discovery from a false-negative (which would false-GREEN).
def _variants(v):
    out = {v}
    try:
        out.add(json.dumps(v)[1:-1])
    except Exception:
        pass
    return out

variants = set()
for v in values:
    variants |= _variants(v)

def _like(s):
    esc = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + esc + "%"

# Read-only DB access — the probe must never create or mutate the jobs DB.
try:
    c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    c.execute("PRAGMA query_only=ON")
    at_risk = set()
    for s in variants:
        if "\x00" in s:
            continue
        pat = _like(s)
        for (jid,) in c.execute(
                "SELECT DISTINCT job_id FROM steps WHERE result LIKE ? ESCAPE '\\' "
                "OR error LIKE ? ESCAPE '\\'", (pat, pat)):
            at_risk.add(jid)
        for (jid,) in c.execute(
                "SELECT id FROM jobs WHERE inputs LIKE ? ESCAPE '\\'", (pat,)):
            at_risk.add(jid)
    at_risk = list(at_risk)
except Exception as e:
    print("LEAKGUARD_DBERR " + type(e).__name__)
    sys.exit(0)

if not at_risk:
    # no stored secret anywhere → nothing to expose; WARN if a source was skipped
    print("LEAKGUARD_SKIPPED 0 0" if skipped else "LEAKGUARD_OK 0 0")
    sys.exit(0)

# Parse the served JSON and inspect DECODED scalar strings (keys + values) — this
# sees the secret regardless of how many escaping layers the transport added, and
# avoids matching across JSON syntax boundaries (which could false-RED).
def _walk(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, val in obj.items():
            yield k
            for s in _walk(val):
                yield s
    elif isinstance(obj, list):
        for val in obj:
            for s in _walk(val):
                yield s

def _served_leaks(doc):
    for s in _walk(doc):
        for v in values:
            if v in s:
                return True
        # a scalar may itself be nested serialized JSON — decode one more layer
        st = s.strip()
        if st[:1] in ("{", "[") and any(v in s for v in variants):
            try:
                if _served_leaks(json.loads(st)):
                    return True
            except Exception:
                pass
    return False

leaks = 0
scanned = 0
failed = 0
truncated = len(at_risk) > 100
first = ""
for jid in at_risk[:100]:
    req = urllib.request.Request(BASE + "/jobs/" + jid)
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        if getattr(resp, "status", 200) != 200:
            failed += 1
            continue
        doc = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        failed += 1  # a failed/malformed read must NEVER read as verified-clean
        continue
    scanned += 1
    if _served_leaks(doc):
        leaks += 1
        if not first:
            first = jid

if leaks:
    print("LEAKGUARD_LEAK %s %d %d" % (first, leaks, scanned))
elif scanned == 0:
    # had at-risk jobs but verified none — endpoint unreachable; do NOT read green
    print("LEAKGUARD_UNVERIFIED %d %d" % (failed, len(at_risk)))
elif failed or skipped:
    print("LEAKGUARD_PARTIAL %d %d %d" % (scanned, failed, len(at_risk)))
elif truncated:
    print("LEAKGUARD_TRUNC %d %d" % (scanned, len(at_risk)))
else:
    print("LEAKGUARD_OK %d %d" % (scanned, len(at_risk)))
"""

_LEAKGUARD_TIMEOUT = 30


def jobs_secret_leak_verdict(token: str) -> tuple[str, str]:
    """Pure verdict (unit-tested) over the in-container leak-guard sentinel.

    RED  → a live credential VALUE appears in a served /jobs response.
    WARN → the guard could not run to a clean conclusion (no secrets found, DB
           unreadable, unexpected output) — never a false RED.
    GREEN → every at-risk job served its credential redacted; 0 values exposed.
    """
    t = (token or "").strip()
    parts = t.split()
    head = parts[0] if parts else ""

    def _nn_ints(rest):
        """Parse trailing count tokens as non-negative ints, else None."""
        try:
            vals = [int(x) for x in rest]
        except (TypeError, ValueError):
            return None
        return vals if all(v >= 0 for v in vals) else None

    # Exact head match (not startswith) so a malformed prefix like LEAKGUARD_OKAY
    # cannot masquerade as a clean verdict.
    if head == "LEAKGUARD_LEAK":
        jid = parts[1] if len(parts) > 1 else "?"
        n = parts[2] if len(parts) > 2 else "?"
        return "red", (f"SECRET LEAK: /jobs serves a live credential in cleartext — "
                       f"job {jid} ({n} affected); serve-path redaction regressed [443fb11e/L18]")
    if head == "LEAKGUARD_OK":
        n = _nn_ints(parts[1:3])
        # GREEN only on a well-formed token where every at-risk job was scanned
        # (scanned == total). Anything else is treated as unverified → WARN.
        if n is None or len(n) != 2 or n[0] != n[1]:
            return "warn", f"jobs leak-guard: malformed OK token {t[:80]!r} — treating as unverified [443fb11e/L18]"
        return "green", (f"jobs leak-guard: {n[0]} at-risk job(s) served redacted, "
                         f"0 credential values exposed [443fb11e/L18]")
    if head == "LEAKGUARD_TRUNC":
        n = _nn_ints(parts[1:3]) or ["?", "?"]
        return "warn", (f"jobs leak-guard: {n[0]}/{n[1]} at-risk jobs served redacted "
                        f"(0 exposed in scanned set) — >100 at-risk, scan capped [443fb11e/L18]")
    if head == "LEAKGUARD_UNVERIFIED":
        total = parts[2] if len(parts) > 2 else "?"
        return "warn", (f"jobs leak-guard: could NOT verify any of {total} at-risk job(s) — every "
                        f"/jobs read failed; endpoint unreachable, NOT confirmed clean [443fb11e/L18]")
    if head == "LEAKGUARD_PARTIAL":
        scanned = parts[1] if len(parts) > 1 else "?"
        failed = parts[2] if len(parts) > 2 else "?"
        total = parts[3] if len(parts) > 3 else "?"
        return "warn", (f"jobs leak-guard: {scanned}/{total} at-risk jobs verified clean but "
                        f"{failed} unreachable/skipped — incomplete, not fully confirmed [443fb11e/L18]")
    if head == "LEAKGUARD_SKIPPED":
        return "warn", ("jobs leak-guard: a secret source was unreadable — inventory "
                        "incomplete, not confirmed clean [443fb11e/L18]")
    if head == "LEAKGUARD_NOSECRETS":
        return "warn", "jobs leak-guard: no app-password secrets found to scan for [443fb11e/L18]"
    if head == "LEAKGUARD_DBERR":
        return "warn", (f"jobs leak-guard: orchestrator DB unreadable "
                        f"({parts[1] if len(parts) > 1 else '?'}) [443fb11e/L18]")
    return "warn", f"jobs leak-guard: unexpected probe output: {t[:120] or '(empty)'} [443fb11e/L18]"


def check_jobs_secret_leak() -> str:
    """443fb11e / L18 — standing tripwire that a WP app-password never reaches the
    /jobs read surface a session consumes. Execs the value-based guard inside
    kai-orchestrator (see _LEAKGUARD_SCRIPT). RED if any live credential value is
    served in cleartext; WARN only if the guard could not run (docker/container
    unreachable — already owned by services_up / container_roster)."""
    try:
        result = subprocess.run(
            ["docker", "exec", "kai-orchestrator", "python3", "-c", _LEAKGUARD_SCRIPT],
            text=True, capture_output=True, timeout=_LEAKGUARD_TIMEOUT, check=False,
        )
    except Exception as exc:
        return f"WARN jobs leak-guard could not run (docker unreachable: {type(exc).__name__}) [443fb11e/L18]"

    # A nonzero exit means the in-container script crashed (it sys.exit(0)s on
    # every handled path) — never trust a stray OK-looking stdout line in that
    # case; fail closed to WARN.
    if result.returncode != 0:
        err = (result.stderr or "").strip().replace("\n", " ")
        if "No such container" in err or "Cannot connect" in err or "not running" in err:
            return "WARN jobs leak-guard skipped — kai-orchestrator not running [443fb11e/L18]"
        return f"WARN jobs leak-guard crashed (exit {result.returncode}): {err[:140] or '(no stderr)'} [443fb11e/L18]"

    out = (result.stdout or "").strip().splitlines()
    token = out[-1].strip() if out else ""
    if not token:
        return "WARN jobs leak-guard produced no output — cannot confirm clean [443fb11e/L18]"

    sev, detail = jobs_secret_leak_verdict(token)
    if sev == "red":
        raise RuntimeError(detail)
    if sev == "warn":
        return "WARN " + detail
    return detail


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
        Check("credential_registry", check_credential_registry),
        Check("jobs_secret_leak", check_jobs_secret_leak),
        Check("source_drift", check_source_drift),
        Check("fleet_visibility", check_fleet),
        Check("codex_verifier_auth", check_codex_verifier_auth),
        Check("hostops_rail_canary", check_hostops_rail_canary),
        Check("host_hygiene", check_host_hygiene),
        Check("cron_log_error_scan", check_cron_log_errors),
        Check("disk_pressure", check_disk_pressure),
        Check("container_roster", check_container_roster),
        Check("backup_freshness", check_backup_freshness),
        Check("tailscale_key_expiry", check_tailscale_key_expiry),
        Check("public_tls", check_public_tls),
        Check("cloudways_auth", check_cloudways_auth),
        Check("backup_verify", check_backup_verify),
        Check("offsite_freshness", check_offsite_freshness),
        # The ONE journey wired today: alert_delivery reads a real Telegram
        # message_id receipt (W-1 #2, the reference witness). Every other check
        # above is a diagnostic — it explains failures, it does not grant green.
        Check("alert_delivery", check_alert_delivery, journey=True),
    )


def run_suite(suite: tuple[Check, ...] | None = None) -> int:
    """Run the baseline as a three-state gate (W-1 external-witness invariant).

    JOURNEYS grant GREEN — each reads an external receipt and yields GREEN /
    UNKNOWN (no fresh receipt — amber) / RED (affirmative failure). DIAGNOSTICS
    (the former 27 proxies) explain WHY a journey failed; a diagnostic that raises
    is a real infra failure and still blocks, but a passing diagnostic no longer by
    itself means the system is healthy — only a witnessed journey does.

    Exit gate (what CI and the close key on): RED (1) on any RED journey or any RED
    diagnostic. AMBER/UNKNOWN journeys are reported loudly but do NOT block — a
    journey without a live driver is honestly UNKNOWN, not a failure, and blocking
    on it would brick the close before that driver lands. Set WITNESS_STRICT=1 to
    make UNKNOWN block too, once every journey has a driver. GREEN is printed only
    when every journey is witnessed GREEN and no diagnostic is RED.
    """
    import os as _os

    suite = suite or checks()
    print("KAI GREEN BASELINE — START")
    diag_failed: list[str] = []
    journeys: list[tuple[str, str]] = []  # (name, verdict)
    for check in suite:
        try:
            detail = check.probe()
            if check.journey:
                verdict = "UNKNOWN" if detail.startswith("WARN") else "GREEN"
                journeys.append((check.name, verdict))
                print(f"{verdict} [journey:{check.name}] {detail}")
            else:
                print(f"GREEN [{check.name}] {detail}")
        except Exception as exc:
            if check.journey:
                journeys.append((check.name, "RED"))
                print(f"RED [journey:{check.name}] {exc}")
            else:
                diag_failed.append(check.name)
                print(f"RED [{check.name}] {exc}")

    j_red = [n for n, v in journeys if v == "RED"]
    j_unknown = [n for n, v in journeys if v == "UNKNOWN"]
    j_green = [n for n, v in journeys if v == "GREEN"]
    if journeys:
        print(f"JOURNEYS WITNESSED — {len(j_green)} green · "
              f"{len(j_unknown)} unknown · {len(j_red)} red "
              f"(green granted by journeys only; {len(suite) - len(journeys)} diagnostics)")

    # A RED journey or a RED diagnostic (real infra failure) blocks.
    blocking = j_red + diag_failed
    if blocking:
        print("KAI GREEN BASELINE — RED: " + ", ".join(blocking))
        return 1
    # No RED, but a journey could not be witnessed => AMBER, honestly not green.
    if j_unknown:
        print("KAI GREEN BASELINE — AMBER: unwitnessed journey(s) " + ", ".join(j_unknown))
        return 1 if _os.environ.get("WITNESS_STRICT") == "1" else 0
    print("KAI GREEN BASELINE — GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(run_suite())

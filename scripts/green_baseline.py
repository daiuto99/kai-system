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
    # per this suite's read-only contract): if :4001 is gone, this goes RED.
    models = parse_model_ids(_request("http://localhost:4001/v1/models"))
    missing = {"kai", "sky", "roads", "coach"} - models
    if missing:
        raise RuntimeError("Buzz shim :4001 missing advisor models: " + ", ".join(sorted(missing)))
    # KAI-1182: the instant :4001 poke passing is NOT proof the advisor path is healthy.
    # On 2026-08-21 the shim wedged (2.5h connection-refused) yet this check read GREEN
    # at the next session start because it samples the instant. Fold in the last ~30 min
    # of observed round-trip behavior from the advisor_dm_probe heartbeat so a RECENT
    # outage cannot read clean: a failed/stale probe window downgrades GREEN -> WARN
    # (never RED — a dead :4001 already RED'd above; this only catches recent-but-recovered).
    window = _advisor_probe_window()
    if window:
        # WARN leads the string (host_hygiene/codex convention) so the row reads
        # "GREEN [buzz_shim_backend] WARN ..." — a pass, but a visible recent-outage flag.
        return window + " — :4001 live now (kai/sky/roads/coach)"
    return "Buzz advisor shim (:4001) serves kai/sky/roads/coach"


def _advisor_probe_window() -> str:
    """Read the advisor_dm_probe heartbeat; return a WARN string if the recent
    round-trip window is unhealthy/stale, '' if the window is healthy. Never raises —
    a missing/unparseable heartbeat yields '' (the instant poke above already passed;
    we do not fabricate an outage from a transient read miss)."""
    import json as _json
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    STALE_SEC = 40 * 60  # probe runs every 15 min; >40 min = ~2+ missed cycles = probe stalled
    state = None
    for candidate in (Path("/vault/_advisor_probe_state.json"),
                      Path("/home/leo/vault/_advisor_probe_state.json")):
        try:
            state = _json.loads(candidate.read_text())
            break
        except Exception:
            continue
    if not isinstance(state, dict):
        return ""  # no heartbeat readable -> defer to the instant poke (no false alarm)
    # INVARIANT (KAI-1182): the window read must NEVER raise/RED the baseline — the
    # instant poke above already proved :4001 live. A single broad guard so ANY
    # malformed field (e.g. consecutive_failures: null -> int(None), bad timestamp)
    # degrades to '' (defer to the instant poke) rather than turning the row RED.
    def _int(v) -> int:
        try:
            return int(v)
        except Exception:
            return 0
    try:
        if not state.get("ok", True):
            return (f"WARN last advisor round-trip FAILED ({state.get('advisor', '?')}: "
                    f"{str(state.get('reason', '?'))[:60]}; "
                    f"{_int(state.get('consecutive_failures', 0))} consecutive) [KAI-1182]")
        if _int(state.get("consecutive_failures", 0)) > 0:
            return (f"WARN advisor round-trip recovering "
                    f"({_int(state.get('consecutive_failures', 0))} recent fail) [KAI-1182]")
        last_ok = state.get("last_ok")
        if last_ok:
            age = _time.time() - _dt.fromisoformat(str(last_ok).replace("Z", "+00:00")).timestamp()
            if age > STALE_SEC:
                return (f"WARN no healthy advisor round-trip in {int(age // 60)}m "
                        f"(probe stalled?) [KAI-1182]")
        return ""  # window healthy
    except Exception:
        return ""  # never RED / never raise from the window read (KAI-1182 invariant)


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
_CRON_LOG_FILES = (
    "advisor_dm_probe.log", "relay_roundtrip_probe.log", "fleet_heartbeat.log",
    "meta_monitor.log", "alert_delivery_heartbeat.log",
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
    """Pure verdict for the alert-channel delivery heartbeat — S1-B4 (audit #03).
    result: first token of ~/backups/.alert_heartbeat ('OK'|'FAIL') or None. age_h:
    hours since the stamp, or None. RED when the channel last FAILED its delivery
    receipt or the proof is stale (>36h) — an alert channel we cannot prove delivers
    is the silent-outage risk B4 exists to kill; WARN when the heartbeat never ran."""
    if result is None or age_h is None:
        return "warn", "alert-delivery heartbeat never run [S1-B4]"
    if result == "FAIL":
        return "red", "alert channel FAILED delivery receipt — pages may not reach Leo [S1-B4]"
    if age_h > 36:
        return "red", f"alert-delivery heartbeat stale ({age_h:.0f}h — channel unproven) [S1-B4]"
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
        Check("codex_verifier_auth", check_codex_verifier_auth),
        Check("host_hygiene", check_host_hygiene),
        Check("cron_log_error_scan", check_cron_log_errors),
        Check("disk_pressure", check_disk_pressure),
        Check("container_roster", check_container_roster),
        Check("backup_freshness", check_backup_freshness),
        Check("tailscale_key_expiry", check_tailscale_key_expiry),
        Check("public_tls", check_public_tls),
        Check("backup_verify", check_backup_verify),
        Check("offsite_freshness", check_offsite_freshness),
        Check("alert_delivery", check_alert_delivery),
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

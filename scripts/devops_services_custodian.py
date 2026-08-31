#!/usr/bin/env python3
"""Services/containers custodian — KAI-47 Phase 2.

DevOps owns service liveness. Modeled on the disk custodian: WATCH+DIAGNOSE in
assess() (read-only, [] when healthy), SAFE remediate in remediate_safe(). Reuses
the exact enumeration logic proven in green_baseline.check_container_roster.

Contract (§1):
  - down + expects-up, restarts still nominal          -> AUTO   (docker restart)
  - a CRASH-LOOP (down after many restarts, OR running  -> STRUCTURAL (queue with
    but flapping at a high RestartCount)                    recent logs — restarting
                                                            into a loop is not a fix)

Crash-loop is judged on restart RATE, not lifetime count (KAI, 2026-08-31). A high
`RestartCount` is only a loop while restarts are still happening: a genuinely
flapping container cannot stay continuously up past STABLE_UPTIME_S. So a running
container with a high LIFETIME count that has held for the stability window is
cleared — it recovered, was fixed, or the count is stale from a long-ago blip.
This killed two standing false positives: kai-tailscale (rc=5 from a 10-day-old
network rebind, stable since) and any "recreated + fixed" service (whose count
reset masked, rather than proved, recovery). `StartedAt` is the rate signal and
needs no persistent state. Unknown uptime stays conservative (still structural).

Runs host-side as `leo` (has Docker access); the sandboxed kai-scheduler watchdog
stays a detector and must not be breached (§2.1).
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone

# Compose projects whose services DevOps owns (same set green_baseline gates).
PROJECTS = {"buzz", "kai-system", "plane"}
# A RestartCount at/above this is a candidate flap — but only a live one counts
# (see STABLE_UPTIME_S). A restart won't hold while it is actively looping, so that
# case is STRUCTURAL (investigate the crash), never another auto-restart.
CRASH_LOOP_RC = 5
# A container continuously up at least this long is NOT actively flapping, however
# high its lifetime RestartCount — docker restart backoff means a real loop restarts
# within minutes and can never accumulate this much uptime between restarts.
STABLE_UPTIME_S = 3600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uptime_s(started_at: str):
    """Seconds since the container's last (re)start, parsed from docker's StartedAt
    (RFC3339, nanosecond precision). None when unparseable or never-started — the
    caller treats None as 'unknown' and stays conservative."""
    if not started_at or started_at.startswith("0001-01-01"):
        return None
    s = started_at.strip().replace("Z", "+00:00")
    # docker emits nanoseconds; datetime.fromisoformat only takes microseconds
    m = re.match(r"(.*\.\d{6})\d*(\+\d{2}:\d{2})$", s)
    if m:
        s = m.group(1) + m.group(2)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


# ── enumeration (reuses the proven check_container_roster approach) ─────────────

def _managed_names() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--format",
         '{{.Names}}\t{{.Label "com.docker.compose.project"}}'],
        capture_output=True, text=True, timeout=15).stdout
    return [ln.split("\t")[0] for ln in out.splitlines()
            if len(ln.split("\t")) >= 2 and ln.split("\t")[1] in PROJECTS]


def _inspect(names: list[str]) -> list[tuple]:
    """(name, status, exit_code, restart_policy, restart_count, uptime_s) per container.
    uptime_s is seconds since the last (re)start, or None if unknown."""
    if not names:
        return []
    fmt = ("{{.Name}}|{{.State.Status}}|{{.State.ExitCode}}|"
           "{{.HostConfig.RestartPolicy.Name}}|{{.RestartCount}}|{{.State.StartedAt}}")
    insp = subprocess.run(["docker", "inspect", "-f", fmt, *names],
                          capture_output=True, text=True, timeout=20).stdout
    rows = []
    for line in insp.splitlines():
        parts = line.strip().lstrip("/").split("|")
        if len(parts) != 6:
            continue
        name, status, exit_code, policy, rc, started = parts
        rows.append((name, status, exit_code, policy,
                     int(rc) if rc.isdigit() else 0, _uptime_s(started)))
    return rows


# ── pure classification (unit-tested) ──────────────────────────────────────────

def classify_container(status: str, exit_code: str, policy: str, rc: int, uptime_s=None):
    """Return (disposition|None, reason). None == healthy (no Finding).

    A one-shot that exited 0 (e.g. plane-migrator) is healthy, not down.
    A container actively crash-looping is STRUCTURAL — restarting it again is not a
    remediation. Lifetime RestartCount alone does NOT prove a loop: a running
    container that has held past STABLE_UPTIME_S is cleared however high its count
    (recovered/fixed/stale). uptime_s=None means unknown → stay conservative."""
    expects_up = policy in ("always", "unless-stopped")
    if status == "running":
        if rc >= CRASH_LOOP_RC:
            if uptime_s is not None and uptime_s >= STABLE_UPTIME_S:
                return None, (f"healthy — {rc} lifetime restarts but stable "
                              f"{int(uptime_s // 3600)}h (not looping)")
            return "structural", f"running but flapping (RestartCount={rc}) — restart is not holding"
        return None, "healthy"
    # not running:
    if not expects_up and exit_code == "0":
        return None, "one-shot exited 0"
    if rc >= CRASH_LOOP_RC:
        return "structural", f"down after {rc} restarts — crash-loop; needs investigation, not another restart"
    return "auto", f"down (status={status}, exit={exit_code}) — safe to restart"


class ServicesCustodian:
    domain = "services"

    def assess(self) -> list:
        from devops_ownership import Finding, AUTO, STRUCTURAL
        try:
            rows = _inspect(_managed_names())
        except Exception as e:
            # Docker unreachable is itself a structural signal, not silence.
            return [Finding(
                domain="services", check="roster_unavailable", severity="warn",
                diagnosis=f"could not enumerate containers: {type(e).__name__}: {e}",
                disposition=STRUCTURAL,
                proposed_action="investigate docker daemon / socket access on the host",
                dedup_key="services-roster-unavailable", detail={})]
        findings = []
        for name, status, exit_code, policy, rc, uptime_s in rows:
            disp, reason = classify_container(status, exit_code, policy, rc, uptime_s)
            if disp is None:
                continue
            sev = "crit" if status != "running" else "warn"
            if disp == AUTO:
                findings.append(Finding(
                    domain="services", check="container_down", severity=sev,
                    diagnosis=f"{name}: {reason}",
                    disposition=AUTO,
                    proposed_action=f"restart {name}",
                    dedup_key=f"services-down-{name}",
                    detail={"name": name, "status": status, "exit_code": exit_code, "rc": rc}))
            else:  # structural (crash-loop / flapping)
                findings.append(Finding(
                    domain="services", check="crash_loop", severity="crit",
                    diagnosis=f"{name}: {reason}",
                    disposition=STRUCTURAL,
                    proposed_action=f"investigate {name} crash-loop (see recent logs) and fix the root cause; do not auto-restart into the loop",
                    dedup_key=f"services-crashloop-{name}",
                    detail={"name": name, "status": status, "rc": rc,
                            "uptime_s": round(uptime_s) if uptime_s is not None else None,
                            "recent_logs": _recent_logs(name)}))
        return findings

    def remediate_safe(self, f) -> str:
        name = f.detail.get("name")
        if not name:
            return "no container name in finding — nothing to restart"
        try:
            subprocess.run(["docker", "restart", name], capture_output=True, text=True, timeout=60)
        except Exception as e:
            return f"restart of {name} failed: {type(e).__name__}: {e}"
        # Verify it held.
        rows = _inspect([name])
        status = rows[0][1] if rows else "unknown"
        return f"restarted {name} → status now {status}"


def _recent_logs(name: str, tail: int = 20) -> str:
    try:
        out = subprocess.run(["docker", "logs", "--tail", str(tail), name],
                             capture_output=True, text=True, timeout=15)
        return (out.stdout or out.stderr)[-1500:]
    except Exception:
        return "(logs unavailable)"

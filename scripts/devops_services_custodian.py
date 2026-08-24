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

Runs host-side as `leo` (has Docker access); the sandboxed kai-scheduler watchdog
stays a detector and must not be breached (§2.1).
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone

# Compose projects whose services DevOps owns (same set green_baseline gates).
PROJECTS = {"buzz", "kai-system", "plane"}
# A RestartCount at/above this is flapping — a restart won't hold, so it is a
# STRUCTURAL problem (investigate the crash), never another auto-restart.
CRASH_LOOP_RC = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── enumeration (reuses the proven check_container_roster approach) ─────────────

def _managed_names() -> list[str]:
    out = subprocess.run(
        ["docker", "ps", "-a", "--format",
         '{{.Names}}\t{{.Label "com.docker.compose.project"}}'],
        capture_output=True, text=True, timeout=15).stdout
    return [ln.split("\t")[0] for ln in out.splitlines()
            if len(ln.split("\t")) >= 2 and ln.split("\t")[1] in PROJECTS]


def _inspect(names: list[str]) -> list[tuple[str, str, str, str, int]]:
    """(name, status, exit_code, restart_policy, restart_count) per container."""
    if not names:
        return []
    fmt = ("{{.Name}}|{{.State.Status}}|{{.State.ExitCode}}|"
           "{{.HostConfig.RestartPolicy.Name}}|{{.RestartCount}}")
    insp = subprocess.run(["docker", "inspect", "-f", fmt, *names],
                          capture_output=True, text=True, timeout=20).stdout
    rows = []
    for line in insp.splitlines():
        parts = line.strip().lstrip("/").split("|")
        if len(parts) != 5:
            continue
        name, status, exit_code, policy, rc = parts
        rows.append((name, status, exit_code, policy, int(rc) if rc.isdigit() else 0))
    return rows


# ── pure classification (unit-tested) ──────────────────────────────────────────

def classify_container(status: str, exit_code: str, policy: str, rc: int):
    """Return (disposition|None, reason). None == healthy (no Finding).

    A one-shot that exited 0 (e.g. plane-migrator) is healthy, not down.
    A flapping/crash-looping container is STRUCTURAL — restarting it again is
    not a remediation."""
    expects_up = policy in ("always", "unless-stopped")
    if status == "running":
        if rc >= CRASH_LOOP_RC:
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
        for name, status, exit_code, policy, rc in rows:
            disp, reason = classify_container(status, exit_code, policy, rc)
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

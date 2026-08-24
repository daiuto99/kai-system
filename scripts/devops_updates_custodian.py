#!/usr/bin/env python3
"""Updates / patching custodian — KAI-47 Phase 2.

DevOps owns OS hygiene. Modeled on the disk custodian. Reuses the proven detection
in green_baseline.check_host_hygiene (apt-check security count, reboot-required flag,
zombie count).

Contract (§1):
  - security updates unattended-upgrade CAN apply  -> AUTO (apply, host-root)
  - security updates HELD BACK (deps outside the    -> STRUCTURAL (an attended/broader
    security-only allowlist — unattended refuses        upgrade + likely reboot; do NOT
    them, correctly)                                     fake an auto-fix that changes
                                                         nothing)
  - zombie/defunct processes       -> AUTO   (SIGCHLD their parent — safe, non-disruptive)
  - reboot-required                -> STRUCTURAL (a reboot is disruptive and Leo's call;
                                        reach him through the deduped QUEUE, not a page
                                        every 15 min — no blocking gate for a non-urgent
                                        pending reboot)

run_maintenance's `sudo` path is dead (no sudoers). Host-root is via nsenter into PID 1
namespaces from a privileged container — the no-sudo root path ([[project_worker_root_via_docker]]).
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── host-root exec — nsenter into PID 1 (real host apt, no sudo) ────────────────

def host_root_exec(argv: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Run argv as root in the HOST namespaces (real /proc, real apt, real signals),
    via a privileged container entering PID 1's namespaces. Distinct from the disk
    custodian's file-mount `-v /:/host` ops — this is for genuine host commands."""
    full = ["docker", "run", "--rm", "--privileged", "--pid=host", "-v", "/:/host",
            "alpine", "nsenter", "-t", "1", "-m", "-u", "-n", "-i", "--"] + list(argv)
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


# ── detection (reuses green_baseline's sources) ─────────────────────────────────

def apt_security_count():
    """(total, security) pending via Ubuntu's canonical apt-check; (None, None) on error.
    Cached apt state — no sudo, no network."""
    try:
        r = subprocess.run(["/usr/lib/update-notifier/apt-check"],
                           capture_output=True, text=True, timeout=20)
        raw = (r.stderr or r.stdout).strip()
        if ";" in raw:
            total, security = (int(x) for x in raw.split(";")[:2])
            return total, security
    except Exception:
        pass
    return None, None


def unattended_applicable() -> bool:
    """True iff unattended-upgrade would actually apply at least one package right now.
    Ubuntu's apt-check counts security updates that unattended-upgrade CANNOT apply
    (deps outside the security-only allowlist); those must route to STRUCTURAL, not a
    no-op AUTO. Parsed from the dry-run's own honest verdict line."""
    try:
        r = host_root_exec(["unattended-upgrade", "--dry-run", "-v"], timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception:
        return False
    if "No packages found that can be upgraded unattended" in out:
        return False
    # unattended-upgrade prints "Packages that will be upgraded:" when it has work.
    return "will be upgraded" in out or "Packages that will be upgraded" in out


def reboot_required() -> bool:
    return Path("/var/run/reboot-required").exists()


def zombie_ppids() -> list[int]:
    """Parent PIDs of defunct/zombie children — signalling the parent reaps them."""
    try:
        out = subprocess.run(["ps", "-eo", "stat=,ppid="], capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return []
    ppids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("Z"):
            try:
                ppids.append(int(parts[1]))
            except ValueError:
                pass
    return sorted(set(ppids))


# ── pure classification (unit-tested) ──────────────────────────────────────────

def classify_hygiene(security, applicable: bool, reboot_req: bool,
                     zombie_parents: list[int]) -> list[dict]:
    """Turn raw hygiene signals into finding-specs. Pure → unit-testable.
    `applicable` = unattended-upgrade can actually apply ≥1 of the pending security
    updates now. When security updates remain but none are applicable, they are
    held-back → STRUCTURAL (never a no-op AUTO that fakes a fix).
    Each spec: {check, disposition, severity, diagnosis, proposed_action, dedup_key, detail}."""
    specs = []
    if security is not None and security > 0:
        if applicable:
            specs.append({
                "check": "security_updates", "disposition": "auto", "severity": "warn",
                "diagnosis": f"{security} pending security update(s), applicable unattended",
                "proposed_action": "apply security updates via unattended-upgrade (host-root)",
                "dedup_key": "updates-security", "detail": {"security": security}})
        else:
            specs.append({
                "check": "security_updates_held", "disposition": "structural", "severity": "warn",
                "diagnosis": (f"{security} pending security update(s) held back — unattended-upgrade "
                              "cannot apply them (dependencies live outside the security-only "
                              "allowlist, e.g. jammy-updates / Docker-CE)"),
                "proposed_action": ("perform an attended/broader `apt upgrade` (pulls the held deps, "
                                    "may restart services and stage a reboot) — a decision, not an "
                                    "unattended auto-apply"),
                "dedup_key": "updates-security-held", "detail": {"security": security}})
    if zombie_parents:
        specs.append({
            "check": "zombies", "disposition": "auto", "severity": "warn",
            "diagnosis": f"{len(zombie_parents)} zombie/defunct child proc(s) under parent(s) {zombie_parents}",
            "proposed_action": "SIGCHLD the parents to reap defunct children",
            "dedup_key": "updates-zombies", "detail": {"ppids": zombie_parents}})
    if reboot_req:
        specs.append({
            "check": "reboot_required", "disposition": "structural", "severity": "warn",
            "diagnosis": "kernel/library updates staged a reboot (/var/run/reboot-required present)",
            "proposed_action": "schedule a host reboot to apply staged kernel/library updates (disruptive — Leo's call, queued not paged)",
            "dedup_key": "updates-reboot-required", "detail": {}})
    return specs


class UpdatesCustodian:
    domain = "updates"

    def assess(self) -> list:
        from devops_ownership import Finding
        _total, security = apt_security_count()
        # Only pay the dry-run cost when there is actually a security count to classify.
        applicable = unattended_applicable() if (security or 0) > 0 else False
        specs = classify_hygiene(security, applicable, reboot_required(), zombie_ppids())
        return [Finding(domain="updates", **s) for s in specs]

    def remediate_safe(self, f) -> str:
        if f.check == "security_updates":
            try:
                r = host_root_exec(["unattended-upgrade"], timeout=1200)
                tail = (r.stdout or r.stderr).strip().splitlines()[-3:]
                _t, sec_after = apt_security_count()
                return f"unattended-upgrade exit={r.returncode}; security pending now {sec_after}; {' | '.join(tail)}"
            except Exception as e:
                return f"security apply failed: {type(e).__name__}: {e}"
        if f.check == "zombies":
            reaped = []
            for ppid in f.detail.get("ppids", []):
                try:
                    host_root_exec(["kill", "-CHLD", str(ppid)], timeout=30)
                    reaped.append(ppid)
                except Exception:
                    pass
            after = zombie_ppids()
            return f"sent SIGCHLD to parents {reaped}; zombie parents remaining: {after}"
        return f"no safe remediation for updates/{f.check}"

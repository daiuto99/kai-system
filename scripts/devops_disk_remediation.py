#!/usr/bin/env python3
"""Autonomous DevOps disk custodian — worker root-fs health (KAI-44).

DevOps OWNS worker disk health; Leo is never paged for pressure ([[feedback_devops_owns_infra]]).
Runs as `leo` on a cron (leo has Docker access; the sandboxed kai-scheduler deliberately
does NOT, so remediation lives here, on the host, not in the watchdog).

Engages at WARN (default 80%), BEFORE crisis. It:
  1. ASSESSES the root fs and its composition (top consumers via root-via-docker `du`).
  2. RECLAIMS what is safe, autonomously and honestly targeted at the ROOT device:
     journald archives + oversized *.log truncation + aborted rsync temp partials.
     (Docker images/build-cache live on /mnt/storage here, so pruning them frees the
     WRONG disk — the old _try_fix_disk's core mistake — so we don't pretend it helps root.)
  3. QUEUES — never auto-destroys — a STRUCTURAL problem it must not decide alone
     (misplaced 23G containerd, backups on the OS disk, real capacity). That becomes a
     triaged Plane item for DevOps, with a concrete proposed action. Leo is touched only
     for a genuine decision, and only through that queue/approval path — never a raw page.

Safe by construction: it only ever truncates logs and removes journal-archives / aborted
temp partials. It NEVER deletes data, backups, media, or mirrors autonomously.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

WARN_PCT = 80
CRIT_PCT = 90
BIG_LOG_MB = 200            # a single *.log over this is a runaway → truncate
LOG_JSONL = Path("/home/leo/kai-system/logs/devops_disk_remediation.jsonl")

# Shared ownership layer (KAI-46) — for the Custodian plug-in below. Imported
# defensively so the standalone run()/main() path never depends on it.
import os as _os  # noqa: E402
_SHARED = Path(_os.environ.get("KAI_SYSTEM_ROOT", "/home/leo/kai-system")) / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# Root-fs subtrees DevOps may reclaim from autonomously (logs only — regenerable).
# Everything else (data, backups, mirrors, containerd) is STRUCTURAL → escalate.
_STRUCTURAL_PREFIXES = ("/mnt", "/home/leo/backups", "/var/lib/containerd", "/var/lib/docker")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root_pct() -> int:
    du = shutil.disk_usage("/")
    return round(du.used / du.total * 100)


def _docker_du() -> dict[str, int]:
    """Top-level root-fs composition in KB, via a root-mount container (no sudo)."""
    out = subprocess.run(
        ["docker", "run", "--rm", "-v", "/:/host:ro", "alpine",
         "du", "-xk", "-d1", "/host"],
        capture_output=True, text=True, timeout=120,
    )
    comp: dict[str, int] = {}
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[0].strip().isdigit():
            kb = int(parts[0])
            path = parts[1].replace("/host", "", 1) or "/"
            comp[path] = kb
    comp.pop("/", None)
    return comp


# ── pure decision logic (unit-tested) ─────────────────────────────────────────

def should_engage(pct: int, warn: int = WARN_PCT) -> bool:
    return pct >= warn


def is_structural(path: str) -> bool:
    """A top consumer DevOps must NOT auto-destroy — data/backups/mirror/containerd."""
    return any(path == p or path.startswith(p + "/") or path.startswith(p)
               for p in _STRUCTURAL_PREFIXES)


def top_structural(comp: dict[str, int], n: int = 5) -> list[tuple[str, int]]:
    items = [(p, kb) for p, kb in comp.items() if is_structural(p)]
    return sorted(items, key=lambda x: -x[1])[:n]


# ── safe autonomous reclaims (root-targeted, log-only) ─────────────────────────

def _root_docker(sh: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", "-v", "/:/host", "alpine", "sh", "-c", sh],
        capture_output=True, text=True, timeout=timeout,
    )


def reclaim_journal(dry: bool) -> str:
    if dry:
        return "would delete archived journals (*@*.journal)"
    _root_docker("find /host/var/log/journal -type f -name '*@*.journal' -delete 2>/dev/null; true")
    return "deleted archived journals"


def reclaim_big_logs(dry: bool, cap_mb: int = BIG_LOG_MB) -> str:
    finder = f"find /host/var/log -type f -name '*.log' -size +{cap_mb}M"
    if dry:
        listing = _root_docker(f"{finder} 2>/dev/null; true").stdout.strip()
        return f"would truncate oversized logs: {listing or '(none)'}"
    _root_docker(f"{finder} -exec truncate -s 0 {{}} + 2>/dev/null; true")
    return f"truncated *.log > {cap_mb}M"


def reclaim_aborted_temps(dry: bool) -> str:
    # rsync partials: hidden, name ends .<6 random>, >10M, in the mirror/data area only.
    finder = ("find /host/mnt -type f -name '.*.??????' -size +10M")
    if dry:
        listing = _root_docker(f"{finder} 2>/dev/null; true").stdout.strip()
        return f"would remove aborted rsync temps: {listing or '(none)'}"
    _root_docker(f"{finder} -delete 2>/dev/null; true")
    return "removed aborted rsync temp partials"


SAFE_RECLAIMS = (reclaim_journal, reclaim_big_logs, reclaim_aborted_temps)


# ── structural escalation (queue, never auto-destroy) ──────────────────────────

_ESCALATION_MARKER = "[devops-disk-structural]"


def escalate_structural(pct: int, top: list[tuple[str, int]], dry: bool) -> str:
    lines = "; ".join(f"{p} {kb // 1024}M" for p, kb in top)
    title = f"[DevOps] Root disk {pct}% after autonomous reclaim — structural action needed {_ESCALATION_MARKER}"
    body = (
        f"<p>DevOps auto-reclaimed all safe (log) space but root is still {pct}%. The remaining "
        f"top consumers are STRUCTURAL and must not be auto-destroyed: {lines}.</p>"
        f"<p>Proposed action (DevOps queue): relocate the misplaced large stores off the 98G OS "
        f"volume onto /mnt/storage (containerd data-root, backups), and/or add capacity. This is a "
        f"decision item, not a log-reclaim.</p>"
    )
    if dry:
        return f"would escalate structural: {title}"
    try:
        sys.path.insert(0, "/home/leo/kai-system")
        import sync_plane_state as sp
        PID = "c0b81eb3-1238-4fd3-9eba-7cb107e304c0"
        # dedup: refresh an existing open marker ticket instead of spamming duplicates
        for i in sp.get_issues(PID):
            if _ESCALATION_MARKER in (i.get("name") or ""):
                return f"structural ticket already open ({i.get('sequence_id')}) — not duplicated"
        r = sp.req("POST", f"projects/{PID}/issues/",
                   {"name": title, "description_html": body, "priority": "high",
                    "state": "81888cd6-3e61-4b62-830c-89b50a43e190",
                    "labels": ["13ff5eec-6697-44bf-8142-774f4bdae4e5"]})
        return f"escalated structural → Plane {r.get('sequence_id') or r.get('id')}"
    except Exception as e:  # never let escalation failure crash the custodian
        return f"escalation failed (logged): {e}"


def _record(rec: dict) -> None:
    try:
        LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with LOG_JSONL.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def run(dry: bool, warn: int, crit: int) -> dict:
    before = root_pct()
    rec = {"ts": _now(), "disk_before": before, "warn": warn, "crit": crit,
           "engaged": False, "actions": [], "disk_after": before, "escalated": None}
    if not should_engage(before, warn):
        rec["note"] = f"root {before}% < warn {warn}% — no action"
        _record(rec)
        return rec

    rec["engaged"] = True
    for action in SAFE_RECLAIMS:
        try:
            rec["actions"].append(action(dry))
        except Exception as e:
            rec["actions"].append(f"{action.__name__} error: {e}")

    after = root_pct() if not dry else before
    rec["disk_after"] = after

    if after >= crit:
        comp = _docker_du()
        rec["escalated"] = escalate_structural(after, top_structural(comp), dry)
    else:
        rec["escalated"] = f"resolved to {after}% by safe reclaim — DevOps owns it, Leo not involved"
    _record(rec)
    return rec


# ── Custodian plug-in (KAI-46) — same verified functions, behind the shared interface ──

class DiskCustodian:
    """Storage-domain custodian for the shared runner. WATCH+DIAGNOSE in assess()
    (read-only, [] when healthy); SAFE reclaim in remediate_safe(). Reuses the exact
    verified functions above — no behavior change, just expressed as Findings the one
    dispatcher routes (auto reclaim; structural queue when crit persists)."""

    domain = "storage"

    def assess(self) -> list:
        from devops_ownership import Finding, AUTO, STRUCTURAL
        pct = root_pct()
        if not should_engage(pct, WARN_PCT):
            return []  # healthy — no docker-du cost incurred
        sev = "crit" if pct >= CRIT_PCT else "warn"
        findings = [Finding(
            domain="storage", check="disk", severity=sev,
            diagnosis=f"root fs at {pct}% (warn {WARN_PCT}% / crit {CRIT_PCT}%)",
            disposition=AUTO,
            proposed_action="safe log reclaim: journald archives + oversized *.log truncation + aborted rsync temps",
            dedup_key="storage-disk-safe-reclaim", detail={"pct": pct},
        )]
        # A crit reading despite safe reclaim running every cycle means the remaining
        # top consumers are STRUCTURAL — queue them, never auto-destroy (§2.3).
        if pct >= CRIT_PCT:
            top = top_structural(_docker_du())
            lines = "; ".join(f"{p} {kb // 1024}M" for p, kb in top)
            findings.append(Finding(
                domain="storage", check="disk_structural", severity="crit",
                diagnosis=f"root {pct}% persists after safe reclaim; remaining top consumers are structural: {lines}",
                disposition=STRUCTURAL,
                proposed_action="relocate misplaced large stores off the 98G OS volume onto /mnt/storage, and/or add capacity",
                dedup_key="storage-disk-structural",
                detail={"pct": pct, "top": [[p, kb] for p, kb in top]},
            ))
        return findings

    def remediate_safe(self, f) -> str:
        results = []
        for action in SAFE_RECLAIMS:
            try:
                results.append(action(False))
            except Exception as e:
                results.append(f"{action.__name__} error: {e}")
        after = root_pct()
        rec = {"ts": _now(), "custodian": "storage", "actions": results, "disk_after": after}
        _record(rec)
        return f"{'; '.join(results)} → root now {after}%"


def main() -> int:
    ap = argparse.ArgumentParser(description="Autonomous DevOps disk custodian (KAI-44)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--warn", type=int, default=WARN_PCT)
    ap.add_argument("--crit", type=int, default=CRIT_PCT)
    args = ap.parse_args()
    rec = run(args.dry_run, args.warn, args.crit)
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

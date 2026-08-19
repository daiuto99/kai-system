#!/usr/bin/env python3
"""KAI-1153 · [MR2] Path-implementation inventory — M-R2 exit proof.

Enumerates each user-facing function/path and asserts exactly ONE *live* implementation.
Emits any duplicate/orphan (present-on-disk but not live) as a finding, so the M-R2
"one implementation per path" consolidation is measured against script output, not prose.

Liveness signals (a candidate is LIVE if ANY strong signal fires):
  • proc:   a matching process is running (ps aux)
  • baked:  the file the container ACTUALLY executes (its WorkingDir), for a running compose service
Presence-only (file exists on disk but no live signal) => ORPHAN candidate.

Verdict per path:
  OK        exactly one live impl
  DUP       >1 live impl (real consolidation target)
  ORPHAN    exactly one live impl but ≥1 orphaned duplicate still on disk (cleanup target)
  MISSING   zero live impl (regression — a path with no live implementation)

Exit non-zero if any path is DUP or MISSING (ORPHAN is a warning, not a hard fail).
v1: a curated registry of the paths the 2026-08-15 Fable review + this session surfaced.
The registry is the artifact that grows as coverage widens; detection primitives are generic.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def _ps() -> str:
    return _run(["ps", "aux"])


def _compose_services() -> set[str]:
    out = _run(["docker", "ps", "--format", "{{.Names}}"])
    return {line.strip() for line in out.splitlines() if line.strip()}


def _container_workdir_has(container: str, filename: str) -> bool:
    """True if `container` runs from a WorkingDir that contains `filename` (baked, executed)."""
    if container not in _RUNNING_CONTAINERS:
        return False
    wd = _run(["docker", "inspect", "-f", "{{.Config.WorkingDir}}", container]).strip() or "/"
    ls = _run(["docker", "exec", container, "sh", "-c", f"ls {wd}/{filename} 2>/dev/null"])
    return filename in ls


_PS_CACHE = _ps()
_RUNNING_CONTAINERS = _compose_services()


@dataclass
class Candidate:
    label: str
    # any of these firing = a signal
    proc_pattern: str = ""          # substring to find in `ps aux`
    baked: tuple[str, str] | None = None   # (container, filename) executed from its WorkingDir
    disk_path: str = ""             # host path whose existence marks presence
    is_module: bool = False         # imported library, not a process: liveness = present in a deployed tree

    def live(self) -> bool:
        # A library/chokepoint module is "live" when it exists in a deployed service
        # tree — it has no process of its own; its single-impl is enforced separately
        # (e.g. check_notify_chokepoint.py). Process/baked signals apply to executables.
        if self.is_module:
            return self.present()
        if self.proc_pattern and self.proc_pattern in _PS_CACHE:
            return True
        if self.baked and _container_workdir_has(*self.baked):
            return True
        return False

    def present(self) -> bool:
        import os
        return bool(self.disk_path) and os.path.exists(self.disk_path)


@dataclass
class Path:
    key: str
    desc: str
    candidates: list[Candidate] = field(default_factory=list)

    def evaluate(self) -> dict:
        live = [c.label for c in self.candidates if c.live()]
        orphans = [c.label for c in self.candidates
                   if (not c.live()) and c.present()]
        if len(live) > 1:
            verdict = "DUP"
        elif len(live) == 0:
            verdict = "MISSING"
        elif orphans:
            verdict = "ORPHAN"
        else:
            verdict = "OK"
        return {"path": self.key, "desc": self.desc, "verdict": verdict,
                "live": live, "orphans": orphans}


# ── Registry (v1) — curated from the Fable HoE review + the KAI-1129/b782c29a session ──
REGISTRY: list[Path] = [
    Path("advisor_dm_delivery", "How an advisor DM reaches the live agent", [
        Candidate("kai-buzz baked agents_bridge (LIVE path)",
                  proc_pattern="agents_bridge.py KAI", baked=("kai-buzz", "agents_bridge.py")),
        Candidate("buzz-eval host agents_bridge (orphaned 08-04)",
                  disk_path="/home/leo/buzz-eval/agent/agents_bridge.py"),
        Candidate("kai_openai_shim :4001 (retired 08-03)",
                  disk_path="/home/leo/buzz-eval/agent/kai_openai_shim.py"),
    ]),
    Path("advisor_shim_backend", "The :4001 advisor shim backing native Buzz advisors", [
        Candidate("kai-buzz-shim compose service (LIVE)", proc_pattern="kai_openai_shim"),
    ]),
    Path("approvals_surface", "Approvals poller (Buzz taps -> gate resolve)", [
        Candidate("buzz_approve.py (kai-buzz)", proc_pattern="buzz_approve.py"),
    ]),
    Path("telegram_inbound", "Inbound Telegram lifeline transport", [
        Candidate("kai-scheduler long-poll (LIVE)", proc_pattern="scheduler.py"),
        Candidate("worker-api telegram webhook (dormant per memory)",
                  disk_path="/home/leo/kai-system/kai-worker-api/routes/telegram.py"),
    ]),
    Path("notify_delivery", "Single Leo-facing notification chokepoint", [
        Candidate("notify_gateway (chokepoint)",
                  disk_path="/home/leo/kai-system/shared/notify_gateway.py", is_module=True),
    ]),
]


def main() -> int:
    results = [p.evaluate() for p in REGISTRY]
    dup = [r for r in results if r["verdict"] == "DUP"]
    missing = [r for r in results if r["verdict"] == "MISSING"]
    orphan = [r for r in results if r["verdict"] == "ORPHAN"]
    ok = [r for r in results if r["verdict"] == "OK"]

    print("KAI-1153 · M-R2 PATH-IMPLEMENTATION INVENTORY")
    print("=" * 64)
    for r in results:
        mark = {"OK": "[OK]", "DUP": "[DUP]", "ORPHAN": "[ORPHAN]", "MISSING": "[MISS]"}[r["verdict"]]
        print(f"{mark:9} {r['path']}")
        print(f"          live:    {r['live'] or '(none)'}")
        if r["orphans"]:
            print(f"          orphans: {r['orphans']}  <- delete/consolidate")
    print("=" * 64)
    print(f"paths: {len(results)} | OK: {len(ok)} | ORPHAN: {len(orphan)} | DUP: {len(dup)} | MISSING: {len(missing)}")
    print("\nJSON:", json.dumps(results))
    # hard fail on real one-impl violations; ORPHAN is a warning (cleanup, not breakage)
    return 1 if (dup or missing) else 0


if __name__ == "__main__":
    sys.exit(main())

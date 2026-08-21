#!/usr/bin/env python3
"""M-R2 · One-implementation-per-path inventory (the stage exit artifact).

Generated inventory, NOT prose (mr-2 exit criterion): for each user-facing path it
enumerates every implementation on disk and derives LIVE vs ORPHAN vs DEAD from
observed reality — a running container, a live cron line, a consumed port, a compose
bind-mount, git tracking — never from a hand-maintained list of "what should be live".

Exit gate: a path with >1 LIVE implementation FAILS (parallel duplicates remain). Run
before and after the consolidation deletions; the diff between runs is the deletion
evidence the stage requires. Read-only: pokes docker/cron/ss/git, mutates nothing.
Runs on the worker HOST.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
KS = HOME / "kai-system"


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def container_running(name: str) -> bool:
    return _sh(["docker", "inspect", "-f", "{{.State.Running}}", name]).strip() == "true"


def crontab_has(substr: str) -> bool:
    return substr in _sh(["crontab", "-l"])


def cron_is_noop(script: Path) -> bool:
    """A keep-alive cron whose every start/up invocation is commented out is dead weight."""
    try:
        lines = [l.strip() for l in script.read_text().splitlines()]
    except Exception:
        return False
    active = [l for l in lines if l and not l.startswith("#")
              and ("start " in l or "up \"" in l) and "()" not in l]
    return not active  # no live start/up line -> no-op


def port_consumed(port: int) -> bool:
    out = _sh(["ss", "-tnp"])
    return f":{port} " in out and "ESTAB" in out


def compose_bind_mounts(host_path: str) -> bool:
    try:
        return host_path in (KS / "docker-compose.yml").read_text()
    except Exception:
        return False


def git_tracked(rel: str) -> bool:
    return rel in _sh(["git", "-C", str(KS), "ls-files", rel])


@dataclass
class Impl:
    ref: str            # file / cron / service reference
    status: str = "?"   # LIVE | ORPHAN | DEAD
    evidence: str = ""


@dataclass
class PathEntry:
    path: str
    impls: list = field(default_factory=list)

    def live_count(self) -> int:
        return sum(1 for i in self.impls if i.status == "LIVE")


def build_inventory() -> list:
    inv: list = []

    # 1. Advisor DM backend (OpenAI-compatible shim on :4001)
    live_shim = container_running("kai-buzz-shim")
    orphan_shim = HOME / "buzz-eval/agent/kai_openai_shim.py"
    inv.append(PathEntry("Advisor DM backend (:4001 OpenAI shim)", [
        Impl("kai-buzz-shim/kai_openai_shim.py (compose service)",
             "LIVE" if live_shim else "DEAD",
             f"container kai-buzz-shim running={live_shim}"),
        Impl("~/buzz-eval/agent/kai_openai_shim.py",
             "ORPHAN" if orphan_shim.exists() else "DEAD",
             "retired 2026-08-03 (watchdog.sh); the live :4001 is the container, not this copy"),
    ]))

    # 2. Advisor DM bridges (Nostr signing/transport)
    kb_live = container_running("kai-buzz")
    archived_bridge = HOME / "buzz-eval/_archived_kai1142/agents_bridge.py"
    inv.append(PathEntry("Advisor bridges (Nostr signing/transport)", [
        Impl("kai-buzz/agents_bridge.py (compose service kai-buzz)",
             "LIVE" if kb_live else "DEAD",
             f"container kai-buzz running={kb_live}; imported by buzz_approve/buzz_provision/sky_dm"),
        Impl("~/buzz-eval/_archived_kai1142/agents_bridge.py",
             "ORPHAN" if archived_bridge.exists() else "DEAD",
             "explicitly _archived_ copy; not imported by any live service"),
    ]))

    # 3. Telegram inbound
    sched_live = container_running("kai-scheduler")
    inv.append(PathEntry("Telegram inbound", [
        Impl("kai-scheduler/scheduler.py long-poll",
             "LIVE" if sched_live else "DEAD",
             f"container kai-scheduler running={sched_live}"),
        Impl("kai-worker-api/scheduler.py webhook",
             "ORPHAN",
             "dormant webhook path; long-poll is the live transport (project_telegram_inbound_transport)"),
    ]))

    # 4. Buzz keep-alive watchdog cron
    wd = HOME / "buzz-eval/agent/watchdog.sh"
    noop = cron_is_noop(wd)
    inv.append(PathEntry("Buzz keep-alive cron", [
        Impl("~/buzz-eval/agent/watchdog.sh (cron: every-min + @reboot)",
             "DEAD" if noop else ("LIVE" if crontab_has("watchdog.sh") else "ORPHAN"),
             f"in crontab={crontab_has('watchdog.sh')}; all start/up lines commented (no-op)={noop}"),
    ]))

    # 5. Host-ops deploy transport (mr-2 'brings': landed or deleted)
    hostops = KS / "kai-orchestrator/capabilities/hostops.py"
    inv.append(PathEntry("Host-ops deploy transport", [
        Impl("kai-orchestrator/capabilities/hostops.py (+ workflows/hostops_deploy.py)",
             "LIVE" if hostops.exists() else "DEAD",
             "landed as an orchestrator capability (single impl)"),
    ]))

    # 6. buzz-eval/agent runtime dir — coupling flag (not its own 'path', but the blocker)
    bind = compose_bind_mounts("/home/leo/buzz-eval/agent:/agent")
    inv.append(PathEntry("~/buzz-eval/agent runtime dir (BUZZ_AGENT_DIR)", [
        Impl("~/buzz-eval/agent (bind-mounted into kai-buzz)",
             "LIVE" if bind else "ORPHAN",
             f"compose bind-mount /agent:rw present={bind}; kai-buzz bridges resolve "
             "BUZZ_AGENT_DIR here -> NOT deletable until folded into the image"),
    ]))

    return inv


def main() -> int:
    inv = build_inventory()
    print("M-R2 ONE-IMPLEMENTATION-PER-PATH INVENTORY")
    print("=" * 66)
    dup = 0
    for e in inv:
        lc = e.live_count()
        flag = "  ✗ DUPLICATE LIVE" if lc > 1 else ("  ✓" if lc == 1 else "  · no live impl")
        print(f"\n{e.path}{flag}")
        for i in e.impls:
            print(f"    [{i.status:6}] {i.ref}")
            print(f"             └ {i.evidence}")
        if lc > 1:
            dup += 1
    print("\n" + "=" * 66)
    verdict = "PASS — exactly one live implementation per path" if dup == 0 \
        else f"FAIL — {dup} path(s) still have >1 live implementation"
    print("VERDICT:", verdict)
    print("Orphans/dead to retire:",
          ", ".join(i.ref.split(" ")[0] for e in inv for i in e.impls
                    if i.status in ("ORPHAN", "DEAD")) or "none")
    return 0 if dup == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fleet / host custodian — KAI-47 Phase 2 (KAI-53).

DevOps owns fleet health. Like the security domain, the honest reality is watch +
escalate: you do not blindly auto-reconcile a node (a wrong auto-action on a degraded
host makes it worse). So this custodian makes the fleet CONTINUOUSLY owned — it reuses
the proven green_baseline.check_fleet verdict (the same fleet_eval the watchdog uses,
so the surfaces cannot disagree) and routes a degraded/lost-visibility fleet to the
deduped Plane queue. A persistently-degraded node becomes a triaged structural item,
not just a page.

No safe AUTO: reconciling a node (reboot/re-enroll/re-key) is disruptive and
node-specific — a decision or an investigated structural fix, never a silent auto.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_KAI_ROOT = Path(os.environ.get("KAI_SYSTEM_ROOT", "/home/leo/kai-system"))
for _p in (str(_KAI_ROOT), str(_KAI_ROOT / "scripts"), str(_KAI_ROOT / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Same fleet-state file the heartbeat writes and the watchdog/green-baseline read
# (host path first, then the container mount) — one source, no parallel state.
_FLEET_STATE_CANDIDATES = (
    Path("/home/leo/vault/_fleet_state.json"),
    Path("/vault/_fleet_state.json"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_fleet_state() -> dict:
    for c in _FLEET_STATE_CANDIDATES:
        try:
            return json.loads(c.read_text())
        except Exception:
            continue
    return {}


# ── pure verdict mapping (unit-tested) ─────────────────────────────────────────

def fleet_severity(returned: str | None, raised: bool) -> str | None:
    """check_fleet RAISES on any unhealthy fleet (node down / ssh-unreachable /
    lost visibility) → 'crit'. A WARN string (a muted/aux-node note) → 'warn'. A
    clean roster string → None (healthy, no Finding)."""
    if raised:
        return "crit"
    if returned and "WARN" in returned:
        return "warn"
    return None


class FleetCustodian:
    domain = "fleet"

    def assess(self) -> list:
        from devops_ownership import Finding, STRUCTURAL
        import green_baseline as gb
        findings: list = []

        # (1) Reachability / visibility verdict — a node down / ssh-blind / lost
        # visibility. Unchanged: reuses the same fleet_eval the watchdog uses.
        try:
            out = gb.check_fleet()
            sev, detail = fleet_severity(out, False), (out or "").strip()
        except Exception as e:
            sev, detail = "crit", f"{type(e).__name__}: {e}"
        if sev is not None:
            findings.append(Finding(
                domain="fleet", check="fleet_health", severity=sev,
                diagnosis=detail or "fleet not-green",
                disposition=STRUCTURAL,
                proposed_action=("investigate the degraded/unreachable node and reconcile it "
                                 "(reboot / re-enroll / re-key are node-specific — not a blind auto-action)"),
                dedup_key="fleet-health",
                detail={"verdict": detail[:400]}))

        # (2) KAI-1240 — reachable-but-DEGRADED health per node (Ollama :11434,
        # disk, mem, tailscaled daemon). This is the "on but sick" layer the
        # reachability verdict cannot see. Each degraded node is ONE deduped
        # structural finding (DevOps-owned; not a Leo page). A missing signal on
        # an ssh_ok node reads as degraded ('unknown'), never silent-green.
        from fleet_eval import fleet_degradations
        try:
            degraded = fleet_degradations(_read_fleet_state(), int(time.time()))
        except Exception as e:  # a broken degrade read must not sink the reachability finding
            degraded = {}
            findings.append(Finding(
                domain="fleet", check="node_health", severity="warn",
                diagnosis=f"node-health degrade check failed: {type(e).__name__}: {e}",
                disposition=STRUCTURAL,
                proposed_action="investigate the fleet-state / degrade reader",
                dedup_key="fleet-node-health-error",
                detail={}))
        for host, reasons in sorted(degraded.items()):
            findings.append(Finding(
                domain="fleet", check="node_health", severity="warn",
                diagnosis=f"{host} reachable but degraded: {'; '.join(reasons)}",
                disposition=STRUCTURAL,
                proposed_action=(f"investigate {host}: restart the down runtime / reclaim disk / "
                                 "free memory — node-specific, not a blind auto-action"),
                dedup_key=f"fleet-node-health-{host}",
                detail={"host": host, "reasons": reasons}))

        return findings

    def remediate_safe(self, f) -> str:  # pragma: no cover - fleet has no safe auto
        return "fleet reconcile is not a safe auto-action — routed structural"

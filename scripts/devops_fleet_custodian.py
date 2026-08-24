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

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_KAI_ROOT = Path(os.environ.get("KAI_SYSTEM_ROOT", "/home/leo/kai-system"))
for _p in (str(_KAI_ROOT), str(_KAI_ROOT / "scripts"), str(_KAI_ROOT / "shared")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        try:
            out = gb.check_fleet()
            sev, detail = fleet_severity(out, False), (out or "").strip()
        except Exception as e:
            sev, detail = "crit", f"{type(e).__name__}: {e}"
        if sev is None:
            return []  # fleet healthy
        return [Finding(
            domain="fleet", check="fleet_health", severity=sev,
            diagnosis=detail or "fleet not-green",
            disposition=STRUCTURAL,
            proposed_action=("investigate the degraded/unreachable node and reconcile it "
                             "(reboot / re-enroll / re-key are node-specific — not a blind auto-action)"),
            dedup_key="fleet-health",
            detail={"verdict": detail[:400]})]

    def remediate_safe(self, f) -> str:  # pragma: no cover - fleet has no safe auto
        return "fleet reconcile is not a safe auto-action — routed structural"

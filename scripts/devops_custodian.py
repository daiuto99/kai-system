#!/usr/bin/env python3
"""DevOps custodian runner — the single host-side sweep (KAI-46, Phase 1).

Loads every domain custodian, calls assess(), and routes each Finding through the
ONE dispatcher (auto | structural | decision — shared/devops_ownership.py). Then
meta-monitors the roster: a custodian that stopped running is itself a Finding.

Runs host-side as `leo` on a cron (leo has Docker access; the kai-scheduler
watchdog is deliberately sandboxed and must not be breached — §2.1). This runner
REPLACES the standalone devops_disk_remediation cron: the disk custodian is now a
plug-in behind the shared interface, so every domain added later inherits ownership.

Modes:
  (default)        healthy sweep — assess all custodians, dispatch findings, live deps.
  --dry-run        assess + classify + PRINT the routing plan; take NO action (no notify,
                   no Plane write, no gate). For a safe healthy-path check.
  --demo           forced-engage a synthetic custodian yielding one auto + one structural +
                   one decision finding, routed through the REAL dispatcher with RECORDING
                   deps — proves all three paths end-to-end with zero side effects.
  --demo-live-gate like --demo but the decision finding raises a REAL approval gate to Leo
                   (short timeout). Use only for an explicit tap-test.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_KAI_ROOT = Path(os.environ.get("KAI_SYSTEM_ROOT", "/home/leo/kai-system"))
for p in (str(_KAI_ROOT), str(_KAI_ROOT / "shared"), str(_KAI_ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from devops_ownership import (  # noqa: E402
    AUTO, DECISION, STRUCTURAL, Deps, DecisionOutcome, Finding,
    default_deps, run_custodians,
)


# ── Custodian registry — every domain plugs in here ────────────────────────────

# Domain roster: (module, class). A broken plug-in is skipped with a WARN — it must
# never sink the whole sweep (and the meta-monitor will flag a domain that vanishes).
_ROSTER = [
    ("devops_disk_remediation", "DiskCustodian"),        # storage (Phase 1)
    ("devops_updates_custodian", "UpdatesCustodian"),    # updates/patching (Phase 2)
    ("devops_backups_custodian", "BackupsCustodian"),    # backups (Phase 2)
    ("devops_services_custodian", "ServicesCustodian"),  # services/containers (Phase 2)
    ("devops_security_custodian", "SecurityCustodian"),  # security surface (Phase 2, KAI-52)
    ("devops_fleet_custodian", "FleetCustodian"),        # fleet/host (Phase 2, KAI-53)
    ("devops_currency_custodian", "CurrencyCustodian"),  # dependency + WP currency (CUR-4)
]


def load_custodians() -> list:
    """The live roster. Phase 1 shipped storage (disk); Phase 2 appends
    updates/backups/services. Security + fleet land next on this sprint."""
    custodians = []
    for mod, cls in _ROSTER:
        try:
            m = __import__(mod)
            custodians.append(getattr(m, cls)())
        except Exception as e:  # a broken plug-in must not sink the whole sweep
            print(f"[WARN] could not load {cls}: {type(e).__name__}: {e}", file=sys.stderr)
    return custodians


def emergency_reclaimer():
    """The pre-exhaustion guard's emergency reclaim (§Phase 3): the disk custodian's
    verified log-only SAFE_RECLAIMS, run immediately when root disk enters the reserve
    band — so the runner can always still write its own state. Returns None if the disk
    custodian can't be imported (guard then only flags the pre-empt)."""
    try:
        import devops_disk_remediation as disk

        def _reclaim() -> str:
            done = []
            for action in disk.SAFE_RECLAIMS:
                try:
                    done.append(action(False))  # dry=False → real reclaim
                except Exception as e:
                    done.append(f"{action.__name__} error: {type(e).__name__}: {e}")
            return "; ".join(done)

        return _reclaim
    except Exception as e:
        print(f"[WARN] emergency reclaimer unavailable: {type(e).__name__}: {e}", file=sys.stderr)
        return None


# ── Recording deps — a faithful, side-effect-free view of where each Finding routes ──

def recording_deps() -> "Deps":
    calls = {"dashboard": [], "structural": [], "decision": []}

    def notify_dashboard(f: "Finding", result: str) -> None:
        calls["dashboard"].append({"finding": f.to_dict(), "result": result})

    def file_structural(f: "Finding") -> str:
        calls["structural"].append(f.to_dict())
        return f"[recorded] would queue structural Plane item for {f.domain}/{f.check} (dedup {f.dedup_key})"

    def request_decision(f: "Finding") -> "DecisionOutcome":
        calls["decision"].append(f.to_dict())
        # Recorded run: simulate an APPROVE so the approved→execute branch is exercised too.
        return DecisionOutcome(approved=True, resolved=True, notes="[recorded] simulated approve")

    deps = Deps(notify_dashboard=notify_dashboard, file_structural=file_structural,
                request_decision=request_decision)
    deps._calls = calls  # type: ignore[attr-defined]
    return deps


# ── Demo custodian — forced-engage, one Finding per disposition ────────────────

class _DemoCustodian:
    domain = "demo"

    def assess(self) -> list:
        return [
            Finding(domain="demo", check="auto_path", severity="warn",
                    diagnosis="synthetic: a regenerable artifact exceeded its cap",
                    disposition=AUTO, proposed_action="truncate the regenerable artifact",
                    dedup_key="demo-auto", detail={"synthetic": True}),
            Finding(domain="demo", check="structural_path", severity="crit",
                    diagnosis="synthetic: a large store is misplaced and must not be auto-destroyed",
                    disposition=STRUCTURAL, proposed_action="relocate the misplaced store (decision item)",
                    dedup_key="demo-structural", detail={"synthetic": True}),
            Finding(domain="demo", check="decision_path", severity="crit",
                    diagnosis="synthetic: a disruptive action needs Leo's authorization",
                    disposition=DECISION, proposed_action="reboot the host to apply security updates",
                    dedup_key="demo-decision", detail={"synthetic": True}),
        ]

    def remediate_safe(self, f: "Finding") -> str:
        return "synthetic safe reclaim performed (no real resource touched)"

    def execute_decision(self, f: "Finding") -> str:
        return "synthetic gated action executed (no real resource touched)"


def _print_dry_run(custodians: list) -> int:
    print("DevOps custodian — DRY RUN (assess + classify only, no action taken)\n")
    total = 0
    for c in custodians:
        cname = getattr(c, "domain", c.__class__.__name__)
        try:
            findings = c.assess() or []
        except Exception as e:
            print(f"  {cname}: assess ERROR — {type(e).__name__}: {e}")
            continue
        if not findings:
            print(f"  {cname}: healthy — [] (no findings)")
            continue
        for f in findings:
            total += 1
            print(f"  {cname}: [{f.disposition.upper()}] {f.check} ({f.severity}) — {f.proposed_action}")
            print(f"           diagnosis: {f.diagnosis}")
    print(f"\nTotal findings: {total}. Routing plan only — nothing executed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DevOps custodian runner (KAI-46)")
    ap.add_argument("--dry-run", action="store_true", help="assess + print routing plan; take no action")
    ap.add_argument("--demo", action="store_true", help="forced-engage synthetic custodian, recording deps")
    ap.add_argument("--demo-live-gate", action="store_true", help="demo but raise a REAL approval gate to Leo")
    ap.add_argument("--liveness-max-age-s", type=float, default=3600.0)
    args = ap.parse_args()

    if args.demo or args.demo_live_gate:
        demo = _DemoCustodian()
        if args.demo_live_gate:
            deps = default_deps()  # real structural filing + real gate raise
            os.environ.setdefault("DEVOPS_GATE_TIMEOUT_S", "120")
            summary = run_custodians([demo], deps=deps, record=False)
        else:
            deps = recording_deps()
            summary = run_custodians([demo], deps=deps, record=False)
            summary["_recorded_routes"] = getattr(deps, "_calls", {})
        print(json.dumps(summary, indent=2, default=str))
        return 0

    custodians = load_custodians()
    if not custodians:
        print("[WARN] no custodians loaded — nothing to sweep", file=sys.stderr)
        return 0

    if args.dry_run:
        return _print_dry_run(custodians)

    summary = run_custodians(custodians, liveness_max_age_s=args.liveness_max_age_s,
                             preempt_reclaim=emergency_reclaimer())
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

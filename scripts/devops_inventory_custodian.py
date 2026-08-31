#!/usr/bin/env python3
"""Inventory-drift custodian — KAI-1298 Phase 3.

Closes the LATENCY gap left by inventory_reconcile.py. That gate runs only at CLOSE:
a build ticket filed at 10am that duplicates a LIVE capability sits undetected until the
next session close (potentially days). This custodian runs the SAME collision sweep on the
15-min DevOps cadence — throttled to once/day — so a duplicate filed BETWEEN closes is
surfaced as a Finding the next human touch sees, not buried until close.

Single source of truth: the sweep itself is NOT reimplemented here. It lives in
~/sonicink/scripts/inventory_reconcile.py (the same code the close gate runs); this
custodian is a thin consumer of its `--json` output — reuse, not a fork.

Contract (§1, mirroring the findings-contract's "no alarm without a cause"):
  - a STAMPED build ticket whose declared INVENTORY-CHECK query now             -> STRUCTURAL (crit)
    matches a LIVE capability (confirmed duplicate)                               propose review/close
  - an UNSTAMPED build ticket whose NAME collides with live capabilities        -> STRUCTURAL (warn)
    on distinctive tokens (careless-duplicate — the exact Phase 3 case)           propose verify-then-stamp

It PROPOSES, never closes — DevOps proposes, Leo (or the close) disposes; STRUCTURAL routes
each Finding to the DevOps Plane queue, deduped by ticket so a standing duplicate is filed
once, not daily. Read-only + fail-open: Plane/inventory unreachable -> [] (no false alarm),
throttle/state errors never raise. It never auto-mutates a ticket.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make devops_ownership importable when this module is run standalone (the dispatcher
# already sets these; harmless when it does).
_KAI_ROOT = Path(os.environ.get("KAI_SYSTEM_ROOT", "/home/leo/kai-system"))
for _p in (str(_KAI_ROOT), str(_KAI_ROOT / "shared"), str(_KAI_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The sweep's single source of truth (sonicink, mirrored on the worker). Overridable for tests.
RECONCILE = Path(os.environ.get(
    "INVENTORY_RECONCILE_PATH", "/home/leo/sonicink/scripts/inventory_reconcile.py"))
STATE = Path(os.environ.get(
    "INVENTORY_CUSTODIAN_STATE", "/home/leo/kai-system/logs/.devops_inventory_custodian.state"))
THROTTLE_H = float(os.environ.get("INVENTORY_CUSTODIAN_THROTTLE_H", "20"))  # daily, on a 15-min cadence
SWEEP_TIMEOUT_S = int(os.environ.get("INVENTORY_CUSTODIAN_TIMEOUT_S", "150"))  # harvest hits net + Plane


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── pure helpers (unit-tested; no IO) ──────────────────────────────────────────

def due(last_iso: str | None, now: datetime, throttle_h: float = THROTTLE_H) -> bool:
    """Is the (network-heavy) sweep due? Due when never run, on an unparseable stamp
    (fail-open — a corrupt state file must never wedge the sweep shut), or >throttle_h old."""
    if not last_iso:
        return True
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return True
    return (now - last).total_seconds() / 3600.0 >= throttle_h


def _ticket_ref(ident, name) -> str:
    """A human-recognizable reference for the Finding text. The reconcile fetch carries no
    sequence_id (its issue objects lack it), so a numeric id becomes KAI-<n> but a UUID
    falls back to the ticket NAME — never a meaningless "KAI-<uuid>". The stable UUID still
    lives in dedup_key + detail for machine dedup."""
    s = str(ident)
    if s.isdigit():
        return f"KAI-{s}"
    nm = (name or "").strip()
    return f'"{nm[:70]}"' if nm else s


def findings_from_sweep(res: dict) -> list:
    """Map the reconcile JSON (failures[] + sweeps[]) onto DevOps Findings. Pure: takes the
    parsed result, returns Finding objects with disposition + proposed_action already set."""
    from devops_ownership import Finding, STRUCTURAL

    out = []
    for f in res.get("failures", []):
        ident = f.get("id", "?")
        live = f.get("live", [])
        ref = _ticket_ref(ident, f.get("name"))
        out.append(Finding(
            domain="inventory", check="duplicate_build_ticket", severity="crit",
            diagnosis=(f"Build ticket {ref} carries an INVENTORY-CHECK stamp whose declared "
                       f"query now matches LIVE capability {live} — a confirmed duplicate filed "
                       f"between closes; the capability already exists and is running."),
            disposition=STRUCTURAL,
            proposed_action=(f"Review {ref} — re-scope to the genuine gap or close as a "
                             f"duplicate of live {live}"),
            dedup_key=f"inventory-dup-{ident}",
            detail={"id": ident, "name": f.get("name"), "declared": f.get("declared"), "live": live},
        ))
    for s in res.get("sweeps", []):
        ident = s.get("id", "?")
        live = s.get("live", [])
        ref = _ticket_ref(ident, s.get("name"))
        out.append(Finding(
            domain="inventory", check="collides_with_live_capability", severity="warn",
            diagnosis=(f"Unstamped build ticket {ref} has a name that collides with LIVE "
                       f"capability {live} on distinctive tokens — it may rebuild what already "
                       f"exists. Filed between closes, so the close gate has not yet swept it."),
            disposition=STRUCTURAL,
            proposed_action=(f"Verify {ref} is not already built; re-scope to the delta and "
                             f"add an INVENTORY-CHECK stamp"),
            dedup_key=f"inventory-collision-{ident}",
            detail={"id": ident, "name": s.get("name"), "live": live},
        ))
    return out


# ── IO layer ───────────────────────────────────────────────────────────────────

def _read_last() -> str | None:
    try:
        return STATE.read_text().strip() or None
    except OSError:
        return None


def _stamp_run(now: datetime) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(now.isoformat())
    except OSError as e:  # a state-write failure must not abort a completed sweep
        print(f"[WARN] inventory custodian: could not stamp run: {type(e).__name__}: {e}", file=sys.stderr)


def _run_sweep() -> dict | None:
    """Invoke the reconcile sweep (fresh harvest) and parse its JSON. None on any failure —
    the sweep is the single source of truth; we never reimplement it, and an unreachable
    Plane/inventory is a no-op (fail-open), never a fabricated Finding."""
    if not RECONCILE.exists():
        print(f"[WARN] inventory custodian: sweep not found at {RECONCILE}", file=sys.stderr)
        return None
    try:
        r = subprocess.run(
            # --no-persist: fresh harvest, but never mutate the worker's mirrored inventory
            # file (the close owns that write); this custodian is read-only by construction.
            [sys.executable, str(RECONCILE), "--json", "--no-persist"],
            capture_output=True, text=True, timeout=SWEEP_TIMEOUT_S,
        )
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[WARN] inventory custodian: sweep failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    if r.returncode != 0 or not r.stdout.strip():
        print(f"[WARN] inventory custodian: sweep rc={r.returncode} stderr={r.stderr[:160]}", file=sys.stderr)
        return None
    try:
        res = json.loads(r.stdout)
    except ValueError as e:
        print(f"[WARN] inventory custodian: sweep JSON parse error: {e}", file=sys.stderr)
        return None
    if res.get("unreachable"):
        print("[WARN] inventory custodian: Plane unreachable — sweep not authoritative, no findings", file=sys.stderr)
        return None
    return res


# ── the Custodian ──────────────────────────────────────────────────────────────

class InventoryCustodian:
    """Duplicate-build-ticket drift, between closes. STRUCTURAL only — proposes, never closes."""

    domain = "inventory"

    def assess(self) -> list:
        now = _now()
        if not due(_read_last(), now):
            return []  # throttled — swept within the day already
        res = _run_sweep()
        if res is None:
            return []  # unreachable / no sweep — fail-open, never a false alarm
        _stamp_run(now)  # a successful sweep counts as the day's run, regardless of hit count
        return findings_from_sweep(res)

    def remediate_safe(self, f) -> str:
        # STRUCTURAL findings are never auto-remediated; this exists to satisfy the protocol.
        return "no-op — inventory drift is proposed (STRUCTURAL), never auto-resolved"


if __name__ == "__main__":
    # Ad-hoc: print what the custodian WOULD file, ignoring the throttle. Read-only.
    _res = _run_sweep() or {}
    _fs = findings_from_sweep(_res)
    print(f"inventory custodian — {len(_fs)} finding(s) "
          f"({len(_res.get('failures', []))} confirmed dup, {len(_res.get('sweeps', []))} collision)")
    for _f in _fs:
        print(f"  [{_f.severity}] {_f.dedup_key}: {_f.proposed_action}")

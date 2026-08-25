#!/usr/bin/env python3
"""Currency custodian — CUR-4 (System Currency Program: notify + cadence).

DevOps owns dependency + WordPress currency. The heavy read-only scan
(scripts/currency_scan.py) runs weekly on its own cron and writes
shared/currency/freshness_state.json. THIS custodian plugs into the DevOps
custodian runner (devops_custodian.py — already on a */15 cron with the ONE
dispatcher): every sweep it reads that state and turns *actionable* staleness
into deduped Findings. It never runs the scan and never mutates anything.

Routing (the currency program is report-only — nothing here auto-applies):
  - a stale OWNED currency layer (py_deps / npm_deps / wp_fleet)  -> STRUCTURAL
    (a gated bump/update: ONE deduped Plane triage item per layer — never a
     per-package page, never an auto-apply)
  - the scan itself missing or stale (the weekly cadence stopped)  -> STRUCTURAL

Silent when fresh (plan §2.6): assess() returns [] when every owned layer is
fresh/not-checked and the scan is recent — no routine "all-green" noise. os_apt,
container_images and tls_certs are owned by the updates/services/fleet custodians
and green_baseline, so this custodian deliberately does NOT double-report them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = _ROOT / "shared" / "currency" / "freshness_state.json"
STALE_SCAN_DAYS = 8  # weekly cadence + a day of slack; older => the weekly scan cron has stopped

# The currency program's OWN layers. Everything else in freshness_state.json is
# owned by another custodian (updates/services/fleet) or green_baseline — reporting
# it here would double-file the same triage item.
_OWNED = ("py_deps", "npm_deps", "wp_fleet")
_GATED_ACTION = {
    "py_deps": ("review + bump the outdated Python dependencies and rebuild the affected "
                "services (gated — never auto-applied)"),
    "npm_deps": ("review + bump the outdated kai-web JS dependencies and rebuild "
                 "(gated — never auto-applied)"),
    "wp_fleet": ("apply the available WordPress core/plugin updates through the gated host-ops "
                 "path (WP updates are never auto-applied)"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def classify_currency(state, now=None, stale_scan_days=STALE_SCAN_DAYS) -> list:
    """Pure: a freshness_state.json dict -> finding-specs. [] when nothing is actionable.
    `state` is None/{} when no scan has ever run. Unit-testable in isolation.
    Each spec: {check, disposition, severity, diagnosis, proposed_action, dedup_key, detail}."""
    now = now or _now()
    specs = []
    state = state if isinstance(state, dict) else None  # a malformed/non-object state == no trustworthy scan

    # 1. cadence meta-check — the scan must actually be running, else the board is a lie
    gen = _parse_iso(state.get("generated_at")) if state else None
    if not state or gen is None:
        specs.append({
            "check": "scan_missing", "disposition": "structural", "severity": "warn",
            "diagnosis": ("no currency scan state present — the weekly currency_scan has never run "
                          "or its output is unreadable, so currency is unmeasured"),
            "proposed_action": "install/restore the weekly currency_scan cron so freshness is measured",
            "dedup_key": "currency-scan-stale", "detail": {}})
        return specs  # nothing else is trustworthy without a scan
    age_days = (now - gen).total_seconds() / 86400.0
    if age_days > stale_scan_days:
        specs.append({
            "check": "scan_stale", "disposition": "structural", "severity": "warn",
            "diagnosis": (f"currency scan is {age_days:.1f}d old (> {stale_scan_days}d) — the weekly "
                          "scan cadence has stopped, so the currency board is going stale"),
            "proposed_action": "repair the weekly currency_scan cron",
            "dedup_key": "currency-scan-stale", "detail": {"age_days": round(age_days, 1)}})

    # 2. actionable staleness per OWNED layer — one deduped finding per layer, not per package
    layers = state.get("layers")
    layers = layers if isinstance(layers, dict) else {}
    for name in _OWNED:
        layer = layers.get(name)
        layer = layer if isinstance(layer, dict) else {}
        if layer.get("status") != "stale":
            continue  # fresh / not-checked assert nothing is wrong — stay silent
        stale_comps = [c for c in (layer.get("components") or [])
                       if isinstance(c, dict) and c.get("status") == "stale"]
        stale_names = [c.get("name") for c in stale_comps]
        cause = layer.get("cause") or layer.get("detail") or f"{name} has components behind current"
        specs.append({
            "check": f"{name}_stale", "disposition": "structural", "severity": "warn",
            "diagnosis": f"{name}: {len(stale_comps)} component(s) stale — {cause}",
            "proposed_action": _GATED_ACTION.get(name, "review and bump (gated)"),
            "dedup_key": f"currency-{name}",
            "detail": {"stale_components": stale_names}})
    return specs


class CurrencyCustodian:
    domain = "currency"

    def assess(self) -> list:
        from devops_ownership import Finding
        try:
            state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
        except (OSError, ValueError):
            state = None  # unreadable state -> classify_currency raises the scan_missing finding
        specs = classify_currency(state)
        return [Finding(domain="currency", **s) for s in specs]

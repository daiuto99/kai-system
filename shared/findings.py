"""KAI Findings Contract — honesty enforced in code, not by goodwill.

One invariant: a finding that asserts something is WRONG (status in
BAD_STATUSES) MUST carry a non-empty ``cause`` — either a verified explanation
or the literal ``NOT_YET_DIAGNOSED``. There is no third option. A bare,
uncaused alarm cannot be emitted by any monitor that routes through this module.

``fresh``/``ok``/``pass`` and the honest ``not-checked`` state assert nothing is
wrong, so they need no cause.

This is the machine-checked teeth behind KAI's "never a bare signal / never
faked green" principle. Every scanner/reader/monitor calls ``enforce_causes()``
on its findings before publishing; ``assert_contract()`` is the strict form for
tests/CI. The point: the system, not the operator's memory, guarantees a
signal never reaches Leo without a cause or an explicit "cause unknown".
"""
from __future__ import annotations

NOT_YET_DIAGNOSED = "not-yet-diagnosed"

# Statuses that assert a problem — these REQUIRE a cause.
BAD_STATUSES = {
    "stale", "alert", "fail", "failed", "degraded", "error",
    "critical", "warn", "warning", "red", "amber",
}
# Explicitly cause-free: good states + the honest no-reader state.
# fresh/ok/pass/green = fine; not-checked = honestly no live reader.


def needs_cause(status) -> bool:
    return str(status or "").lower() in BAD_STATUSES


def _has_cause(obj: dict) -> bool:
    c = obj.get("cause")
    return isinstance(c, str) and c.strip() != ""


def _first_component_cause(layer: dict):
    for comp in layer.get("components", []) or []:
        if isinstance(comp, dict) and _has_cause(comp):
            return comp["cause"]
    return None


def enforce_causes(layers: dict) -> int:
    """Guarantee the invariant by construction. Mutates ``layers`` in place:
    any bad-status finding lacking a cause is stamped (a layer inherits a
    component's verified cause if present, else NOT_YET_DIAGNOSED). Returns the
    count of findings left explicitly undiagnosed — an honest visible number,
    not a hidden one.
    """
    undiagnosed = 0
    for layer in (layers or {}).values():
        if not isinstance(layer, dict):
            continue
        if needs_cause(layer.get("status")) and not _has_cause(layer):
            layer["cause"] = _first_component_cause(layer) or NOT_YET_DIAGNOSED
            if layer["cause"] == NOT_YET_DIAGNOSED:
                undiagnosed += 1
        for comp in layer.get("components", []) or []:
            if isinstance(comp, dict) and needs_cause(comp.get("status")) and not _has_cause(comp):
                comp["cause"] = NOT_YET_DIAGNOSED
                undiagnosed += 1
    return undiagnosed


def assert_contract(layers: dict) -> None:
    """Strict form for tests/CI: raise AssertionError if ANY bad-status finding
    lacks a cause. This is what proves the contract has teeth."""
    violations = []
    for name, layer in (layers or {}).items():
        if not isinstance(layer, dict):
            continue
        if needs_cause(layer.get("status")) and not _has_cause(layer):
            violations.append(str(name))
        for comp in layer.get("components", []) or []:
            if isinstance(comp, dict) and needs_cause(comp.get("status")) and not _has_cause(comp):
                violations.append(f"{name}:{comp.get('name')}")
    if violations:
        raise AssertionError(
            "Findings Contract violated — bad-status finding with no cause: " + ", ".join(violations)
        )

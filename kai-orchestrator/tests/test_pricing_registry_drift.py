"""KAI-1283 — pricing single-source drift guard.

provider_registry.json is the authored home for paid per-token + per-call rates.
usage_tracker loads them from there at import, with hardcoded fallback tables used
only if the registry is unreadable. These tests assert the fallback mirrors the
registry (so the safety net can never silently diverge) and that live COSTS were
actually sourced from the registry when it is present.
"""
import json

import pytest

import usage_tracker as ut


def _registry():
    try:
        return json.loads(ut.REGISTRY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _registry_paid_and_percall(reg):
    paid, per_call = {}, {}
    for pid, p in (reg.get("providers") or {}).items():
        for model, r in (p.get("token_per_1m") or {}).items():
            paid[model] = (float(r["input"]), float(r["output"]))
        for unit, usd in (p.get("per_call_usd") or {}).items():
            per_call[f"{pid}/{unit}"] = float(usd)
    return paid, per_call


def test_fallback_mirrors_registry():
    """The hardcoded fallback price tables must equal the registry — else the
    safety net has drifted from the single source and could misprice on a bad
    registry read."""
    reg = _registry()
    if reg is None:
        pytest.skip("provider_registry.json not readable in this environment")
    paid, per_call = _registry_paid_and_percall(reg)
    assert ut._FALLBACK_PAID == paid, (
        "usage_tracker._FALLBACK_PAID drifted from provider_registry.json token_per_1m"
    )
    assert ut._FALLBACK_PER_CALL == per_call, (
        "usage_tracker._FALLBACK_PER_CALL drifted from provider_registry.json per_call_usd"
    )


def test_live_costs_sourced_from_registry():
    """When the registry is present, every paid model in it appears in COSTS with
    the registry's rate (i.e. the registry, not the fallback, is authoritative)."""
    reg = _registry()
    if reg is None:
        pytest.skip("provider_registry.json not readable in this environment")
    paid, per_call = _registry_paid_and_percall(reg)
    for model, rate in paid.items():
        assert ut.COSTS.get(model) == rate, f"COSTS[{model}] != registry rate {rate}"
    for key, usd in per_call.items():
        assert ut.PER_CALL_COSTS.get(key) == usd, f"PER_CALL_COSTS[{key}] != {usd}"


def test_self_hosted_models_stay_zero():
    """Self-hosted local models are structurally free and must remain $0 in COSTS,
    never defaulting to a paid rate."""
    for model in ut._SELF_HOSTED_ZERO:
        assert ut.COSTS.get(model) == (0.0, 0.0), f"{model} should be free"


if __name__ == "__main__":
    for t in (test_fallback_mirrors_registry, test_live_costs_sourced_from_registry,
              test_self_hosted_models_stay_zero):
        try:
            t()
            print(f"  {t.__name__}... PASS")
        except Exception as e:
            print(f"  {t.__name__}... FAIL: {e}")

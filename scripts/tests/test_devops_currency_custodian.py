"""CUR-4 — currency custodian classification logic (report-only, deduped, silent-when-fresh)."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "devops_currency_custodian", _ROOT / "devops_currency_custodian.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _state(layers, generated_at=NOW):
    return {"generated_at": generated_at.isoformat(), "layers": layers}


def _layer(status, comps=None):
    return {"status": status, "components": comps or [], "cause": "x behind current"}


# ── cadence meta-check ─────────────────────────────────────────────────────────
def test_no_scan_state_flags_scan_missing():
    specs = cc.classify_currency(None, now=NOW)
    assert len(specs) == 1 and specs[0]["check"] == "scan_missing"
    assert specs[0]["disposition"] == "structural" and specs[0]["dedup_key"] == "currency-scan-stale"


def test_stale_scan_flags_cadence_stopped():
    old = NOW - timedelta(days=30)
    specs = cc.classify_currency(_state({}, generated_at=old), now=NOW)
    assert any(s["check"] == "scan_stale" for s in specs)


def test_recent_scan_all_fresh_is_silent():
    layers = {"py_deps": _layer("fresh"), "npm_deps": _layer("not-checked"), "wp_fleet": _layer("fresh")}
    assert cc.classify_currency(_state(layers), now=NOW) == []  # no routine all-green noise


# ── actionable staleness ───────────────────────────────────────────────────────
def test_stale_owned_layer_becomes_one_structural_finding():
    comps = [_c("kai-worker-api", "stale"), _c("kai-council-api", "stale"), _c("kai-buzz", "fresh")]
    specs = cc.classify_currency(_state({"py_deps": _layer("stale", comps)}), now=NOW)
    assert len(specs) == 1  # ONE finding for the layer, not one per package
    s = specs[0]
    assert s["check"] == "py_deps_stale" and s["dedup_key"] == "currency-py_deps"
    assert s["disposition"] == "structural"
    assert s["detail"]["stale_components"] == ["kai-worker-api", "kai-council-api"]  # only the stale ones


def test_not_owned_layer_is_not_double_reported():
    # os_apt is owned by the updates custodian — currency must not file it
    layers = {"os_apt": _layer("stale", [_c("apt packages", "stale")]), "py_deps": _layer("fresh")}
    assert cc.classify_currency(_state(layers), now=NOW) == []


def test_each_owned_layer_dedups_independently():
    layers = {"py_deps": _layer("stale", [_c("svc", "stale")]),
              "wp_fleet": _layer("stale", [_c("site", "stale")])}
    keys = {s["dedup_key"] for s in cc.classify_currency(_state(layers), now=NOW)}
    assert keys == {"currency-py_deps", "currency-wp_fleet"}


def test_findings_are_valid_and_report_only():
    # every spec has the fields a Finding requires and is never an auto-apply
    layers = {"py_deps": _layer("stale", [_c("svc", "stale")]), "wp_fleet": _layer("stale", [_c("s", "stale")])}
    specs = cc.classify_currency(_state(layers), now=NOW)
    assert specs
    for s in specs:
        assert s["dedup_key"].strip() and s["disposition"] == "structural" and s["diagnosis"]
        assert s["severity"] in ("warn", "crit") and s["proposed_action"]


def test_malformed_state_degrades_to_scan_missing_never_crashes():
    # non-dict state, or a state whose `layers` is the wrong type, must not raise
    for bad in ("x", 42, [1, 2], {"generated_at": NOW.isoformat(), "layers": ["oops"]},
                {"generated_at": NOW.isoformat(), "layers": {"py_deps": "not-a-dict"}}):
        specs = cc.classify_currency(bad, now=NOW)
        assert isinstance(specs, list)  # never crashes
        # the first three have no valid scan timestamp -> scan_missing; the last two are silent (fresh-ish)
    assert cc.classify_currency("x", now=NOW)[0]["check"] == "scan_missing"


def _c(name, status):
    return {"name": name, "status": status}

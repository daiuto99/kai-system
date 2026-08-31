"""KAI-1298 Phase 3 — inventory-drift custodian decision logic (pure, no IO)."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_inventory_custodian",
    Path(__file__).resolve().parent.parent / "devops_inventory_custodian.py")
inv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inv)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


# ── throttle (due) ─────────────────────────────────────────────────────────────

def test_due_never_run():
    assert inv.due(None, NOW) is True


def test_due_unparseable_stamp_fails_open():
    # a corrupt state file must never wedge the sweep shut
    assert inv.due("not-a-timestamp", NOW) is True


def test_due_recent_is_throttled():
    last = (NOW - timedelta(hours=2)).isoformat()
    assert inv.due(last, NOW, throttle_h=20) is False


def test_due_old_is_due():
    last = (NOW - timedelta(hours=21)).isoformat()
    assert inv.due(last, NOW, throttle_h=20) is True


def test_due_exactly_at_threshold_is_due():
    last = (NOW - timedelta(hours=20)).isoformat()
    assert inv.due(last, NOW, throttle_h=20) is True


# ── sweep -> Findings ───────────────────────────────────────────────────────────

def test_no_hits_yields_no_findings():
    assert inv.findings_from_sweep({"failures": [], "sweeps": []}) == []
    assert inv.findings_from_sweep({}) == []


def test_ticket_ref_numeric_becomes_kai_seq():
    assert inv._ticket_ref("1301", "Build tasks") == "KAI-1301"


def test_ticket_ref_uuid_falls_back_to_name():
    # the reconcile fetch has no sequence_id -> never a meaningless "KAI-<uuid>"
    ref = inv._ticket_ref("6a08a4e5-5080-4544-aee4-650ee7b08fee", "Build a parking-lot endpoint")
    assert ref == '"Build a parking-lot endpoint"'
    assert "6a08a4e5" not in ref


def test_confirmed_duplicate_is_crit_structural():
    from devops_ownership import STRUCTURAL
    res = {"failures": [{"id": "1301", "name": "Build the tasks API",
                         "declared": ["endpoint:/tasks"], "live": ["endpoint:/tasks"]}],
           "sweeps": []}
    fs = inv.findings_from_sweep(res)
    assert len(fs) == 1
    f = fs[0]
    assert f.domain == "inventory"
    assert f.severity == "crit"
    assert f.disposition == STRUCTURAL
    assert f.dedup_key == "inventory-dup-1301"
    assert "1301" in f.proposed_action
    assert "LIVE" in f.diagnosis and len(f.diagnosis) > 40  # findings contract: a real cause


def test_name_collision_is_warn_structural():
    from devops_ownership import STRUCTURAL
    res = {"failures": [],
           "sweeps": [{"id": "1302", "name": "Build a parking-lot capture endpoint",
                       "live": ["endpoint:/parking-lot/capture"]}]}
    fs = inv.findings_from_sweep(res)
    assert len(fs) == 1
    f = fs[0]
    assert f.severity == "warn"
    assert f.disposition == STRUCTURAL
    assert f.dedup_key == "inventory-collision-1302"
    assert "LIVE" in f.diagnosis and len(f.diagnosis) > 40


def test_dedup_keys_are_stable_and_unique_per_ticket():
    res = {"failures": [{"id": "1", "name": "a", "declared": ["x"], "live": ["x"]}],
           "sweeps": [{"id": "2", "name": "b", "live": ["y"]}]}
    keys = [f.dedup_key for f in inv.findings_from_sweep(res)]
    assert keys == ["inventory-dup-1", "inventory-collision-2"]
    assert len(set(keys)) == len(keys)


def test_custodian_domain_and_protocol_shape():
    c = inv.InventoryCustodian()
    assert c.domain == "inventory"
    assert callable(c.assess)
    assert callable(c.remediate_safe)
    # STRUCTURAL findings are never auto-remediated
    assert "no-op" in c.remediate_safe(None).lower()

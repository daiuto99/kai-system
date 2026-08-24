"""KAI-46 — DevOps ownership layer: Finding contract + dispatcher routing + meta-monitor."""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_shared = Path(__file__).resolve().parent.parent.parent / "shared"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))
_spec = importlib.util.spec_from_file_location("devops_ownership", _shared / "devops_ownership.py")
do = importlib.util.module_from_spec(_spec)
sys.modules["devops_ownership"] = do  # dataclass needs the module registered (PEP 563 annotations)
_spec.loader.exec_module(do)


def _finding(**kw):
    base = dict(domain="d", check="c", severity="warn", diagnosis="why",
                disposition=do.AUTO, proposed_action="act", dedup_key="k", detail={})
    base.update(kw)
    return do.Finding(**base)


# ── Finding contract ───────────────────────────────────────────────────────────

def test_bad_disposition_rejected():
    with pytest.raises(ValueError):
        _finding(disposition="explode")


def test_missing_dedup_key_rejected():
    with pytest.raises(ValueError):
        _finding(dedup_key="")


def test_bad_severity_without_diagnosis_is_stamped_not_yet_diagnosed():
    f = _finding(severity="crit", diagnosis="")
    assert f.diagnosis == do.NOT_YET_DIAGNOSED
    assert f.undiagnosed is True


def test_good_diagnosis_preserved():
    f = _finding(severity="crit", diagnosis="containerd 23G on OS disk")
    assert f.diagnosis == "containerd 23G on OS disk"
    assert f.undiagnosed is False


# ── Recording deps + a fake custodian for routing tests ────────────────────────

class _Rec:
    def __init__(self, gate: "do.DecisionOutcome"):
        self.dashboard, self.structural, self.decision = [], [], []
        self._gate = gate

    def deps(self):
        return do.Deps(
            notify_dashboard=lambda f, r: self.dashboard.append((f.dedup_key, r)),
            file_structural=lambda f: (self.structural.append(f.dedup_key) or f"queued {f.dedup_key}"),
            request_decision=lambda f: (self.decision.append(f.dedup_key) or self._gate),
        )


class _Cust:
    domain = "fake"
    def __init__(self):
        self.remediated, self.executed = [], []
    def assess(self):
        return []
    def remediate_safe(self, f):
        self.remediated.append(f.dedup_key)
        return "did the safe thing"
    def execute_decision(self, f):
        self.executed.append(f.dedup_key)
        return "did the gated thing"


# ── Dispatcher routing ─────────────────────────────────────────────────────────

def test_auto_calls_remediate_then_dashboard():
    rec = _Rec(do.DecisionOutcome(True, True))
    c = _Cust()
    out = do.dispatch(_finding(disposition=do.AUTO), c, rec.deps())
    assert c.remediated == ["k"]
    assert rec.dashboard and rec.dashboard[0][0] == "k"
    assert out["handled"] is True and "auto-remediated" in out["outcome"]


def test_structural_files_plane_item():
    rec = _Rec(do.DecisionOutcome(True, True))
    out = do.dispatch(_finding(disposition=do.STRUCTURAL, severity="crit"), _Cust(), rec.deps())
    assert rec.structural == ["k"]
    assert out["handled"] is True and "queued" in out["outcome"]


def test_decision_approved_executes_and_notifies():
    rec = _Rec(do.DecisionOutcome(approved=True, resolved=True))
    c = _Cust()
    out = do.dispatch(_finding(disposition=do.DECISION, severity="crit"), c, rec.deps())
    assert rec.decision == ["k"]
    assert c.executed == ["k"]
    assert rec.structural == []           # approved → NOT queued
    assert "approved" in out["outcome"] and out["handled"] is True


def test_decision_rejected_stands_down_and_queues():
    rec = _Rec(do.DecisionOutcome(approved=False, resolved=True))
    c = _Cust()
    out = do.dispatch(_finding(disposition=do.DECISION, severity="crit"), c, rec.deps())
    assert c.executed == []                # rejected → NOT executed
    assert rec.structural == ["k"]         # rejected → queued as structural
    assert "rejected" in out["outcome"]


def test_decision_timeout_is_failclosed_and_queues():
    rec = _Rec(do.DecisionOutcome(approved=False, resolved=False))  # timed out
    c = _Cust()
    out = do.dispatch(_finding(disposition=do.DECISION, severity="crit"), c, rec.deps())
    assert c.executed == []                # timeout → fail-closed, NOT executed
    assert rec.structural == ["k"]         # timeout → queued
    assert "timed out" in out["outcome"]


def test_dispatch_is_failsoft_when_remediate_raises():
    class Boom(_Cust):
        def remediate_safe(self, f):
            raise RuntimeError("kaboom")
    rec = _Rec(do.DecisionOutcome(True, True))
    out = do.dispatch(_finding(disposition=do.AUTO), Boom(), rec.deps())
    assert out["handled"] is False
    assert "dispatch error" in out["outcome"]  # captured, not raised


def test_missing_execute_decision_logs_not_crashes():
    class NoExec:
        domain = "x"
        def assess(self): return []
        def remediate_safe(self, f): return "safe"
    rec = _Rec(do.DecisionOutcome(approved=True, resolved=True))
    out = do.dispatch(_finding(disposition=do.DECISION, severity="crit"), NoExec(), rec.deps())
    assert out["handled"] is True and "no execute_decision" in out["outcome"]


# ── Meta-monitor (liveness) ─────────────────────────────────────────────────────

def test_meta_monitor_flags_missing_and_stale(monkeypatch, tmp_path):
    live = tmp_path / "liveness.json"
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(seconds=100)).isoformat()
    stale = (now - timedelta(seconds=9999)).isoformat()
    live.write_text(json.dumps({"storage": fresh, "backups": stale}))
    monkeypatch.setattr(do, "LIVENESS", live)
    out = do.meta_monitor(["storage", "backups", "fleet"], max_age_s=3600, now=now)
    checks = {f.detail["domain"] for f in out}
    assert checks == {"backups", "fleet"}          # storage fresh → not flagged
    for f in out:
        assert f.disposition == do.STRUCTURAL
        assert f.domain == "meta"


def test_meta_monitor_clean_when_all_fresh(monkeypatch, tmp_path):
    live = tmp_path / "liveness.json"
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    live.write_text(json.dumps({"storage": (now - timedelta(seconds=10)).isoformat()}))
    monkeypatch.setattr(do, "LIVENESS", live)
    assert do.meta_monitor(["storage"], max_age_s=3600, now=now) == []


# ── run_custodians end-to-end (injected deps, no real transport) ────────────────

def test_run_custodians_dispatches_and_stamps_liveness(monkeypatch, tmp_path):
    live = tmp_path / "liveness.json"
    monkeypatch.setattr(do, "LIVENESS", live)

    class Eng(_Cust):
        domain = "storage"
        def assess(self):
            return [_finding(domain="storage", disposition=do.AUTO, dedup_key="s1")]
    rec = _Rec(do.DecisionOutcome(True, True))
    summary = do.run_custodians([Eng()], deps=rec.deps(), liveness_max_age_s=3600, record=False)
    assert summary["findings"] == 1
    assert rec.dashboard  # auto finding logged to dashboard
    assert json.loads(live.read_text()).get("storage")  # liveness stamped

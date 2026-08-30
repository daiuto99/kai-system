"""KAI-46 — DevOps ownership layer: Finding contract + dispatcher routing.
(W-1 #5 removed the custodian-liveness meta-monitor from this layer.)"""
import importlib.util
import json
import sys
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


# ── Meta-monitor removed in W-1 #5 (declaration-class tear-out) ─────────────────
# do.meta_monitor() and its "custodian not running" Findings were deleted — a dead
# custodian now surfaces via its DOMAIN diagnostic, not a liveness health declaration.


def test_meta_monitor_removed(monkeypatch, tmp_path):
    # The self-referential meta-monitor must be GONE, not merely quiet.
    assert not hasattr(do, "meta_monitor")


def test_run_custodians_emits_no_meta_findings(monkeypatch, tmp_path):
    # Even a never-stamped roster produces no meta health Finding anymore.
    live = tmp_path / "liveness.json"
    monkeypatch.setattr(do, "LIVENESS", live)
    summary = do.run_custodians([], record=False)
    assert summary["meta"] == []


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


# ── Pre-exhaustion guard (§Phase 3, KAI-48) ─────────────────────────────────────

def test_preempt_engages_only_at_or_above_threshold():
    assert do.pre_exhaustion_engage(94.9, preempt_pct=95) is False
    assert do.pre_exhaustion_engage(95.0, preempt_pct=95) is True
    assert do.pre_exhaustion_engage(99.9, preempt_pct=95) is True


def test_guard_returns_none_below_threshold():
    # Below the reserve band → no pre-empt, no reclaim call.
    assert do.pre_exhaustion_guard(preempt_pct=95, reclaim=lambda: "x", pct_fn=lambda: 80.0) is None


def test_guard_runs_reclaim_when_in_reserve_band():
    calls = []
    rec = do.pre_exhaustion_guard(
        preempt_pct=95, reclaim=lambda: (calls.append(1) or "reclaimed 2G"),
        pct_fn=lambda: 97.0)
    assert calls == [1]                         # emergency reclaim actually ran
    assert rec is not None and rec["event"] == "pre_exhaustion_preempt"
    assert rec["pct"] == 97.0 and rec["reclaimed"] == "reclaimed 2G"


def test_guard_is_failsoft_when_reclaim_raises():
    def boom():
        raise RuntimeError("disk io error")
    rec = do.pre_exhaustion_guard(preempt_pct=95, reclaim=boom, pct_fn=lambda: 96.0)
    assert rec is not None
    assert "emergency reclaim error" in rec["reclaimed"]  # captured, not raised


def test_guard_flags_when_no_reclaimer_supplied():
    rec = do.pre_exhaustion_guard(preempt_pct=95, reclaim=None, pct_fn=lambda: 98.0)
    assert rec is not None and "no emergency reclaimer" in rec["reclaimed"]


def test_run_custodians_records_preempt_in_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(do, "LIVENESS", tmp_path / "liveness.json")
    monkeypatch.setattr(do, "root_pct", lambda: 96.0)  # force the reserve band
    calls = []
    rec = _Rec(do.DecisionOutcome(True, True))
    summary = do.run_custodians(
        [_Cust()], deps=rec.deps(), record=False,
        preempt_reclaim=lambda: (calls.append(1) or "freed logs"))
    assert calls == [1]                                  # guard ran the reclaim first
    assert summary["preempt"] is not None
    assert summary["preempt"]["reclaimed"] == "freed logs"


def test_run_custodians_no_preempt_when_healthy(monkeypatch, tmp_path):
    monkeypatch.setattr(do, "LIVENESS", tmp_path / "liveness.json")
    monkeypatch.setattr(do, "root_pct", lambda: 40.0)  # plenty of headroom
    rec = _Rec(do.DecisionOutcome(True, True))
    summary = do.run_custodians([_Cust()], deps=rec.deps(), record=False,
                                preempt_reclaim=lambda: "should not run")
    assert summary["preempt"] is None

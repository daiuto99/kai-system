"""KAI-1487 — data-feed custodian decision logic + assess() flagging."""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "devops_feeds_custodian",
    Path(__file__).resolve().parent.parent / "devops_feeds_custodian.py")
fc = importlib.util.module_from_spec(_spec)
# Register before exec so the module-level @dataclass can resolve cls.__module__.
sys.modules[_spec.name] = fc
_spec.loader.exec_module(fc)

CAL = fc.Feed("calendar", "/calendar/events", "events", True)
OURA = fc.Feed("oura", "/oura/today", "readiness", False)


def test_healthy_feed_not_broken():
    broken, _ = fc.feed_broken({"events": [{"title": "x"}]}, CAL)
    assert broken is False


def test_unreachable_is_broken():
    broken, reason = fc.feed_broken(None, CAL)
    assert broken is True and "unreachable" in reason


def test_error_field_is_broken():
    # the exact 2026-09 failure: 200 with an error field
    broken, reason = fc.feed_broken({"events": [], "error": "gmail not configured"}, CAL)
    assert broken is True and "error:" in reason


def test_bad_feed_status_is_broken():
    broken, reason = fc.feed_broken({"events": [], "feed_status": "auth_failed"}, CAL)
    assert broken is True and "auth_failed" in reason


def test_empty_ok_feed_is_not_broken_when_empty():
    # a legitimately empty calendar week is NOT a failure
    broken, _ = fc.feed_broken({"events": []}, CAL)
    assert broken is False


def test_missing_content_key_is_broken():
    broken, reason = fc.feed_broken({"something_else": 1}, CAL)
    assert broken is True and "missing" in reason


def test_empty_not_ok_feed_is_broken_when_empty():
    # Oura is always populated when healthy — empty means broken
    broken, reason = fc.feed_broken({"readiness": {}}, OURA)
    assert broken is True and "empty" in reason


def test_oura_populated_is_not_broken():
    broken, _ = fc.feed_broken({"readiness": {"score": 82}}, OURA)
    assert broken is False


def test_severity_hard_vs_soft():
    assert fc._severity("unreachable / no response") == "crit"
    assert fc._severity("feed_status=auth_failed") == "crit"
    assert fc._severity("empty 'readiness' (this feed is always populated when healthy)") == "warn"


def test_assess_flags_broken_feeds_as_structural(monkeypatch):
    # calendar dead (error), everything else healthy -> exactly one STRUCTURAL finding
    def fake_fetch(path, **kw):
        if path.startswith("/calendar"):
            return {"events": [], "error": "calendar auth expired", "feed_status": "auth_failed"}
        if path.startswith("/gmail"):
            return {"emails": [{"id": "1"}]}
        if path.startswith("/tasks"):
            return {"today": [{"id": "t"}]}
        if path.startswith("/oura"):
            return {"readiness": {"score": 80}}
        return None
    monkeypatch.setattr(fc, "fetch_feed", fake_fetch)
    import devops_ownership as do  # importable via fc's shared-path insertion
    findings = fc.DataFeedsCustodian().assess()
    assert len(findings) == 1
    f = findings[0]
    assert f.domain == "feeds" and f.check == "calendar"
    # Compare to the constant, not a literal — a sibling test mutates do.STRUCTURAL in-process.
    assert f.disposition == do.STRUCTURAL
    assert f.severity == "crit"
    assert f.dedup_key == "feed-broken-calendar"


def test_assess_all_healthy_is_silent(monkeypatch):
    # a superset dict satisfies whichever content_key each feed checks -> zero findings
    monkeypatch.setattr(fc, "fetch_feed", lambda path, **kw: {
        "events": [1], "emails": [1], "today": [1], "readiness": {"score": 1}})
    assert fc.DataFeedsCustodian().assess() == []

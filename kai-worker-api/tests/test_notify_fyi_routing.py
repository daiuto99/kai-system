"""Autonomous-action FYI routing: POST /notify goes through the single notify
gateway to the dashboard System tab (the Leo-visible surface), not the retired
#devops Slack channel. Replaces a dead /notify (404) that silently dropped every
sprint_watchdog / close-complete / sprint_runner FYI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import system_activity as sa  # noqa: E402


def test_notify_routes_to_dashboard_audience_as_real(monkeypatch):
    captured = {}

    def fake_notify(event):
        captured["event"] = event
        return sa.ng.NotifyResult("dashboard_only", "dashboard", False, "test")

    monkeypatch.setattr(sa.ng, "notify", fake_notify)
    resp = sa.post_notify(sa.NotifyRequest(text="hi Leo", channel="devops"))

    assert resp["ok"] is True
    assert resp["destination"] == "dashboard"
    ev = captured["event"]
    # the Leo-visible surface + a real (non-synthetic) event so the reality gate keeps it
    assert ev.audience == "dashboard"
    assert ev.provenance == "real"
    assert ev.title == "hi Leo"


def test_notify_defaults_channel_and_source(monkeypatch):
    captured = {}

    def fake_notify(event):
        captured["e"] = event
        return sa.ng.NotifyResult("dashboard_only", "dashboard", False, "t")

    monkeypatch.setattr(sa.ng, "notify", fake_notify)
    sa.post_notify(sa.NotifyRequest(text="x"))
    assert captured["e"].source == "autonomous"
    assert captured["e"].kind == "alert"

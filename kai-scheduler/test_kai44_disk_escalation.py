"""KAI-44 — a self-unfixable, system-threatening CRITICAL (disk heading to 100%)
must escalate to Leo, not route dashboard_only.

Root cause of the 2026-08-24 disk crisis: the watchdog correctly detected
`Disk CRITICAL: 97%` and paged "you need to take action" every ~2h for ~11h, but
every page went through _slack_alert -> tg_alert(audience="dashboard"), and the
notify gateway only sends approval/personal audiences to Leo (Rule B). So the one
alert that genuinely needed a human never reached one. Fix: disk (LEO_CRITICAL_CHECKS)
pages with audience='personal'; everything else stays dashboard.
"""
import watchdog
import tg_alert as tgmod


def test_disk_is_a_leo_critical_check():
    assert "disk" in watchdog.LEO_CRITICAL_CHECKS


def test_slack_alert_defaults_to_dashboard(monkeypatch):
    seen = {}
    monkeypatch.setattr(tgmod, "tg_alert", lambda msg, **kw: seen.update(kw))
    watchdog._slack_alert("tok", "some routine operational note")
    assert seen.get("audience") == "dashboard"


def test_slack_alert_forwards_personal_audience(monkeypatch):
    seen = {}
    monkeypatch.setattr(tgmod, "tg_alert", lambda msg, **kw: seen.update(kw))
    watchdog._slack_alert("tok", "Disk CRITICAL: 99% used", audience="personal")
    assert seen.get("audience") == "personal"


def test_personal_audience_routes_to_leo_telegram():
    """Regression guard: the gateway must send personal → Leo's Telegram, else the
    escalation is silent again."""
    import notify_gateway as ng
    dest, reason = ng._route(ng.Event(audience="personal", title="x", body="y",
                                      kind="alert", source="watchdog"))
    assert dest == "telegram"
    dash, _ = ng._route(ng.Event(audience="dashboard", title="x", body="y",
                                 kind="alert", source="watchdog"))
    assert dash == "dashboard"

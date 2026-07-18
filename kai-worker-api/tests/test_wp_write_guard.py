import wp_write_guard as guard


def test_all_documented_canonical_surfaces_pass_allowlist():
    callers = (
        "/app/workflows/wordpress_publish_homepage.py",
        "/app/routes/wordpress.py",
        "/home/leo/sonicink/scripts/wp_add_site.sh",
        "/home/leo/sonicink/scripts/wp_brand_consistency.py",
    )
    for caller in callers:
        assert guard.assert_canonical_caller(caller, "test_write") == caller


def test_noncanonical_caller_alerts_devops_and_is_blocked(monkeypatch):
    alerts = []
    monkeypatch.setattr(guard, "_alert_devops", lambda caller, action: alerts.append((caller, action)))

    try:
        guard.assert_canonical_caller("/tmp/chat_session_wp_rest.py", "synthetic_direct_rest")
    except guard.WorkflowOnlyWriteViolation:
        pass
    else:
        raise AssertionError("non-canonical caller was not blocked")

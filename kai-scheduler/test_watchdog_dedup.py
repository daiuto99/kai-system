#!/usr/bin/env python3
"""KAI-471 — watchdog alert/dedup behavior tests.

Proves the consecutive-fail + classification design that closes the false
"Google Calendar credential expired" CRITICAL alerts on transient timeouts.

Run on the worker:
    python3 ~/kai-system/kai-scheduler/test_watchdog_dedup.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _fresh_module():
    """Import watchdog with a temp ALERT_STATE_FILE and fresh module state."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    os.environ["ALERT_STATE_FILE_OVERRIDE"] = tmp.name
    # Force reimport
    sys.path.insert(0, "/home/leo/kai-system/kai-scheduler")
    if "watchdog" in sys.modules:
        del sys.modules["watchdog"]
    import watchdog as wd  # type: ignore
    wd.ALERT_STATE_FILE = Path(tmp.name)
    wd._last_alert.clear()
    wd._fail_counter.clear()
    return wd, Path(tmp.name)


class WatchdogDedupTests(unittest.TestCase):

    def setUp(self):
        self.wd, self.state_path = _fresh_module()
        # 5c4e94f4: these tests prove the escalation MECHANISM, not prod deferral
        # config. google_calendar (the OAUTH exemplar) is in DEFERRED_CHECKS in
        # prod, which skips it before the counter/escalation path — the actual
        # root cause of the 3 stale failures (NOT the Slack->Telegram reroute the
        # ticket title guessed). Clear it so the exemplar is genuinely exercised;
        # test_deferred_check_is_skipped covers the deferral behavior separately.
        _p = mock.patch.object(self.wd, "DEFERRED_CHECKS", {})
        _p.start()
        self.addCleanup(_p.stop)

    def tearDown(self):
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    # ── Counter mechanics ────────────────────────────────────────────────────

    def test_consecutive_fail_counter_increments(self):
        self.assertEqual(self.wd._record_failure("google_calendar"), 1)
        self.assertEqual(self.wd._record_failure("google_calendar"), 2)
        self.assertEqual(self.wd._record_failure("google_calendar"), 3)

    def test_success_resets_counter(self):
        self.wd._record_failure("google_calendar")
        self.wd._record_failure("google_calendar")
        self.wd._record_success("google_calendar")
        self.assertNotIn("google_calendar", self.wd._fail_counter)

    def test_state_persists_to_disk(self):
        self.wd._record_failure("oura")
        data = json.loads(self.state_path.read_text())
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["fail_counters"]["oura"], 1)

    def test_legacy_flat_format_migrated_on_load(self):
        # Simulate legacy format
        self.state_path.write_text(json.dumps({"google_calendar": 12345.0}))
        loaded = self.wd._load_alert_state()
        self.assertIn("google_calendar", loaded["alerts"])
        self.assertEqual(loaded["fail_counters"], {})

    # ── Failure classification ───────────────────────────────────────────────

    def test_classify_timeout_as_transient(self):
        self.assertEqual(self.wd._classify_failure("timed out"), "transient")
        self.assertEqual(self.wd._classify_failure("ReadTimeout"), "transient")
        self.assertEqual(self.wd._classify_failure("HTTP 502"), "transient")
        self.assertEqual(self.wd._classify_failure("ConnectError"), "transient")

    def test_classify_401_as_auth(self):
        self.assertEqual(self.wd._classify_failure("HTTP 401"), "auth")
        self.assertEqual(self.wd._classify_failure("Unauthorized"), "auth")
        self.assertEqual(self.wd._classify_failure("invalid_token"), "auth")

    def test_classify_other(self):
        self.assertEqual(self.wd._classify_failure("something weird"), "other")

    # ── Snooze preservation ──────────────────────────────────────────────────

    def test_oauth_snooze_preserved_on_recovery(self):
        """The critical bug: recovery used to clear the snooze, causing the next
        transient failure to re-fire. Snooze must survive _record_success."""
        from datetime import datetime, timezone
        self.wd._last_alert["google_calendar"] = (
            datetime.now(timezone.utc).timestamp() + 22 * 3600
        )
        self.wd._save_alert_state()
        # Service recovers
        self.wd._record_success("google_calendar")
        # Snooze must still be in place
        self.assertIn("google_calendar", self.wd._last_alert)

    def test_non_oauth_alert_cleared_on_recovery_in_main_loop(self):
        """Non-OAuth checks DO clear their alert key on recovery so they can
        immediately re-alert on the next genuine outage (the main-loop branch)."""
        # This contract is enforced by run_watchdog_checks(), not by helpers.
        # Helper-level: _record_success does not touch _last_alert; main loop
        # calls _clear_alert(key) explicitly for non-OAuth keys.
        self.wd._last_alert["oura"] = 12345.0
        self.wd._record_success("oura")
        self.assertIn("oura", self.wd._last_alert)  # helper preserves
        self.wd._clear_alert("oura")
        self.assertNotIn("oura", self.wd._last_alert)  # main loop clears explicitly

    # ── End-to-end scenarios ─────────────────────────────────────────────────

    def test_single_transient_does_not_escalate(self):
        """Scenario: one tick of timeout. No alert. No counter at threshold."""
        with mock.patch.object(self.wd, "_post_oauth_escalation") as posted, \
             mock.patch.object(self.wd, "_slack_alert"), \
             mock.patch.object(self.wd, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            # All checks pass except google_calendar (one tick of timeout)
            patched_checks = []
            for key, label, fn in self.wd.CHECKS:
                if key == "google_calendar":
                    patched_checks.append((key, label, lambda: (False, "timed out")))
                else:
                    patched_checks.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched_checks):
                self.wd.run_watchdog_checks()
            posted.assert_not_called()
            self.assertEqual(self.wd._fail_counter.get("google_calendar"), 1)

    def test_three_transient_ticks_still_does_not_escalate(self):
        """Scenario: 3 ticks of transient timeout. Threshold met BUT classification
        is transient → no escalation. This is the 18:05 bug case."""
        with mock.patch.object(self.wd, "_post_oauth_escalation") as posted, \
             mock.patch.object(self.wd, "_slack_alert"), \
             mock.patch.object(self.wd, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            patched_checks = []
            for key, label, fn in self.wd.CHECKS:
                if key == "google_calendar":
                    patched_checks.append((key, label, lambda: (False, "timed out")))
                else:
                    patched_checks.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched_checks):
                for _ in range(5):  # well above threshold
                    self.wd.run_watchdog_checks()
            posted.assert_not_called()

    def test_three_auth_ticks_escalates_once(self):
        """Scenario: 3 ticks of 401. Threshold met + classification=auth → escalate."""
        with mock.patch.object(self.wd, "_post_oauth_escalation") as posted, \
             mock.patch.object(self.wd, "_slack_alert"), \
             mock.patch.object(self.wd, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            patched_checks = []
            for key, label, fn in self.wd.CHECKS:
                if key == "google_calendar":
                    patched_checks.append((key, label, lambda: (False, "HTTP 401")))
                else:
                    patched_checks.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched_checks):
                for _ in range(5):
                    self.wd.run_watchdog_checks()
            # Should escalate exactly once due to 24h snooze
            self.assertEqual(posted.call_count, 1)

    # ── System-wide classification gate (the rule, not the bandaid) ──────────

    def test_non_oauth_transient_never_escalates(self):
        """SYSTEM RULE: Telegram (non-OAuth) sustained transient timeouts —
        even past threshold — must never page Leo. The classification gate
        applies to every check, not just OAuth."""
        with mock.patch.object(self.wd, "_post_oauth_escalation") as posted, \
             mock.patch.object(self.wd, "_slack_alert") as slack, \
             mock.patch.object(self.wd, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            patched_checks = []
            for key, label, fn in self.wd.CHECKS:
                if key == "telegram":
                    patched_checks.append((key, label, lambda: (False, "timed out")))
                else:
                    patched_checks.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched_checks):
                for _ in range(5):  # well past threshold
                    self.wd.run_watchdog_checks()
            posted.assert_not_called()
            # No batched-failures Slack message either
            for call in slack.call_args_list:
                msg = call.args[1] if len(call.args) > 1 else call.kwargs.get("message", "")
                self.assertNotIn("Telegram", msg, "Transient should never reach Slack")

    def test_non_oauth_auth_failure_still_escalates(self):
        """SYSTEM RULE counterpart: real auth failures DO escalate after threshold,
        even for non-OAuth services. Classification gate allows 'auth' through."""
        with mock.patch.object(self.wd, "_post_oauth_escalation"), \
             mock.patch.object(self.wd, "_slack_alert") as slack, \
             mock.patch.object(self.wd, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            patched_checks = []
            for key, label, fn in self.wd.CHECKS:
                if key == "oura":
                    patched_checks.append((key, label, lambda: (False, "HTTP 401")))
                else:
                    patched_checks.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched_checks):
                for _ in range(3):  # hit threshold
                    self.wd.run_watchdog_checks()
            # Should have sent at least one Slack message mentioning Oura
            oura_msgs = [
                c for c in slack.call_args_list
                if "Oura" in (c.args[1] if len(c.args) > 1 else c.kwargs.get("message", ""))
            ]
            self.assertGreater(len(oura_msgs), 0, "Real auth failure should escalate")

    def test_auth_recovery_preserves_snooze(self):
        """Scenario: auth fail → escalate → recover → next transient must NOT re-fire."""
        with mock.patch.object(self.wd, "_post_oauth_escalation") as posted, \
             mock.patch.object(self.wd, "_slack_alert"), \
             mock.patch.object(self.wd, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            # Phase 1: 3 ticks of 401 → escalate
            patched_fail = []
            for key, label, fn in self.wd.CHECKS:
                if key == "google_calendar":
                    patched_fail.append((key, label, lambda: (False, "HTTP 401")))
                else:
                    patched_fail.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched_fail):
                for _ in range(3):
                    self.wd.run_watchdog_checks()
            self.assertEqual(posted.call_count, 1)
            # Phase 2: 1 tick of recovery — snooze preserved
            patched_ok = []
            for key, label, fn in self.wd.CHECKS:
                patched_ok.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched_ok):
                self.wd.run_watchdog_checks()
            self.assertIn("google_calendar", self.wd._last_alert)
            # Phase 3: 3 ticks of timeout — must not escalate (transient + snooze)
            with mock.patch.object(self.wd, "CHECKS", [
                (k, l, (lambda: (False, "timed out")) if k == "google_calendar" else (lambda: (True, "ok")))
                for k, l, _ in self.wd.CHECKS
            ]):
                for _ in range(3):
                    self.wd.run_watchdog_checks()
            self.assertEqual(posted.call_count, 1)


    # ── 5c4e94f4 additions ───────────────────────────────────────────────────

    def test_deferred_check_is_skipped(self):
        """A check in DEFERRED_CHECKS is skipped entirely — no counter, no
        escalation. This is why the mechanism tests clear DEFERRED_CHECKS:
        google_calendar is deferred in prod (n8n OAuth dead until S7-9)."""
        with mock.patch.object(self.wd, "DEFERRED_CHECKS", {"google_calendar": "deferred"}), \
             mock.patch.object(self.wd, "_post_oauth_escalation") as posted, \
             mock.patch.object(self.wd, "_slack_alert"), \
             mock.patch.object(self.wd, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            patched = []
            for key, label, fn in self.wd.CHECKS:
                if key == "google_calendar":
                    patched.append((key, label, lambda: (False, "HTTP 401")))
                else:
                    patched.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched):
                for _ in range(5):
                    self.wd.run_watchdog_checks()
            posted.assert_not_called()
            self.assertIsNone(self.wd._fail_counter.get("google_calendar"))

    def test_failures_page_without_slack_token(self):
        """5c4e94f4 landmine regression: a real failure must still page Leo when
        the RETIRED slack_bot_token is ABSENT. Paging routes through
        _slack_alert -> tg_alert -> notify gateway, which does not use that
        secret; gating the send on it (the old `if failures and token`) was a
        silent-death path the day the dead secret is removed."""
        def _no_slack_token(name):
            return "" if name == "slack_bot_token" else "fake-token"
        with mock.patch.object(self.wd, "_post_oauth_escalation"), \
             mock.patch.object(self.wd, "_slack_alert") as slack, \
             mock.patch.object(self.wd, "_load_secret", side_effect=_no_slack_token), \
             mock.patch.object(self.wd, "run_maintenance"), \
             mock.patch.object(self.wd, "run_gap_checks"), \
             mock.patch.object(self.wd, "check_container_warnings"), \
             mock.patch.object(self.wd, "archive_container_warnings"), \
             mock.patch.object(self.wd, "prune_archived_logs"):
            patched = []
            for key, label, fn in self.wd.CHECKS:
                if key == "oura":
                    patched.append((key, label, lambda: (False, "HTTP 401")))
                else:
                    patched.append((key, label, lambda: (True, "ok")))
            with mock.patch.object(self.wd, "CHECKS", patched):
                for _ in range(3):
                    self.wd.run_watchdog_checks()
            oura_msgs = [
                c for c in slack.call_args_list
                if "Oura" in (c.args[1] if len(c.args) > 1 else c.kwargs.get("message", ""))
            ]
            self.assertGreater(len(oura_msgs), 0,
                               "Failure must page even with slack_bot_token absent")


if __name__ == "__main__":
    unittest.main(verbosity=2)

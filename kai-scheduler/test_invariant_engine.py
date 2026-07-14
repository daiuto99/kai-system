"""S5-2 — Invariant engine: seeded violation + dedup Plane filing tests.

Proves:
1. Kill switch (INVARIANT_RUNNER_ENABLED=false) exits before running ANY check.
2. A seeded pass→fail transition is detected and Plane filing is attempted.
3. A second run with the same failure is deduped (Plane NOT filed again).
4. Recovery (fail→pass) clears the dedup, so the NEXT failure will refile.

Run on the worker:
    python3 ~/kai-system/kai-scheduler/test_invariant_engine.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))


def _fresh_invariants():
    """Import invariants with a temp RESULT_PATH and cleared module state."""
    import importlib
    if "invariants" in sys.modules:
        del sys.modules["invariants"]
    import invariants as inv  # type: ignore
    # Point RESULT_PATH to a temp file so tests don't touch /vault
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    inv.RESULT_PATH = Path(tmp.name)
    inv.VAULT_PATH = Path(tempfile.mkdtemp())
    # Ensure 00_System dir exists for next_action.json writes
    (inv.VAULT_PATH / "00_System").mkdir(parents=True, exist_ok=True)
    # Reset module state
    inv._prev_state.clear()
    inv._violation_issue_ids.clear()
    inv._violation_issue_refs.clear()
    inv._RUNNER_ENABLED = True
    return inv, Path(tmp.name)


class KillSwitchTests(unittest.TestCase):

    def test_kill_switch_skips_all_checks(self):
        """INVARIANT_RUNNER_ENABLED=false must exit before running any invariant."""
        inv, _ = _fresh_invariants()
        inv._RUNNER_ENABLED = False
        call_count = [0]

        def _spy_fn():
            call_count[0] += 1
            return True, "should not run"

        orig = inv.INVARIANTS
        inv.INVARIANTS = [("test_inv", "Test", _spy_fn)]
        try:
            ok, results = inv.run_invariants()
        finally:
            inv.INVARIANTS = orig

        self.assertTrue(ok)
        self.assertEqual(results, {})
        self.assertEqual(call_count[0], 0, "kill switch must prevent any invariant from running")


class PlaneApiHealthTests(unittest.TestCase):

    def setUp(self):
        self.inv, self.result_path = _fresh_invariants()

    def tearDown(self):
        try:
            self.result_path.unlink()
        except FileNotFoundError:
            pass

    def test_rejected_authenticated_probe_fails(self):
        """D15 regression: HTTP 401 is a failed auth probe, never a pass."""
        response = mock.Mock(status_code=401)
        with mock.patch.object(self.inv, "_load_secret", return_value="deliberately-bad"), \
             mock.patch.object(self.inv.httpx, "get", return_value=response):
            passed, detail = self.inv.inv_plane_api_health()

        self.assertFalse(passed)
        self.assertIn("401", detail)

    def test_authenticated_200_probe_passes_and_uses_token(self):
        response = mock.Mock(status_code=200)

        def fake_get(url, *, headers, timeout):
            self.assertEqual(headers, {"X-API-Key": "valid-test-token"})
            return response

        with mock.patch.object(self.inv, "_load_secret", return_value="valid-test-token"), \
             mock.patch.object(self.inv.httpx, "get", side_effect=fake_get):
            passed, detail = self.inv.inv_plane_api_health()

        self.assertTrue(passed)
        self.assertIn("authenticated", detail)


class NextActionGuardTests(unittest.TestCase):

    def setUp(self):
        self.inv, self.result_path = _fresh_invariants()
        self.path = self.inv.VAULT_PATH / "00_System" / "next_action.json"

    def tearDown(self):
        try:
            self.result_path.unlink()
        except FileNotFoundError:
            pass

    def test_from_memory_pointer_is_refused(self):
        self.path.write_text(json.dumps({
            "action": "remembered stale prose",
            "written_at": "2026-07-14T13:00:00+00:00",
        }))

        passed, detail = self.inv.inv_ledger_pointer_consistent()

        self.assertFalse(passed)
        self.assertIn("provenance", detail)

    def test_live_derived_open_pointer_passes(self):
        from datetime import datetime, timezone
        issue_id = "11111111-2222-4333-8444-555555555555"
        project_id = self.inv._KAI_PROJECT
        action = f"KAI-792 ({issue_id}, In Progress, urgent) — Board truth"
        self.path.write_text(json.dumps({
            "action": action,
            "written_at": datetime.now(timezone.utc).isoformat(),
            "source": "live_plane_readback",
            "issue_id": issue_id,
            "project_id": project_id,
        }))
        states = mock.Mock()
        states.text = json.dumps({
            "results": [{"id": "state-1", "name": "In Progress", "group": "started"}],
        })
        states.raise_for_status = mock.Mock()
        issue = mock.Mock()
        issue.text = json.dumps({
            "id": issue_id,
            "name": "Board truth",
            "state": "state-1",
            "priority": "urgent",
            "sequence_id": 792,
        })
        issue.raise_for_status = mock.Mock()

        with mock.patch.object(self.inv, "_load_secret", return_value="valid-test-token"), \
             mock.patch.object(self.inv.httpx, "get", side_effect=[states, issue]):
            passed, detail = self.inv.inv_ledger_pointer_consistent()

        self.assertTrue(passed)
        self.assertIn("KAI-792", detail)


class SeededViolationTests(unittest.TestCase):

    def setUp(self):
        self.inv, self.result_path = _fresh_invariants()

    def tearDown(self):
        try:
            self.result_path.unlink()
        except FileNotFoundError:
            pass

    def _make_checks(self, fail_key: str, fail_detail: str) -> list:
        checks = []
        for key, label, fn in self.inv.INVARIANTS:
            if key == fail_key:
                def _fail(d=fail_detail):
                    return False, d
                checks.append((key, label, _fail))
            else:
                def _pass():
                    return True, "ok"
                checks.append((key, label, _pass))
        return checks

    def test_seeded_failure_detected_on_transition(self):
        """Seed backup_integrity as passing, then failing: transition must be detected."""
        key = "backup_integrity"
        self.inv._prev_state[key] = True  # seed prior pass

        with mock.patch.object(self.inv, "_slack_post") as slack, \
             mock.patch.object(self.inv, "_file_invariant_issue") as plane_file, \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"):
            checks = self._make_checks(key, "seeded test failure — backup.log not found")
            with mock.patch.object(self.inv, "INVARIANTS", checks):
                ok, results = self.inv.run_invariants()

        self.assertFalse(ok)
        self.assertFalse(results[key]["pass"])
        self.assertEqual(results[key]["detail"], "seeded test failure — backup.log not found")
        # Transition detected → Slack fired
        slack.assert_called_once()
        slack_msg = slack.call_args[0][1]
        self.assertIn("Backup Integrity", slack_msg)
        # Plane filing attempted
        plane_file.assert_called_once_with(key, "Backup Integrity", "seeded test failure — backup.log not found")

    def test_first_observed_failure_gets_open_issue_mapping(self):
        """D15 regression: startup on red must map it even without a prior pass."""
        key = "backup_integrity"

        def _map_issue(k, label, detail):
            self.inv._violation_issue_ids[k] = 4242

        checks = self._make_checks(key, "already failing at process start")
        with mock.patch.object(self.inv, "_slack_post"), \
             mock.patch.object(self.inv, "_file_invariant_issue", side_effect=_map_issue) as plane_file, \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.inv, "INVARIANTS", checks):
            ok, results = self.inv.run_invariants()

        self.assertFalse(ok)
        self.assertFalse(results[key]["pass"])
        plane_file.assert_called_once_with(key, "Backup Integrity", "already failing at process start")
        data = json.loads(self.result_path.read_text())
        self.assertEqual(data["open_issue_ids"][key], 4242)

    def test_plane_filing_deduped_on_second_failure(self):
        """Same invariant failing twice: Plane must be filed exactly once (dedup)."""
        key = "backup_integrity"
        self.inv._prev_state[key] = True  # seed prior pass

        filed_count = [0]

        def _count_file(k, label, detail):
            filed_count[0] += 1
            self.inv._violation_issue_ids[k] = 9999  # simulate filed

        with mock.patch.object(self.inv, "_slack_post"), \
             mock.patch.object(self.inv, "_file_invariant_issue", side_effect=_count_file), \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"):
            checks = self._make_checks(key, "seeded failure run 1")
            with mock.patch.object(self.inv, "INVARIANTS", checks):
                self.inv.run_invariants()  # run 1: transition detected, filed
            # run 2: still failing, but deduped (key already in _violation_issue_ids)
            checks2 = self._make_checks(key, "seeded failure run 2")
            with mock.patch.object(self.inv, "INVARIANTS", checks2):
                self.inv.run_invariants()

        self.assertEqual(filed_count[0], 1, "Plane issue must be filed exactly once (dedup)")

    def test_recovery_clears_dedup_so_next_failure_refiles(self):
        """After recovery: dedup cleared, so next failure period refiles a new issue."""
        key = "backup_integrity"
        self.inv._prev_state[key] = True
        self.inv._violation_issue_ids[key] = 7777  # simulate open issue from prior period

        filed_count = [0]

        def _count_file(k, label, detail):
            filed_count[0] += 1
            self.inv._violation_issue_ids[k] = 8888

        with mock.patch.object(self.inv, "_slack_post"), \
             mock.patch.object(self.inv, "_file_invariant_issue", side_effect=_count_file), \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"):
            # Run 1: recovery (fail→pass) — clears dedup
            self.inv._prev_state[key] = False
            checks_pass = self._make_checks("__none__", "")  # all pass
            with mock.patch.object(self.inv, "INVARIANTS", checks_pass):
                self.inv.run_invariants()
            self.assertNotIn(key, self.inv._violation_issue_ids, "dedup must clear on recovery")

            # Run 2: new failure period — must refile
            self.inv._prev_state[key] = True
            checks_fail = self._make_checks(key, "new failure after recovery")
            with mock.patch.object(self.inv, "INVARIANTS", checks_fail):
                self.inv.run_invariants()

        self.assertEqual(filed_count[0], 1, "New failure after recovery must refile exactly once")

    def test_vault_write_includes_open_issue_ids(self):
        """invariants.json must include open_issue_ids for dashboard observability."""
        key = "backup_integrity"
        self.inv._prev_state[key] = True
        self.inv._violation_issue_ids[key] = 1234

        with mock.patch.object(self.inv, "_slack_post"), \
             mock.patch.object(self.inv, "_file_invariant_issue"), \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"):
            checks = self._make_checks(key, "test")
            with mock.patch.object(self.inv, "INVARIANTS", checks):
                self.inv.run_invariants()

        data = json.loads(self.result_path.read_text())
        self.assertIn("open_issue_ids", data)
        self.assertEqual(data["open_issue_ids"].get(key), 1234)


class NewInvariantSmokeTests(unittest.TestCase):
    """Smoke tests — confirm each new invariant returns (bool, str) without crashing."""

    def setUp(self):
        self.inv, _ = _fresh_invariants()
        # Seed a valid next_action.json in the temp vault
        na = self.inv.VAULT_PATH / "00_System" / "next_action.json"
        na.write_text(json.dumps({
            "action": "Test action for smoke test",
            "written_at": "2026-07-10T12:00:00Z",
        }))

    def _check_inv(self, fn_name: str):
        fn = getattr(self.inv, fn_name)
        result = fn()
        self.assertIsInstance(result, tuple, f"{fn_name} must return tuple")
        self.assertEqual(len(result), 2, f"{fn_name} must return 2-tuple")
        self.assertIsInstance(result[0], bool, f"{fn_name}[0] must be bool")
        self.assertIsInstance(result[1], str, f"{fn_name}[1] must be str")

    def test_inv_ledger_pointer_consistent(self):
        self._check_inv("inv_ledger_pointer_consistent")

    def test_inv_secret_files_permissions(self):
        self._check_inv("inv_secret_files_permissions")

    def test_inv_no_wp_password_in_vault_json(self):
        self._check_inv("inv_no_wp_password_in_vault_json")

    def test_inv_capability_transports_healthy(self):
        self._check_inv("inv_capability_transports_healthy")

    def test_inv_vault_backup_skip_manifest(self):
        self._check_inv("inv_vault_backup_skip_manifest")

    def test_inv_audit_log_integrity(self):
        self._check_inv("inv_audit_log_integrity")

    def test_inv_no_override_without_ack(self):
        # Seed a valid audit log with acknowledged entries
        audit = self.inv.VAULT_PATH / "00_System" / "capability_audit.jsonl"
        import json as _json
        from datetime import datetime, timezone
        audit.write_text(
            _json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "endpoint": "/test",
                "operator": "leo",
                "reason": "smoke test",
                "detail": {},
            }) + "\n"
        )
        self._check_inv("inv_no_override_without_ack")

    def test_inv_all_closed_issues_have_td(self):
        # No plane_api_token mounted in test env — must return (bool, str) gracefully
        self._check_inv("inv_all_closed_issues_have_td")

    def test_inv_session_saves_current(self):
        # Seed a valid session_close_log.json
        close_log = self.inv.VAULT_PATH / "00_System" / "session_close_log.json"
        from datetime import datetime, timezone
        close_log.write_text(
            json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "date": "2026-07-10",
            })
        )
        self._check_inv("inv_session_saves_current")

    def test_inv_workspace_sync_current(self):
        # Seed a valid git_activity.json with a recent commit
        git_log = self.inv.VAULT_PATH / "00_System" / "git_activity.json"
        from datetime import datetime, timezone
        git_log.write_text(
            json.dumps([{
                "hash": "abc1234",
                "message": "smoke test commit",
                "committed_at": datetime.now(timezone.utc).isoformat(),
            }])
        )
        self._check_inv("inv_workspace_sync_current")

    def test_d5_stale_job_remediation_readback(self):
        """D5 stale-job abandon must write+readback-verify stale_jobs.json."""
        ok, msg = self.inv._remediate_stale_jobs()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)
        if ok:
            marker = self.inv.VAULT_PATH / "00_System" / "stale_jobs.json"
            self.assertTrue(marker.exists(), "stale_jobs.json must exist after remediation")
            data = json.loads(marker.read_text())
            self.assertEqual(data["action"], "stale_job_abandon")
            self.assertFalse(data["consumed"])

    def test_d5_workspace_sync_trigger_readback(self):
        """D5 workspace re-sync trigger must write+readback-verify trigger file."""
        ok, msg = self.inv._remediate_workspace_sync_trigger()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)
        if ok:
            tf = self.inv.VAULT_PATH / "00_System" / "workspace_sync_trigger.json"
            self.assertTrue(tf.exists(), "workspace_sync_trigger.json must exist after remediation")
            data = json.loads(tf.read_text())
            self.assertEqual(data["action"], "request_workspace_resync")
            self.assertFalse(data["consumed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

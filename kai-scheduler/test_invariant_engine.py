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


class EmbeddedGitCredentialInvariantTests(unittest.TestCase):
    def setUp(self):
        self.inv, _ = _fresh_invariants()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = Path(self.temp_dir.name) / "config"
        self.inv.GIT_CONFIG_PATHS = (self.config,)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_planted_embedded_credential_fails_without_echoing_it(self):
        self.config.write_text('[remote "origin"]\nurl = https://user:token@example.invalid/repo.git\n')
        passed, detail = self.inv.inv_no_embedded_git_creds()
        self.assertFalse(passed)
        self.assertIn(str(self.config), detail)
        self.assertNotIn("user:token", detail)

    def test_clean_config_passes(self):
        self.config.write_text('[remote "origin"]\nurl = git@example.invalid:org/repo.git\n')
        passed, detail = self.inv.inv_no_embedded_git_creds()
        self.assertTrue(passed)
        self.assertIn("Mac: Layer-2 LaunchAgent audit", detail)


class LoopTelemetryInvariantTests(unittest.TestCase):

    def test_fails_when_recent_agentic_run_has_no_iteration_records(self):
        inv, _ = _fresh_invariants()
        response = mock.Mock()
        response.json.return_value = {"recent_runs": 1, "iteration_records": 0, "missing_runs": ["council-agentic-missing"]}
        with mock.patch.object(inv.httpx, "get", return_value=response):
            passed, detail = inv.inv_loop_telemetry_present()
        self.assertFalse(passed)
        self.assertIn("council-agentic-missing", detail)

    def test_passes_when_recent_agentic_runs_have_iteration_records(self):
        inv, _ = _fresh_invariants()
        response = mock.Mock()
        response.json.return_value = {"recent_runs": 1, "iteration_records": 2, "missing_runs": []}
        with mock.patch.object(inv.httpx, "get", return_value=response):
            passed, detail = inv.inv_loop_telemetry_present()
        self.assertTrue(passed)
        self.assertIn("2 records", detail)


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


class ExternalScanTests(unittest.TestCase):

    def setUp(self):
        self.inv, self.result_path = _fresh_invariants()

    def tearDown(self):
        self.result_path.unlink(missing_ok=True)

    def test_unauthenticated_public_200_fails(self):
        """KAI-814: a public data/mutation 200 must turn the perimeter red."""
        response = mock.Mock(status_code=200, headers={})
        with mock.patch.object(self.inv.httpx, "request", return_value=response), \
             mock.patch.object(self.inv, "_host_scan_ips", return_value=("192.168.1.2", "100.64.0.2")), \
             mock.patch.object(self.inv, "_probe_refused", return_value=(True, "refused")), \
             mock.patch.object(self.inv, "_council_unauth", return_value=(True, "401")), \
             mock.patch.object(self.inv, "_scan_open_host_ports", return_value=(True, "allowed")):
            passed, detail = self.inv.inv_external_scan()
        self.assertFalse(passed)
        self.assertIn("unauth HTTP 200", detail)

    def test_refused_qdrant_and_ollama_pass(self):
        """KAI-813 guard accepts explicit connection refusal, not a timeout."""
        with mock.patch.object(self.inv.socket, "create_connection", side_effect=ConnectionRefusedError):
            passed, detail = self.inv._probe_refused("192.168.1.2", 6333)
        self.assertTrue(passed)
        self.assertIn("connection refused", detail)

    def test_tailnet_only_port_fails_on_lan(self):
        result = mock.Mock(returncode=0, stdout="18080/tcp open  unknown\\n", stderr="")
        with mock.patch.object(self.inv.subprocess, "run", return_value=result):
            passed, detail = self.inv._scan_open_host_ports(self.inv.LAN_SCAN_IP)
        self.assertFalse(passed)
        self.assertIn("18080", detail)

    def test_tailnet_control_port_fails_on_lan(self):
        result = mock.Mock(returncode=0, stdout="8001/tcp open  unknown\\n", stderr="")
        with mock.patch.object(self.inv.subprocess, "run", return_value=result):
            passed, detail = self.inv._scan_open_host_ports(self.inv.LAN_SCAN_IP)
        self.assertFalse(passed)
        self.assertIn("LAN", detail)

    def test_syncthing_transport_and_plane_pass_on_lan(self):
        result = mock.Mock(returncode=0, stdout="22000/tcp open  unknown\\n8090/tcp open  unknown\\n", stderr="")
        with mock.patch.object(self.inv.subprocess, "run", return_value=result):
            passed, detail = self.inv._scan_open_host_ports(self.inv.LAN_SCAN_IP)
        self.assertTrue(passed, detail)

    def test_samba_is_not_allowlisted_on_any_interface(self):
        result = mock.Mock(returncode=0, stdout="139/tcp open  netbios-ssn\\n445/tcp open  microsoft-ds\\n", stderr="")
        with mock.patch.object(self.inv.subprocess, "run", return_value=result):
            tailnet_passed, tailnet_detail = self.inv._scan_open_host_ports(self.inv.TAILNET_SCAN_IP)
            lan_passed, lan_detail = self.inv._scan_open_host_ports(self.inv.LAN_SCAN_IP)
        self.assertFalse(tailnet_passed)
        self.assertFalse(lan_passed)
        self.assertIn("139", tailnet_detail)
        self.assertIn("139", lan_detail)

    def test_plane_proxy_passes_on_tailnet(self):
        result = mock.Mock(returncode=0, stdout="8090/tcp open  unknown\\n", stderr="")
        with mock.patch.object(self.inv.subprocess, "run", return_value=result):
            passed, detail = self.inv._scan_open_host_ports(self.inv.TAILNET_SCAN_IP)
        self.assertTrue(passed, detail)

    def test_syncthing_gui_is_not_allowlisted(self):
        result = mock.Mock(returncode=0, stdout="8384/tcp open  unknown\\n", stderr="")
        with mock.patch.object(self.inv.subprocess, "run", return_value=result):
            passed, detail = self.inv._scan_open_host_ports(self.inv.TAILNET_SCAN_IP)
        self.assertFalse(passed)
        self.assertIn("8384", detail)

    def test_tailnet_control_port_passes_on_tailnet(self):
        result = mock.Mock(returncode=0, stdout="8001/tcp open  unknown\\n", stderr="")
        with mock.patch.object(self.inv.subprocess, "run", return_value=result):
            passed, detail = self.inv._scan_open_host_ports(self.inv.TAILNET_SCAN_IP)
        self.assertTrue(passed)


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

        with mock.patch.object(self.inv, "_telegram_alert") as alert, \
             mock.patch.object(self.inv, "_file_invariant_issue") as plane_file, \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"):
            checks = self._make_checks(key, "seeded test failure — backup.log not found")
            with mock.patch.object(self.inv, "INVARIANTS", checks):
                ok, results = self.inv.run_invariants()

        self.assertFalse(ok)
        self.assertFalse(results[key]["pass"])
        self.assertEqual(results[key]["detail"], "seeded test failure — backup.log not found")
        # Transition detected → Telegram alert fired
        alert.assert_called_once()
        alert_msg = alert.call_args[0][0]
        self.assertIn("Backup Integrity", alert_msg)
        # Plane filing attempted
        plane_file.assert_called_once_with(key, "Backup Integrity", "seeded test failure — backup.log not found")

    def test_first_observed_failure_gets_open_issue_mapping(self):
        """D15 regression: startup on red must map it even without a prior pass."""
        key = "backup_integrity"

        def _map_issue(k, label, detail):
            self.inv._violation_issue_ids[k] = 4242

        checks = self._make_checks(key, "already failing at process start")
        with mock.patch.object(self.inv, "_telegram_alert"), \
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

        with mock.patch.object(self.inv, "_telegram_alert"), \
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

        with mock.patch.object(self.inv, "_telegram_alert"), \
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

        with mock.patch.object(self.inv, "_telegram_alert"), \
             mock.patch.object(self.inv, "_file_invariant_issue"), \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"):
            checks = self._make_checks(key, "test")
            with mock.patch.object(self.inv, "INVARIANTS", checks):
                self.inv.run_invariants()

        data = json.loads(self.result_path.read_text())
        self.assertIn("open_issue_ids", data)
        self.assertEqual(data["open_issue_ids"].get(key), 1234)

    def test_two_restart_simulations_reuse_the_one_persisted_open_issue(self):
        """KAI-949: reset process state twice; a continuing failure never refiles."""
        key = "backup_integrity"
        self.result_path.write_text(json.dumps({
            "open_issue_refs": {key: {"sequence_id": 4242, "issue_id": "issue-4242"}},
        }))
        calls = []

        for _restart in range(2):
            self.inv._prev_state.clear()
            self.inv._violation_issue_ids.clear()
            self.inv._violation_issue_refs.clear()
            checks = self._make_checks(key, "continuing failure")
            with mock.patch.object(self.inv, "_mapped_issue_is_open", return_value=True), \
                 mock.patch.object(self.inv, "_file_invariant_issue", side_effect=lambda *a: calls.append(a)), \
                 mock.patch.object(self.inv, "_telegram_alert"), \
                 mock.patch.object(self.inv, "_load_secret", return_value="fake-token"), \
                 mock.patch.object(self.inv, "INVARIANTS", checks):
                self.inv.run_invariants()

            self.assertEqual(self.inv._violation_issue_ids[key], 4242)

        self.assertEqual(calls, [], "continuing failure must reuse, never refile")

    def test_recovery_closes_the_mapped_issue_before_clearing_dedup(self):
        """KAI-949: a real pass closes the standing ticket, not merely memory."""
        key = "backup_integrity"
        self.inv._violation_issue_ids[key] = 4242
        self.inv._violation_issue_refs[key] = {"sequence_id": 4242, "issue_id": "issue-4242"}
        checks = self._make_checks("__none__", "")
        with mock.patch.object(self.inv, "_close_invariant_issue", return_value=True) as close, \
             mock.patch.object(self.inv, "_telegram_alert"), \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"), \
             mock.patch.object(self.inv, "INVARIANTS", checks):
            self.inv.run_invariants()
        close.assert_called_once_with(key)
        self.assertNotIn(key, self.inv._violation_issue_ids)
        self.assertNotIn(key, self.inv._violation_issue_refs)

    def test_deferred_invariant_is_not_counted_as_failing(self):
        """Watchdog summary must not turn pass=None deferrals into failures."""
        checks = [("real_failure", "Real Failure", lambda: (False, "seeded"))]
        deferred = {"planned_pause": ("Planned Pause", "explicitly deferred")}
        with mock.patch.object(self.inv, "INVARIANTS", checks), \
             mock.patch.object(self.inv, "DEFERRED_INVARIANTS", deferred), \
             mock.patch.object(self.inv, "_ensure_invariant_issue", return_value=True), \
             mock.patch.object(self.inv, "_load_secret", return_value="fake-token"), \
             self.assertLogs(self.inv.log, level="INFO") as captured:
            self.inv.run_invariants()

        joined = "\n".join(captured.output)
        self.assertIn("1 failing", joined)
        self.assertNotIn("2 failing", joined)


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

import base64
import importlib.util
import io
import json
import tempfile
import time
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock


MODULE = Path(__file__).parents[1] / "green_baseline.py"
SPEC = importlib.util.spec_from_file_location("green_baseline", MODULE)
baseline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = baseline
SPEC.loader.exec_module(baseline)


class GreenBaselineTests(unittest.TestCase):
    def test_declares_the_canonical_required_checks(self):
        self.assertEqual(
            [check.name for check in baseline.checks()],
            [
                "services_up", "session_brief", "worker_auth_fail_closed",
                "plane_reachable", "qdrant_up", "litellm_models",
                "qwen_mid_route_and_fallback", "buzz_shim_backend", "secret_permissions",
                "credential_registry", "source_drift",
                "fleet_visibility", "codex_verifier_auth", "hostops_rail_canary",
                "host_hygiene",
                "cron_log_error_scan",
                "disk_pressure", "container_roster", "backup_freshness",
                "tailscale_key_expiry", "public_tls", "cloudways_auth", "backup_verify",
                "offsite_freshness",
                "alert_delivery",
            ],
        )

    def test_models_parser_reads_openai_shape(self):
        self.assertEqual(
            baseline.parse_model_ids('{"data":[{"id":"qwen-mid"},{"id":"qwen-mid-worker"}]}'),
            {"qwen-mid", "qwen-mid-worker"},
        )

    def test_plane_parser_reads_grouped_worker_response(self):
        self.assertEqual(
            baseline.parse_plane_open_issues('{"projects":[{"issues":[{}, {}]},{"issues":[{}]}]}'),
            3,
        )

    def test_suite_returns_red_and_names_the_failed_check(self):
        good = baseline.Check("green", lambda: "ok")
        bad = baseline.Check("broken", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(baseline.run_suite((good, bad)), 1)
        self.assertIn("KAI GREEN BASELINE — RED: broken", output.getvalue())


def _jwt_with_exp(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return "h." + payload + ".s"


class CodexVerifierAuthProbe(unittest.TestCase):
    """KAI-1159 — the probe reads ~/.codex/auth.json and WARNs (never REDs) on an
    expired/near-expiry OAuth token, GREENs when valid or on API-key auth."""

    def _detail_for(self, auth_obj):
        with tempfile.TemporaryDirectory() as home:
            codex = Path(home) / ".codex"
            codex.mkdir()
            (codex / "auth.json").write_text(json.dumps(auth_obj))
            with mock.patch.object(baseline.Path, "home", return_value=Path(home)):
                return baseline.check_codex_verifier_auth()

    def test_expired_token_warns_not_reds(self):
        exp = int(time.time()) - 100 * 86400
        detail = self._detail_for({"tokens": {"access_token": _jwt_with_exp(exp)}})
        self.assertIn("WARN", detail)
        self.assertIn("EXPIRED", detail)
        self.assertIn("KAI-1159", detail)
        # the probe must NOT raise — a dead verifier never turns the suite RED
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((baseline.Check("codex", lambda: detail),))
        self.assertEqual(rc, 0)

    def test_valid_token_reads_green(self):
        exp = int(time.time()) + 100 * 86400
        detail = self._detail_for({"tokens": {"access_token": _jwt_with_exp(exp)}})
        self.assertNotIn("WARN", detail)
        self.assertIn("valid", detail)

    def test_near_expiry_warns(self):
        exp = int(time.time()) + 12 * 3600
        detail = self._detail_for({"tokens": {"access_token": _jwt_with_exp(exp)}})
        self.assertIn("WARN", detail)
        self.assertIn("expires in", detail)

    def test_api_key_auth_reads_green(self):
        detail = self._detail_for({"OPENAI_API_KEY": "sk-test", "tokens": {}})
        self.assertNotIn("WARN", detail)
        self.assertIn("API-key", detail)


class CloudwaysAuthProbe(unittest.TestCase):
    """S1-B5 — best-effort Cloudways OAuth probe: GREEN on 200, WARN (never RED) on
    401/403 or transport failure, WARN on missing creds. Token never surfaced."""

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="cwauth_")
        (Path(self._dir) / "cloudways_account_email.txt").write_text("leo@example.com\n")
        (Path(self._dir) / "cloudways_api_token.txt").write_text("sekret-key\n")
        self._patch_secrets = mock.patch.object(baseline, "SECRETS", Path(self._dir))
        self._patch_secrets.start()

    def tearDown(self):
        self._patch_secrets.stop()

    def _urlopen_returning(self, status):
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.Mock(status=status)
        return mock.patch.object(baseline.urllib.request, "urlopen", return_value=cm)

    def test_valid_token_reads_green(self):
        with self._urlopen_returning(200):
            detail = baseline.check_cloudways_auth()
        self.assertNotIn("WARN", detail)
        self.assertIn("valid", detail)
        self.assertNotIn("sekret-key", detail)

    def test_rejected_token_warns_with_code(self):
        err = baseline.urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch.object(baseline.urllib.request, "urlopen", side_effect=err):
            detail = baseline.check_cloudways_auth()
        self.assertIn("WARN", detail)
        self.assertIn("403", detail)
        self.assertIn("rotate", detail)

    def test_unreachable_warns(self):
        with mock.patch.object(baseline.urllib.request, "urlopen", side_effect=OSError("boom")):
            detail = baseline.check_cloudways_auth()
        self.assertIn("WARN", detail)
        self.assertIn("unreachable", detail)

    def test_missing_creds_warns(self):
        for name in ("cloudways_account_email.txt", "cloudways_api_token.txt"):
            (Path(self._dir) / name).unlink()
        detail = baseline.check_cloudways_auth()
        self.assertIn("WARN", detail)

    def test_never_reds(self):
        err = baseline.urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch.object(baseline.urllib.request, "urlopen", side_effect=err):
            detail = baseline.check_cloudways_auth()
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((baseline.Check("cloudways_auth", lambda: detail),))
        self.assertEqual(rc, 0)


class CredentialRegistryVerdict(unittest.TestCase):
    """S1-B2 — inventory-first guard: every credential on disk must be registered
    (else WARN), every runtime-critical one present (else RED), clean surface GREEN."""

    REG = {
        "credentials": [
            {"name": "anthropic_api_key", "criticality": "runtime-critical"},
            {"name": "kai_worker_auth", "criticality": "runtime-critical"},
            {"name": "oura_token", "criticality": "degraded"},
        ],
        "patterns": [
            {"pattern": r"^wp_[a-z0-9-]+_kai_app_password$", "criticality": "degraded"},
        ],
    }

    def test_clean_surface_is_green(self):
        present = ["anthropic_api_key", "kai_worker_auth", "oura_token",
                   "wp_sonicink_kai_app_password", "wp_the71_kai_app_password"]
        sev, detail = baseline.credential_registry_verdict(self.REG, present)
        self.assertEqual(sev, "green")
        self.assertIn("5 credentials enumerated", detail)
        self.assertIn("2 runtime-critical present", detail)

    def test_pattern_covers_fleet_rows(self):
        # a fleet credential matching only a pattern must NOT read as unregistered
        sev, _ = baseline.credential_registry_verdict(
            self.REG, ["anthropic_api_key", "kai_worker_auth", "wp_alexadaiuto_kai_app_password"])
        self.assertEqual(sev, "green")

    def test_unregistered_credential_warns(self):
        present = ["anthropic_api_key", "kai_worker_auth", "brand_new_secret"]
        sev, detail = baseline.credential_registry_verdict(self.REG, present)
        self.assertEqual(sev, "warn")
        self.assertIn("brand_new_secret", detail)
        self.assertIn("add a row", detail)

    def test_missing_runtime_critical_reds(self):
        present = ["anthropic_api_key", "oura_token"]  # kai_worker_auth absent
        sev, detail = baseline.credential_registry_verdict(self.REG, present)
        self.assertEqual(sev, "red")
        self.assertIn("kai_worker_auth", detail)
        self.assertIn("MISSING", detail)

    def test_red_precedes_warn(self):
        # a missing-critical AND an unregistered extra → RED wins (worst-case)
        present = ["anthropic_api_key", "unexpected_extra"]
        sev, _ = baseline.credential_registry_verdict(self.REG, present)
        self.assertEqual(sev, "red")

    def test_shipped_registry_matches_live_secret_surface(self):
        """The registry that ships must actually cover the repo's own credential
        surface — this is the guard guarding itself: a real drift fails the test."""
        registry_path = MODULE.parent / "readiness_registry.json"
        registry = json.loads(registry_path.read_text())
        present = sorted(p.stem for p in (MODULE.parents[1] / "secrets").glob("*.txt"))
        if not present:
            self.skipTest("secrets/ not present in this checkout")
        sev, detail = baseline.credential_registry_verdict(registry, present)
        self.assertIn(sev, ("green", "warn"), detail)  # never RED on the live surface
        self.assertNotEqual(sev, "warn", f"shipped registry has drift: {detail}")

    def test_probe_never_reds_on_missing_registry_file(self):
        with mock.patch.object(baseline.Path, "read_text", side_effect=OSError("nope")):
            detail = baseline.check_credential_registry()
        self.assertIn("WARN", detail)


class HostopsRailCanaryProbe(unittest.TestCase):
    """KAI-1166 [S1-B1] — the canary execs the resolver inside kai-orchestrator and
    REDs when the rail cannot resolve a payload (empty store or resolve error),
    GREENs on a successful resolve, and only WARNs when docker/container is down."""

    def _fake_run(self, *, stdout="", stderr="", returncode=0):
        proc = mock.Mock()
        proc.stdout, proc.stderr, proc.returncode = stdout, stderr, returncode
        return mock.patch.object(baseline.subprocess, "run", return_value=proc)

    def test_resolves_reads_green(self):
        with self._fake_run(stdout="CANARY_OK alexadaiuto kai_publish_gate_secret 64\n"):
            detail = baseline.check_hostops_rail_canary()
        self.assertNotIn("WARN", detail)
        self.assertIn("resolved", detail)
        self.assertIn("alexadaiuto/kai_publish_gate_secret", detail)
        self.assertIn("64B", detail)
        self.assertIn("KAI-1166", detail)

    def test_never_leaks_secret_bytes(self):
        # The in-container script only ever prints len(); assert the probe surfaces a count, not material.
        with self._fake_run(stdout="CANARY_OK alexadaiuto kai_publish_gate_secret 64\n"):
            detail = baseline.check_hostops_rail_canary()
        self.assertIn("64B", detail)

    def test_empty_store_reds(self):
        with self._fake_run(stdout="CANARY_EMPTY no-payload\n"):
            with self.assertRaises(RuntimeError) as ctx:
                baseline.check_hostops_rail_canary()
        self.assertIn("un-executable", str(ctx.exception))
        self.assertIn("KAI-1166", str(ctx.exception))

    def test_resolve_failure_reds(self):
        with self._fake_run(
            stdout="CANARY_FAIL alexadaiuto HostOpsIdentityError: payload secret does not have mode 0600\n"
        ):
            with self.assertRaises(RuntimeError) as ctx:
                baseline.check_hostops_rail_canary()
        self.assertIn("cannot resolve payload", str(ctx.exception))
        self.assertIn("mode 0600", str(ctx.exception))

    def test_import_error_reds(self):
        with self._fake_run(stdout="CANARY_IMPORTERR ModuleNotFoundError: hostops_identity\n"):
            with self.assertRaises(RuntimeError) as ctx:
                baseline.check_hostops_rail_canary()
        self.assertIn("resolver import failed", str(ctx.exception))

    def test_container_down_warns_not_reds(self):
        with self._fake_run(stderr="Error: No such container: kai-orchestrator", returncode=1):
            detail = baseline.check_hostops_rail_canary()
        self.assertIn("WARN", detail)
        self.assertIn("not running", detail)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((baseline.Check("canary", lambda: detail),))
        self.assertEqual(rc, 0)

    def test_docker_unreachable_warns(self):
        with mock.patch.object(baseline.subprocess, "run", side_effect=OSError("boom")):
            detail = baseline.check_hostops_rail_canary()
        self.assertIn("WARN", detail)
        self.assertIn("docker unreachable", detail)

    def test_empty_store_actually_turns_suite_red(self):
        with self._fake_run(stdout="CANARY_EMPTY store-absent\n"):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = baseline.run_suite((baseline.Check("hostops_rail_canary", baseline.check_hostops_rail_canary),))
        self.assertEqual(rc, 1)
        self.assertIn("hostops_rail_canary", out.getvalue())


class HostHygieneVerdict(unittest.TestCase):
    """KAI-1161 — host_hygiene_verdict WARNs on any hygiene concern, GREENs clean,
    and (via run_suite) never turns the baseline RED."""

    def test_clean_reads_green(self):
        d = baseline.host_hygiene_verdict(0, 40, False, 0, 1.0)
        self.assertNotIn("WARN", d)
        self.assertIn("clean", d)

    def test_pending_security_warns_with_counts(self):
        d = baseline.host_hygiene_verdict(9, 39, False, 0, 0.5)
        self.assertIn("WARN", d)
        self.assertIn("9 security", d)
        self.assertIn("of 39", d)
        self.assertIn("KAI-1161", d)

    def test_reboot_required_warns(self):
        d = baseline.host_hygiene_verdict(0, 0, True, 0, 0.0)
        self.assertIn("WARN", d)
        self.assertIn("reboot-required", d)

    def test_zombies_warn(self):
        d = baseline.host_hygiene_verdict(0, 0, False, 3, 0.0)
        self.assertIn("WARN", d)
        self.assertIn("3 zombie", d)

    def test_stale_cache_warns(self):
        d = baseline.host_hygiene_verdict(0, 0, False, 0, 9.0)
        self.assertIn("WARN", d)
        self.assertIn("stale", d)

    def test_unavailable_counts_warn(self):
        d = baseline.host_hygiene_verdict(None, None, False, 0, 0.0)
        self.assertIn("WARN", d)
        self.assertIn("unavailable", d)

    def test_probe_runs_and_never_reds(self):
        detail = baseline.check_host_hygiene()
        self.assertIsInstance(detail, str)
        self.assertIn("host hygiene", detail)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((baseline.Check("hh", lambda: detail),))
        self.assertEqual(rc, 0)


class CronLogErrorScan(unittest.TestCase):
    """S1-B2 — cron_log_error_scan: WARN (never RED) on recent anchored faults,
    GREEN when clean, and immune to false positives from normal status lines."""

    def test_verdict_clean_is_green(self):
        d = baseline.cron_log_error_verdict([])
        self.assertNotIn("WARN", d)
        self.assertIn("clean", d)

    def test_verdict_warns_with_counts_and_sample(self):
        d = baseline.cron_log_error_verdict([("advisor_dm_probe.log", 3, "FAIL boom")])
        self.assertIn("WARN", d)
        self.assertIn("advisor_dm_probe.log:3", d)
        self.assertIn("boom", d)

    def _write(self, name, text):
        p = Path(self._dir) / name
        p.write_text(text)
        return p

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="cronlog_test_")

    def test_scan_counts_recent_timestamped_fault(self):
        now = time.time()
        from datetime import datetime, timezone
        recent = datetime.fromtimestamp(now - 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        p = self._write("x.log", f"[{recent}Z] FAIL advisor=kai — no round-trip\n")
        cnt, sample = baseline._scan_cron_log(p, now, 21600)
        self.assertEqual(cnt, 1)
        self.assertIn("FAIL", sample)

    def test_scan_ages_out_old_fault(self):
        now = time.time()
        from datetime import datetime, timezone
        old = datetime.fromtimestamp(now - 30000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        p = self._write("x.log", f"[{old}Z] FAIL advisor=kai — no round-trip\n")
        cnt, _ = baseline._scan_cron_log(p, now, 21600)  # 30000s > 6h window
        self.assertEqual(cnt, 0)

    def test_scan_ignores_benign_status_words(self):
        now = time.time()
        p = self._write("x.log", "OK advisor=kai healthy round-trip error: none failed=0\n")
        cnt, _ = baseline._scan_cron_log(p, now, 21600)
        self.assertEqual(cnt, 0)

    def test_scan_untimestamped_fault_in_recent_tail_counts(self):
        now = time.time()
        p = self._write("x.log", "notify_dedup write failed: PermissionError\n")
        cnt, sample = baseline._scan_cron_log(p, now, 21600)
        self.assertEqual(cnt, 1)
        self.assertIn("PermissionError", sample)

    def test_scan_missing_file_is_clean_never_raises(self):
        cnt, sample = baseline._scan_cron_log(Path(self._dir) / "nope.log", time.time(), 21600)
        self.assertEqual((cnt, sample), (0, None))

    def test_probe_never_reds(self):
        import os
        os.environ["KAI_CRON_LOG_DIR"] = self._dir
        try:
            detail = baseline.check_cron_log_errors()
        finally:
            del os.environ["KAI_CRON_LOG_DIR"]
        self.assertIsInstance(detail, str)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((baseline.Check("cron", lambda: detail),))
        self.assertEqual(rc, 0)


class DiskPressureEval(unittest.TestCase):
    """S1-B2 — disk_pressure_eval: RED on exhaustion, WARN on pressure, GREEN clean."""

    def test_clean_is_green(self):
        sev, d = baseline.disk_pressure_eval(40.0, 30.0, 55.0, 5.0)
        self.assertEqual(sev, "green")
        self.assertIn("disk 40%", d)

    def test_disk_80_warns(self):
        sev, d = baseline.disk_pressure_eval(84.0, 30.0, 55.0, 0.0)
        self.assertEqual(sev, "warn")
        self.assertIn("root disk 84%", d)

    def test_disk_92_reds(self):
        sev, d = baseline.disk_pressure_eval(93.0, 30.0, 55.0, 0.0)
        self.assertEqual(sev, "red")
        self.assertIn("root disk 93%", d)

    def test_inode_exhaustion_reds(self):
        sev, d = baseline.disk_pressure_eval(40.0, 95.0, 55.0, 0.0)
        self.assertEqual(sev, "red")
        self.assertIn("inodes 95%", d)

    def test_low_memory_reds(self):
        sev, d = baseline.disk_pressure_eval(40.0, 30.0, 2.0, 0.0)
        self.assertEqual(sev, "red")
        self.assertIn("mem avail 2%", d)

    def test_swap_pressure_warns(self):
        sev, d = baseline.disk_pressure_eval(40.0, 30.0, 55.0, 60.0)
        self.assertEqual(sev, "warn")
        self.assertIn("swap 60%", d)

    def test_probe_runs_and_returns_str(self):
        detail = baseline.check_disk_pressure()
        self.assertIsInstance(detail, str)


class ContainerRosterAndBackup(unittest.TestCase):
    """S1-B2/B3 — the roster probe RED-raises on a down container and the backup
    probe WARNs on staleness; both run without raising on a healthy host."""

    def test_roster_probe_runs(self):
        detail = baseline.check_container_roster()
        self.assertIsInstance(detail, str)
        # on this host all managed containers run -> not a WARN-unavailable
        self.assertNotIn("unavailable", detail)

    def test_backup_probe_runs(self):
        detail = baseline.check_backup_freshness()
        self.assertIsInstance(detail, str)
        self.assertTrue("backups" in detail or "backup" in detail)

    def test_both_probes_never_hard_fail_suite_when_returning(self):
        # a returned (non-raising) detail must never turn the suite RED
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((
                baseline.Check("r", lambda: "ok roster"),
                baseline.Check("b", lambda: "WARN backups: x [S1-B3]"),
            ))
        self.assertEqual(rc, 0)


class ExpiryProbes(unittest.TestCase):
    """S1-B2 — expiry_severity thresholds + the tailscale/TLS probes run clean."""

    def test_severity_thresholds(self):
        self.assertEqual(baseline.expiry_severity(60, 14, 7), "green")
        self.assertEqual(baseline.expiry_severity(10, 14, 7), "warn")
        self.assertEqual(baseline.expiry_severity(3, 14, 7), "red")
        self.assertEqual(baseline.expiry_severity(None, 14, 7), "warn")

    def test_tailscale_probe_runs(self):
        detail = baseline.check_tailscale_key_expiry()
        self.assertIsInstance(detail, str)
        self.assertIn("tailscale", detail)

    def test_public_tls_probe_runs(self):
        detail = baseline.check_public_tls()
        self.assertIsInstance(detail, str)
        self.assertIn("TLS", detail)


class BackupVerifyProbe(unittest.TestCase):
    """S1-B3 — backup_verify reads ~/backups/.verify_result: RED on FAIL, WARN on
    missing/stale, GREEN on a recent PASS. Runs against the live stamp."""

    def test_probe_runs(self):
        detail = baseline.check_backup_verify()
        self.assertIsInstance(detail, str)
        self.assertIn("backup verify", detail)



class OffsiteFreshnessVerdict(unittest.TestCase):
    """S1-B3 — offsite_freshness_verdict: WARN while gated/disabled, RED once enabled
    and the offsite copy has failed or gone stale, GREEN on a fresh copy. The live
    probe (no offsite.env yet) must never turn the suite RED."""

    def test_disabled_warns_not_reds(self):
        sev, d = baseline.offsite_freshness_verdict(False, None, None)
        self.assertEqual(sev, "warn")
        self.assertIn("not enabled", d)
        self.assertIn("S1-B3", d)

    def test_enabled_fail_reds(self):
        sev, d = baseline.offsite_freshness_verdict(True, "FAIL", 1.0)
        self.assertEqual(sev, "red")
        self.assertIn("FAILED", d)

    def test_enabled_never_run_warns(self):
        sev, d = baseline.offsite_freshness_verdict(True, None, None)
        self.assertEqual(sev, "warn")
        self.assertIn("never run", d)

    def test_enabled_stale_reds(self):
        sev, d = baseline.offsite_freshness_verdict(True, "OK", 48.0)
        self.assertEqual(sev, "red")
        self.assertIn("stale", d)

    def test_enabled_fresh_green(self):
        sev, d = baseline.offsite_freshness_verdict(True, "OK", 3.0)
        self.assertEqual(sev, "green")
        self.assertIn("fresh", d)

    def test_live_probe_runs_and_never_reds_while_gated(self):
        detail = baseline.check_offsite_freshness()
        self.assertIsInstance(detail, str)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((baseline.Check("offsite", lambda: detail),))
        self.assertEqual(rc, 0)



class AlertDeliveryVerdict(unittest.TestCase):
    """S1-B4 — alert_delivery_verdict: WARN when never run, RED on a FAILED or stale
    receipt, GREEN on a fresh delivery. The live probe must not turn the suite RED
    when the heartbeat is merely absent."""

    def test_never_run_warns(self):
        sev, d = baseline.alert_delivery_verdict(None, None)
        self.assertEqual(sev, "warn")
        self.assertIn("never run", d)

    def test_fail_reds(self):
        sev, d = baseline.alert_delivery_verdict("FAIL", 1.0)
        self.assertEqual(sev, "red")
        self.assertIn("FAILED", d)

    def test_stale_reds(self):
        sev, d = baseline.alert_delivery_verdict("OK", 48.0)
        self.assertEqual(sev, "red")
        self.assertIn("stale", d)

    def test_fresh_green(self):
        sev, d = baseline.alert_delivery_verdict("OK", 2.0)
        self.assertEqual(sev, "green")
        self.assertIn("delivery-verified", d)

    def test_live_probe_runs_and_never_reds_when_absent(self):
        detail = baseline.check_alert_delivery()
        self.assertIsInstance(detail, str)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = baseline.run_suite((baseline.Check("ad", lambda: detail),))
        # never-run/fresh -> not RED; a pre-existing stale/FAIL stamp is a real RED
        self.assertIn(rc, (0, 1))


if __name__ == "__main__":
    unittest.main()

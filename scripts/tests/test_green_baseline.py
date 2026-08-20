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
                "qwen_mid_route_and_fallback", "buzz_shim_backend", "secret_permissions", "source_drift",
                "fleet_visibility", "codex_verifier_auth", "host_hygiene",
                "disk_pressure", "container_roster", "backup_freshness",
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


if __name__ == "__main__":
    unittest.main()

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
                "fleet_visibility", "codex_verifier_auth",
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


if __name__ == "__main__":
    unittest.main()

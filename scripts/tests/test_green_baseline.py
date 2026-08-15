import importlib.util
import io
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout


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
                "fleet_visibility",
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


if __name__ == "__main__":
    unittest.main()

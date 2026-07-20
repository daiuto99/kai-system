import unittest
from types import SimpleNamespace
from unittest import mock

from models import CapabilityResult
from workflows.wordpress_publish_homepage import PublishHomepageWorkflow


class HomepageOverwriteGuardTests(unittest.TestCase):
    def _workflow(self):
        return PublishHomepageWorkflow("job-homepage-overwrite-guard")

    def _context(self, expected=11):
        return {
            "site": "sette-uno.com",
            "creds": {"fqdn": "example.test", "app_password": "not-a-secret"},
            "expected_current_homepage_id": expected,
        }

    def _front_page(self, page_id, show_on_front="page"):
        return CapabilityResult(ok=True, status="succeeded", data={
            "show_on_front": show_on_front, "page_on_front": page_id,
        })

    def test_matching_predecessor_proceeds_without_gate(self):
        gate = mock.Mock()
        with mock.patch("capabilities.get_capability", side_effect=lambda name: (
            (lambda **_: self._front_page(11)) if name == "wordpress.get_front_page" else gate
        )):
            result = self._workflow()._step_precheck_homepage_overwrite(
                self._context(expected=11), {"id": "step-match"}
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.verification["evidence"]["decision"], "expected_predecessor_matches")
        gate.assert_not_called()

    def test_mismatched_predecessor_requires_explicit_human_gate(self):
        gate = mock.Mock(return_value=CapabilityResult(
            ok=True, status="awaiting_gate", data={"gate_id": "gate-mismatch"},
            verification={"verified": False},
        ))
        with mock.patch("capabilities.get_capability", side_effect=lambda name: (
            (lambda **_: self._front_page(11)) if name == "wordpress.get_front_page" else gate
        )):
            result = self._workflow()._step_precheck_homepage_overwrite(
                self._context(expected=5), {"id": "step-mismatch"}
            )

        self.assertEqual(result.status, "awaiting_gate")
        brief = gate.call_args.kwargs["brief"]
        self.assertEqual(gate.call_args.kwargs["gate_type"], "homepage_overwrite")
        self.assertEqual(brief["overwrite_guard"]["expected_current_homepage_id"], 5)
        self.assertEqual(brief["overwrite_guard"]["live_page_on_front"], 11)
        self.assertIn("explicit human confirmation", brief["overwrite_guard"]["required_decision"])

    def test_missing_expected_id_fails_safe_to_explicit_human_gate(self):
        gate = mock.Mock(return_value=CapabilityResult(
            ok=True, status="awaiting_gate", data={"gate_id": "gate-missing"},
            verification={"verified": False},
        ))
        with mock.patch("capabilities.get_capability", side_effect=lambda name: (
            (lambda **_: self._front_page(11)) if name == "wordpress.get_front_page" else gate
        )):
            result = self._workflow()._step_precheck_homepage_overwrite(
                self._context(expected=None), {"id": "step-missing"}
            )

        self.assertEqual(result.status, "awaiting_gate")
        self.assertIsNone(gate.call_args.kwargs["brief"]["overwrite_guard"]["expected_current_homepage_id"])

    def test_no_existing_page_front_proceeds_without_expected_id(self):
        gate = mock.Mock()
        with mock.patch("capabilities.get_capability", side_effect=lambda name: (
            (lambda **_: self._front_page(None, show_on_front="posts")) if name == "wordpress.get_front_page" else gate
        )):
            result = self._workflow()._step_precheck_homepage_overwrite(
                self._context(expected=None), {"id": "step-no-page"}
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.verification["evidence"]["decision"], "safe_no_existing_front_page")
        gate.assert_not_called()


class FrontPageCapabilityTests(unittest.TestCase):
    def test_get_front_page_reads_wp_settings(self):
        from capabilities.wordpress import get_front_page
        response = SimpleNamespace(
            ok=True, data={"show_on_front": "page", "page_on_front": 11},
            status_code=200, body_preview="",
        )
        with mock.patch("capabilities.wordpress.safe_request", return_value=response) as request:
            result = get_front_page("sette-uno.com", {"fqdn": "example.test", "app_password": "pw"})

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"show_on_front": "page", "page_on_front": 11})
        self.assertEqual(request.call_args.args[0], "GET")
        self.assertIn("/wp-json/wp/v2/settings", request.call_args.args[1])


if __name__ == "__main__":
    unittest.main()

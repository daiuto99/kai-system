"""WP AR-1 gap2 — the creative gate must review REAL material, not rubber-stamp.

Before this fix, load_brief returned an empty brief when no brief_path was given
and the creative gate's vault_brief was therefore empty — the chain approved
creative it never saw. These lock the fix: load_brief auto-loads the property's
approved brand brief (BUILD_PROFILE.md) into a dedicated review_brief, the page
content path (brief_text) is left untouched, and the gate surfaces provenance so
an ungoverned (empty) review is visible instead of silent.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models import CapabilityResult
from workflows.wordpress_build_page_draft import BuildPageDraftWorkflow


class LoadBriefAutoload(unittest.TestCase):
    def _wf(self):
        return BuildPageDraftWorkflow("job-gap2")

    def test_autoloads_approved_brief_into_review_brief(self):
        with tempfile.TemporaryDirectory() as d:
            prof = Path(d) / "BUILD_PROFILE.md"
            prof.write_text("# the71c brand brief\nvoice: warm")
            with mock.patch("brand_profile.profile_path", return_value=prof):
                r = self._wf()._step_load_brief({"site": "the71c"})
        self.assertTrue(r.ok)
        # gate reviews the real brief...
        self.assertEqual(r.data["review_brief"], "# the71c brand brief\nvoice: warm")
        self.assertTrue(r.data["brief_source"].startswith("auto:build_profile"))
        # ...but the page-content fallback (brief_text) is NOT polluted by it.
        self.assertEqual(r.data["brief_text"], "")

    def test_unreadable_brief_fails_open_not_crash(self):
        # A present-but-unreadable profile must NOT crash the governed workflow.
        boom = mock.Mock()
        boom.exists.return_value = True
        boom.read_text.side_effect = PermissionError("denied")
        with mock.patch("brand_profile.profile_path", return_value=boom):
            r = self._wf()._step_load_brief({"site": "the71c"})
        self.assertTrue(r.ok)
        self.assertEqual(r.data["review_brief"], "")
        self.assertEqual(r.data["brief_source"], "none:brief_unreadable")

    def test_ungoverned_property_surfaces_empty_not_silent(self):
        missing = Path("/nonexistent/BUILD_PROFILE.md")
        with mock.patch("brand_profile.profile_path", return_value=missing):
            r = self._wf()._step_load_brief({"site": "no-such-site"})
        self.assertTrue(r.ok)
        self.assertEqual(r.data["review_brief"], "")
        self.assertEqual(r.data["brief_source"], "none:no_profile")

    def test_explicit_brief_path_still_wins(self):
        with tempfile.TemporaryDirectory() as d:
            bp = Path(d) / "brief.md"
            bp.write_text("authored brief")
            r = self._wf()._step_load_brief({"site": "the71c", "brief_path": str(bp)})
        self.assertEqual(r.data["brief_text"], "authored brief")
        self.assertEqual(r.data["review_brief"], "authored brief")
        self.assertTrue(r.data["brief_source"].startswith("inputs:"))


class CreativeGateReviewsMaterial(unittest.TestCase):
    def _gate_brief(self, ctx):
        captured = {}

        def fake_gate(**kwargs):
            captured.update(kwargs)
            return CapabilityResult(ok=True, status="awaiting_gate", data={"gate_id": "g"})

        wf = BuildPageDraftWorkflow("job-gap2-gate")
        with mock.patch("capabilities.get_capability", return_value=fake_gate):
            wf._run_gate("creative_brief", {"id": "s1"}, ctx)
        return captured["brief"]

    def test_gate_reviews_auto_loaded_material(self):
        brief = self._gate_brief({
            "site": "the71c",
            "review_brief": "# the71c brand brief",
            "brief_source": "auto:build_profile:the71c",
        })
        self.assertEqual(brief["vault_brief"], "# the71c brand brief")
        self.assertEqual(brief["brief_source"], "auto:build_profile:the71c")

    def test_empty_review_is_visible_as_none(self):
        brief = self._gate_brief({"site": "the71c"})
        self.assertEqual(brief["vault_brief"], "")
        self.assertEqual(brief["brief_source"], "none")


if __name__ == "__main__":
    unittest.main()

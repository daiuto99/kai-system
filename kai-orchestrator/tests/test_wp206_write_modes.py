"""WP-20.6b/c — EDIT/BUILD write-mode regression net.

The two governed drafts-only workflows (build_page_draft, edit_page_draft) were
built and wired but had ZERO test coverage. These lock the properties that make
them safe:
  1. Drafts-only BY CONSTRUCTION — the step lists contain no publish / set-front-
     page step, so a future edit can't quietly turn a draft path into a deploy.
  2. update_page REFUSES a published/live page (the EDIT drafts-only guard) — the
     single most important safety property of the EDIT mode.
  3. edit_page_draft rejects a missing page_id before any write.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from workflows.wordpress_build_page_draft import BuildPageDraftWorkflow
from workflows.wordpress_edit_page_draft import EditPageDraftWorkflow

# Any step whose capability implies deploying/publishing must NOT exist in a
# drafts-only workflow. This is the structural guarantee.
_FORBIDDEN = ("publish", "set_front_page", "front_page", "homepage")


def _resp(ok=True, data=None, status_code=200, body_preview=""):
    return SimpleNamespace(ok=ok, data=data, status_code=status_code, body_preview=body_preview)


class DraftsOnlyByConstruction(unittest.TestCase):
    def test_build_workflow_step_list_is_drafts_only(self):
        names = [s.name for s in BuildPageDraftWorkflow.steps]
        self.assertEqual(
            names,
            ["load_site_config", "check_credentials", "load_brief",
             "dev_gate", "creative_brief", "generate_page", "create_page_draft", "complete"],
        )
        # both governance gates present
        gates = {s.name for s in BuildPageDraftWorkflow.steps if s.step_type == "approval_gate"}
        self.assertEqual(gates, {"dev_gate", "creative_brief"})
        # no publish/homepage capability anywhere
        for s in BuildPageDraftWorkflow.steps:
            cap = (s.capability or "").lower()
            self.assertFalse(any(f in cap for f in _FORBIDDEN), f"forbidden cap: {cap}")

    def test_edit_workflow_updates_not_creates_and_is_drafts_only(self):
        names = [s.name for s in EditPageDraftWorkflow.steps]
        self.assertEqual(
            names,
            ["load_site_config", "check_credentials", "load_brief",
             "dev_gate", "creative_brief", "update_page_draft", "complete"],
        )
        caps = [(s.capability or "") for s in EditPageDraftWorkflow.steps]
        self.assertIn("wordpress.update_page", caps)
        self.assertNotIn("wordpress.create_page", caps)  # EDIT targets an existing page
        for s in EditPageDraftWorkflow.steps:
            cap = (s.capability or "").lower()
            self.assertFalse(any(f in cap for f in _FORBIDDEN), f"forbidden cap: {cap}")


class UpdatePageDraftsOnlyGuard(unittest.TestCase):
    """capabilities.wordpress.update_page must refuse anything that isn't a draft."""

    def _creds(self):
        return {"fqdn": "example.test", "app_password": "not-a-secret"}

    def test_refuses_published_page(self):
        import capabilities.wordpress as wp
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "safe_request",
                               return_value=_resp(data={"status": "publish"})) as sr:
            res = wp.update_page(site="the71c", page_id=42, content="<h1>x</h1>",
                                 creds=self._creds(), caller="test")
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "failed_permanent")
        self.assertEqual(res.error["type"], "not_a_draft")
        self.assertEqual(sr.call_count, 1)  # only the GET probe; no write POST

    def test_proceeds_on_draft_page(self):
        import capabilities.wordpress as wp
        seq = [_resp(data={"status": "draft"}),                       # GET probe
               _resp(data={"id": 42, "link": "https://example.test/?p=42"})]  # POST write
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "_run_brand_drift",
                               return_value={"checked": True, "governed": True, "drift": False}), \
             mock.patch.object(wp, "safe_request", side_effect=seq) as sr:
            res = wp.update_page(site="the71c", page_id=42, content="<h1>x</h1>",
                                 creds=self._creds(), caller="test", property="the71c")
        self.assertTrue(res.ok)
        self.assertEqual(res.status, "succeeded")
        self.assertEqual(res.data["id"], 42)
        self.assertEqual(sr.call_count, 2)  # GET probe + POST write

    def test_missing_page_returns_not_found(self):
        import capabilities.wordpress as wp
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "safe_request",
                               return_value=_resp(ok=False, status_code=404, body_preview="nope")):
            res = wp.update_page(site="the71c", page_id=999, content="x",
                                 creds=self._creds(), caller="test")
        self.assertFalse(res.ok)
        self.assertEqual(res.error["type"], "page_not_found")


class EditWorkflowPageIdGuard(unittest.TestCase):
    def test_missing_page_id_fails_permanent_before_write(self):
        wf = EditPageDraftWorkflow("job-edit-guard")
        res = wf._step_update_page({"site": "the71c", "creds": {"fqdn": "x", "app_password": "y"}})
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "failed_permanent")
        self.assertEqual(res.error["type"], "missing_page_id")


if __name__ == "__main__":
    unittest.main()

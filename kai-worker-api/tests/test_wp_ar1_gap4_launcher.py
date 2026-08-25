"""WP AR-1 gap4 — launch ergonomics for governed WP workflows.

The launcher must map inputs correctly (dropping unset optionals so the workflow
sees a clean inputs dict), the job-status proxy must surface the OPEN gate id
from the steps (so no docker-exec is needed to find it), and gate-resolve must
pass the approval through. These lock that contract with the orchestrator proxy
mocked out.
"""
import unittest
from unittest import mock

from fastapi import HTTPException

from routes import orchestrator as o


class LauncherInputs(unittest.TestCase):
    def test_launch_drops_unset_optionals_and_returns_job(self):
        captured = {}

        def fake_post(path, payload):
            captured["path"] = path
            captured["payload"] = payload
            return {"job_id": "job-1", "status": "started"}

        with mock.patch.object(o, "_orchestrator_post", side_effect=fake_post):
            res = o.build_page_draft(o.BuildPageDraftRequest(site="the71c", page_title="Home"))

        self.assertEqual(captured["path"], "/workflows/run")
        self.assertEqual(captured["payload"]["type"], "wordpress.build_page_draft")
        inp = captured["payload"]["inputs"]
        # unset optionals are omitted...
        self.assertNotIn("page_content", inp)
        self.assertNotIn("property", inp)
        self.assertNotIn("brief_path", inp)
        # ...required + defaulted fields ride through
        self.assertEqual(inp["site"], "the71c")
        self.assertEqual(inp["page_title"], "Home")
        self.assertEqual(inp["probe"], False)
        self.assertEqual(res["workflow_id"], "job-1")
        self.assertTrue(res["ok"])

    def test_launch_forwards_content_and_property(self):
        captured = {}
        with mock.patch.object(o, "_orchestrator_post",
                               side_effect=lambda p, pl: captured.update(pl) or {"job_id": "j"}):
            o.build_page_draft(o.BuildPageDraftRequest(
                site="s", page_title="t", page_content="<p>hi</p>", property="the71c"))
        self.assertEqual(captured["inputs"]["page_content"], "<p>hi</p>")
        self.assertEqual(captured["inputs"]["property"], "the71c")

    def test_launch_missing_job_id_is_502(self):
        with mock.patch.object(o, "_orchestrator_post", return_value={"status": "started"}):
            with self.assertRaises(HTTPException) as cm:
                o.build_page_draft(o.BuildPageDraftRequest(site="s", page_title="t"))
        self.assertEqual(cm.exception.status_code, 502)


class JobStatusPendingGate(unittest.TestCase):
    def _job(self, steps):
        return {"job": {"id": "job-1"}, "steps": steps}

    def test_extracts_pending_gate_from_json_string_result(self):
        steps = [
            {"name": "dev_gate", "status": "succeeded", "result": "{}"},
            {"name": "creative_brief", "status": "awaiting_gate",
             "result": '{"gate_id": "gate-abc"}'},
        ]
        with mock.patch.object(o, "_orchestrator_get", return_value=self._job(steps)):
            res = o.get_job_status("job-1")
        self.assertEqual(res["pending_gate"], {"gate_id": "gate-abc", "step": "creative_brief"})

    def test_extracts_pending_gate_from_dict_result(self):
        steps = [{"name": "dev_gate", "status": "awaiting_gate",
                  "result": {"gate_id": "gate-xyz"}}]
        with mock.patch.object(o, "_orchestrator_get", return_value=self._job(steps)):
            res = o.get_job_status("job-1")
        self.assertEqual(res["pending_gate"]["gate_id"], "gate-xyz")

    def test_no_open_gate_returns_none(self):
        steps = [{"name": "create_page_draft", "status": "succeeded", "result": "{}"}]
        with mock.patch.object(o, "_orchestrator_get", return_value=self._job(steps)):
            res = o.get_job_status("job-1")
        self.assertIsNone(res["pending_gate"])

    def test_non_dict_step_result_does_not_crash(self):
        # a decoded result that is a list/scalar must not crash the extractor
        steps = [
            {"name": "x", "status": "awaiting_gate", "result": "[]"},
            {"name": "y", "status": "awaiting_gate", "result": 42},
            {"name": "z", "status": "succeeded", "result": "{}"},
        ]
        with mock.patch.object(o, "_orchestrator_get", return_value=self._job(steps)):
            res = o.get_job_status("job-1")
        self.assertIsNone(res["pending_gate"])

    def test_generic_upstream_error_is_502_not_404(self):
        with mock.patch.object(o, "_orchestrator_get", return_value={"error": "db locked"}):
            with self.assertRaises(HTTPException) as cm:
                o.get_job_status("job-1")
        self.assertEqual(cm.exception.status_code, 502)

    def test_error_containing_not_found_substring_is_502(self):
        # only the exact "not found" sentinel is a 404; a fault that merely
        # contains the words must surface as an upstream 502.
        with mock.patch.object(o, "_orchestrator_get",
                               return_value={"error": "database table not found"}):
            with self.assertRaises(HTTPException) as cm:
                o.get_job_status("job-1")
        self.assertEqual(cm.exception.status_code, 502)

    def test_not_found_is_404(self):
        with mock.patch.object(o, "_orchestrator_get", return_value={"error": "not found"}):
            with self.assertRaises(HTTPException) as cm:
                o.get_job_status("nope")
        self.assertEqual(cm.exception.status_code, 404)


class GateResolve(unittest.TestCase):
    def test_resolve_passes_approval_through(self):
        captured = {}
        with mock.patch.object(o, "_orchestrator_post",
                               side_effect=lambda p, pl: captured.update(path=p, payload=pl) or {"job_id": "job-1"}):
            res = o.resolve_orchestrator_gate("gate-abc",
                                              o.GateResolveRequest(approved=True, notes="looks good"))
        self.assertEqual(captured["path"], "/gates/gate-abc/resolve")
        self.assertEqual(captured["payload"], {"approved": True, "advisor": "leo", "notes": "looks good"})
        self.assertEqual(res["job_id"], "job-1")


class UpstreamStatusFidelity(unittest.TestCase):
    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.text = "boom"

    def _client(self, resp):
        client = mock.MagicMock()
        client.__enter__.return_value = client
        client.get.return_value = resp
        client.post.return_value = resp
        return client

    def test_get_upstream_404_surfaces_as_404(self):
        with mock.patch.object(o.httpx, "Client", return_value=self._client(self._Resp(404))):
            with self.assertRaises(HTTPException) as cm:
                o._orchestrator_get("/jobs/x")
        self.assertEqual(cm.exception.status_code, 404)

    def test_get_upstream_503_surfaces_as_502(self):
        with mock.patch.object(o.httpx, "Client", return_value=self._client(self._Resp(503))):
            with self.assertRaises(HTTPException) as cm:
                o._orchestrator_get("/jobs/x")
        self.assertEqual(cm.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()

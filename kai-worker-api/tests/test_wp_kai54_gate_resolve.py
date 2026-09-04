"""KAI-54 — dashboard WP gate-list/resolve.

Locks the two additions to the /wordpress/* dashboard path: the build-draft
status poller must surface the OPEN gate id (pending_gate), and the resolve
proxy must forward the approval to the orchestrator and map upstream client
errors faithfully. The orchestrator is mocked out.
"""
import unittest
from unittest import mock

from fastapi import HTTPException

from routes import wordpress as w


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """Context-manager stand-in for httpx.Client with canned get/post."""
    def __init__(self, get_resp=None, post_resp=None):
        self._get, self._post = get_resp, post_resp
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        self.calls.append(("GET", url))
        return self._get

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json))
        self.last_headers = headers or {}
        return self._post


class BuildDraftStatusGateId(unittest.TestCase):
    def _run(self, steps):
        fake = _FakeClient(get_resp=_Resp(200, {"job": {"status": "running"}, "steps": steps}))
        with mock.patch.object(w.httpx, "Client", return_value=fake):
            return w.build_draft_status("job-1")

    def test_surfaces_gate_id_from_string_result(self):
        res = self._run([
            {"name": "dev_gate", "status": "succeeded", "result": "{}"},
            {"name": "creative_gate", "status": "awaiting_gate", "result": '{"gate_id": "gate-abc"}'},
        ])
        self.assertEqual(res["pending_gate"], {"gate_id": "gate-abc", "step": "creative_gate"})
        self.assertEqual(res["awaiting_gate"], "creative_gate")
        # secret-free: raw step results never ride out in the steps list
        self.assertTrue(all("result" not in s for s in res["steps"]))

    def test_surfaces_gate_id_from_dict_result(self):
        res = self._run([{"name": "dev_gate", "status": "pending_leo", "result": {"gate_id": "gate-xyz"}}])
        self.assertEqual(res["pending_gate"]["gate_id"], "gate-xyz")

    def test_no_open_gate_is_none(self):
        res = self._run([{"name": "dev_gate", "status": "succeeded", "result": "{}"}])
        self.assertIsNone(res["pending_gate"])


class ResolveBuildGate(unittest.TestCase):
    def _req(self, approved=True, notes="ok"):
        return w.GateResolveRequest(approved=approved, notes=notes)

    def test_forwards_approval_and_returns_ok(self):
        fake = _FakeClient(post_resp=_Resp(200, {"job_id": "job-1"}))
        with mock.patch.object(w.httpx, "Client", return_value=fake):
            res = w.resolve_build_gate("gate-abc", self._req(approved=True, notes="ship it"))
        method, url, body = fake.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/gates/gate-abc/resolve"))
        self.assertEqual(body, {"approved": True, "advisor": "leo", "notes": "ship it"})
        # C2 [SEC] 8ae14701: resolve proxy must carry the gate-resolve credential
        self.assertIn("X-KAI-Gate-Resolve", fake.last_headers)
        self.assertEqual(res, {"ok": True, "gate_id": "gate-abc", "job_id": "job-1"})

    def test_upstream_400_is_not_masqueraded_as_502(self):
        fake = _FakeClient(post_resp=_Resp(400, text="gate already resolved"))
        with mock.patch.object(w.httpx, "Client", return_value=fake):
            with self.assertRaises(HTTPException) as cm:
                w.resolve_build_gate("gate-gone", self._req())
        self.assertEqual(cm.exception.status_code, 400)

    def test_upstream_5xx_is_502(self):
        fake = _FakeClient(post_resp=_Resp(503, text="orchestrator down"))
        with mock.patch.object(w.httpx, "Client", return_value=fake):
            with self.assertRaises(HTTPException) as cm:
                w.resolve_build_gate("gate-abc", self._req())
        self.assertEqual(cm.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()

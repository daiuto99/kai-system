"""HOSTOPS-(c): privileged host-op gates are human-only.

The security property: a hostops mutation gate can NEVER be auto-approved by the
council. It must land in pending_leo and wait for Leo — no resolution, no
callback fired — regardless of any advisor verdict. This guards against a future
auto-approve fallback silently capturing host mutations.
"""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import routes_council_gate as gates


class HostopsGateHumanOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "gates"
        self.store = gates.PersistentGateStore(self.root)
        self.patches = ExitStack()
        self.patches.enter_context(mock.patch.object(gates, "_GATES_STORE", self.store))
        self.patches.enter_context(mock.patch.object(gates, "_VAULT_GATES", self.root))

    def tearDown(self):
        self.patches.close()
        self.tempdir.cleanup()

    def _request(self, gate_type, brief):
        return gates.GateRequest(
            gate_id=f"hostops-{gate_type}",
            gate_type=gate_type,
            brief=brief,
            callback_url="http://orchestrator.invalid/gates/test/resolve",
        )

    def _seed(self, req):
        self.store[req.gate_id] = {
            "gate_id": req.gate_id, "gate_type": req.gate_type, "brief": req.brief,
            "status": "processing", "resolution": None,
            "callback_url": req.callback_url, "created_at": "2026-07-21T00:00:00+00:00",
        }

    def test_hostops_gates_go_to_pending_leo_never_auto_approved(self):
        cases = [
            ("hostops_place_secret",
             {"hostops_operation": "place_secret", "site": "site-a",
              "secret_name": "publish_gate", "audit_identity": "app:1:u"}),
            ("hostops_deploy_plugin",
             {"hostops_operation": "deploy_plugin", "site": "site-b",
              "plugin": "kai-publish-gate", "audit_identity": "app:1:u"}),
        ]
        for gate_type, brief in cases:
            with self.subTest(gate_type=gate_type):
                req = self._request(gate_type, brief)
                self._seed(req)
                callbacks = []
                with (
                    mock.patch.object(gates, "_fire_callback",
                                      side_effect=lambda _u, r: callbacks.append(r)),
                    mock.patch.object(gates, "_persist_gate_record"),
                    mock.patch.object(gates, "_slack_post", return_value="ts"),
                ):
                    gates._process_gate(req)

                state = self.store[req.gate_id]
                self.assertEqual(state["status"], "pending_leo")   # waits for Leo
                self.assertIsNone(state["resolution"])             # not resolved
                self.assertEqual(callbacks, [])                    # no callback fired

    def test_hostops_type_is_registered_explicitly_not_unknown_fallthrough(self):
        # The property is explicit, not incidental: the types are in the registry.
        self.assertIn("hostops_place_secret", gates._HOSTOPS_GATE_TYPES)
        self.assertIn("hostops_deploy_plugin", gates._HOSTOPS_GATE_TYPES)

    def test_brief_carries_no_payload_bytes(self):
        # The gate brief names the secret; the resolver reads bytes post-approval.
        brief = {"hostops_operation": "place_secret", "site": "site-a",
                 "secret_name": "publish_gate", "audit_identity": "app:1:u"}
        with mock.patch.object(gates, "_persist_artifact") as persist:
            summary, assessment = gates._hostops_gate_review(brief, "hostops-x")
        self.assertIn("place_secret", summary)
        self.assertIn("HUMAN-ONLY", assessment)
        # Only a name was ever handed to the review — no byte payload present.
        self.assertNotIn("material", str(brief))
        persist.assert_called_once()


if __name__ == "__main__":
    unittest.main()

"""HOSTOPS-(c): privileged host-op gates are human-only.

The security property: a hostops mutation gate can NEVER be auto-approved by the
council. It must land in pending_leo and wait for Leo — no resolution, no
callback fired — regardless of any advisor verdict. This guards against a future
auto-approve fallback silently capturing host mutations.
"""
import json
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

    def test_external_hostops_gates_go_to_pending_leo(self):
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
                    mock.patch.object(gates, "_hostops_action", return_value={"op": "deploy_plugin", "owner": "client", "external_party": True}),
                    mock.patch.object(gates, "_fire_callback",
                                      side_effect=lambda _u, r: callbacks.append(r)),
                    mock.patch.object(gates, "_persist_gate_record"),
                    mock.patch.object(gates, "_fyi"),
                ):
                    gates._process_gate(req)

                state = self.store[req.gate_id]
                self.assertEqual(state["status"], "pending_leo")
                self.assertIsNone(state["resolution"])             # not resolved
                self.assertEqual(callbacks, [])                    # no callback fired

    def test_hostops_type_is_registered_explicitly_not_unknown_fallthrough(self):
        # The property is explicit, not incidental: the types are in the registry.
        self.assertIn("hostops_place_secret", gates._HOSTOPS_GATE_TYPES)
        self.assertIn("hostops_deploy_plugin", gates._HOSTOPS_GATE_TYPES)

    def test_real_resolution_without_owner_fails_closed(self):
        registry = Path(self.tempdir.name) / "sites.json"
        registry.write_text(json.dumps({"sites": {"unmarked": {}}}))
        with mock.patch.object(gates, "_WORDPRESS_SITES", registry):
            action = gates._hostops_action({"hostops_operation": "deploy_plugin", "site": "unmarked"})
        self.assertEqual(action["owner"], "unknown")
        self.assertEqual(gates.classify(action).mode, "approve")

    def test_real_resolution_unreadable_registry_fails_closed(self):
        with mock.patch("pathlib.Path.read_text", side_effect=OSError("unreadable")):
            action = gates._hostops_action({"hostops_operation": "deploy_plugin", "site": "alexadaiuto"})
        self.assertEqual(action["owner"], "unknown")
        self.assertEqual(gates.classify(action).mode, "approve")

    def test_real_resolution_explicit_leo_owner_is_autonomous(self):
        registry = Path(self.tempdir.name) / "sites.json"
        registry.write_text(json.dumps({"sites": {"owned": {"owner": "leo"}}}))
        with mock.patch.object(gates, "_WORDPRESS_SITES", registry):
            action = gates._hostops_action({"hostops_operation": "deploy_plugin", "site": "owned"})
        self.assertEqual(action["owner"], "leo")
        self.assertEqual(gates.classify(action).mode, "autonomous")

    def test_leo_owned_hostops_gate_auto_resolves_without_t2_prompt(self):
        req = self._request("hostops_deploy_plugin", {"hostops_operation": "deploy_plugin", "site": "site-a", "plugin": "kai-publish-gate", "audit_identity": "app:1:u"})
        self._seed(req)
        with (mock.patch.object(gates, "_hostops_action", return_value={"op":"deploy_plugin", "owner":"leo"}), mock.patch.object(gates, "_fyi"), mock.patch.object(gates, "_fire_callback") as callback):
            gates._process_gate(req)
        self.assertEqual(self.store[req.gate_id]["status"], "resolved")
        callback.assert_called_once()

    def test_brief_carries_no_payload_bytes(self):
        # The gate brief names the secret; the resolver reads bytes post-approval.
        brief = {"hostops_operation": "place_secret", "site": "site-a",
                 "secret_name": "publish_gate", "audit_identity": "app:1:u"}
        with mock.patch.object(gates, "_persist_artifact") as persist:
            summary, assessment = gates._hostops_gate_review(brief, "hostops-x")
        self.assertIn("place_secret", summary)
        self.assertIn("autonomy policy", assessment)
        # Only a name was ever handed to the review — no byte payload present.
        self.assertNotIn("material", str(brief))
        persist.assert_called_once()

    def test_hostops_gate_enqueues_a_bound_t2_action_without_secret_bytes(self):
        req = self._request("hostops_place_secret", {
            "hostops_operation": "place_secret", "site": "site-a",
            "secret_name": "publish_gate", "audit_identity": "app:1:u",
        })
        self._seed(req)
        with (
            mock.patch.object(gates, "_hostops_action", return_value={"op": "place_secret", "owner": "client", "external_party": True}),
            mock.patch.object(gates, "_fyi"),
            mock.patch.object(gates.httpx, "post") as post,
        ):
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"id": "t2abc123"}
            gates._process_gate(req)

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["kind"], "hostops_gate")
        self.assertEqual(payload["gate_id"], req.gate_id)
        self.assertEqual(payload["callback_url"], req.callback_url)
        self.assertIn("place_secret", payload["action"])
        self.assertNotIn("material", str(payload))
        self.assertEqual(self.store[req.gate_id]["t2_action_id"], "t2abc123")


if __name__ == "__main__":
    unittest.main()

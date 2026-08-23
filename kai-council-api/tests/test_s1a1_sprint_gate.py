"""[S1-A1] Sprint approval gate — behavior contract.

A `sprint_gate` is the primitive an autonomous sprint uses to raise a HARD GATE
through the live council router (Buzz-primary / Telegram-emergency). Properties:

  1. It runs NO LLM review chain and lands in pending_leo (Leo decides), using the
     caller-provided summary/detail verbatim — never auto-resolved.
  2. It is a registered gate_type, not the unknown-gate-type fallthrough.
  3. Resolving it fires NO callback (a sprint has no orchestrator workflow; it polls
     /state via request_sprint_gate instead).
"""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import routes_council_gate as gates


class SprintGateTests(unittest.TestCase):
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

    def _seed(self, gate_id, brief):
        req = gates.GateRequest(
            gate_id=gate_id, gate_type="sprint_gate", brief=brief,
            callback_url="sprint://poll",
        )
        self.store[gate_id] = {
            "gate_id": gate_id, "gate_type": "sprint_gate", "brief": brief,
            "status": "processing", "resolution": None,
            "callback_url": "sprint://poll", "created_at": "2026-08-23T00:00:00+00:00",
        }
        return req

    def test_sprint_gate_lands_pending_leo_with_caller_summary_no_review(self):
        brief = {"summary": "Rotate the Cloudways token to all copies",
                 "detail": "credential move — hard gate", "kind": "sprint_gate"}
        req = self._seed("sprint-abc123", brief)
        callbacks = []
        with (
            # Buzz primary + poller alive => the notify path just logs; no transport.
            mock.patch.object(gates, "_APPROVAL_SURFACE", "buzz"),
            mock.patch.object(gates, "_buzz_alive", return_value=True),
            mock.patch.object(gates, "_fire_callback",
                              side_effect=lambda _u, r: callbacks.append(r)),
            mock.patch.object(gates, "_persist_gate_record"),
            mock.patch.object(gates, "_fyi"),
        ):
            gates._process_gate(req)

        state = self.store["sprint-abc123"]
        self.assertEqual(state["status"], "pending_leo")     # human-only
        self.assertIsNone(state["resolution"])               # not auto-resolved
        self.assertEqual(callbacks, [])                      # no callback on raise
        # Caller framing is preserved verbatim — not an LLM review summary.
        self.assertEqual(state["summary"], brief["summary"])
        self.assertEqual(state["kai_assessment"], brief["detail"])

    def test_sprint_gate_missing_summary_is_safe(self):
        req = self._seed("sprint-nosum", {"kind": "sprint_gate"})
        with (
            mock.patch.object(gates, "_APPROVAL_SURFACE", "buzz"),
            mock.patch.object(gates, "_buzz_alive", return_value=True),
            mock.patch.object(gates, "_fire_callback"),
            mock.patch.object(gates, "_persist_gate_record"),
            mock.patch.object(gates, "_fyi"),
        ):
            gates._process_gate(req)
        state = self.store["sprint-nosum"]
        self.assertEqual(state["status"], "pending_leo")
        self.assertTrue(state["summary"])                    # a non-empty placeholder

    def test_resolving_sprint_gate_fires_no_callback(self):
        self._seed("sprint-res", {"summary": "do X", "detail": "", "kind": "sprint_gate"})
        self.store.update("sprint-res", status="pending_leo")
        fired = []
        with (
            mock.patch.object(gates, "_persist_gate_record"),
            mock.patch.object(gates, "_fyi"),
            mock.patch.object(gates, "_capture_gate_learning"),
            mock.patch.object(gates, "_fire_callback",
                              side_effect=lambda _u, r: fired.append(r)),
        ):
            gates.resolve_gate("sprint-res",
                                      gates.GateResolve(approved=True, notes="ok", resolver="leo"))
        self.assertEqual(self.store["sprint-res"]["status"], "resolved")
        self.assertEqual(fired, [])                           # sprint_gate skips callback


if __name__ == "__main__":
    unittest.main()

"""BUG 91dbcb0a — council gate reviews fall back to a LOCAL reviewer when the
pinned cloud model is unavailable, instead of denying every governed gate closed.

Covers:
  * cloud OK              -> cloud verdict returned, local NEVER called
  * cloud raises          -> local reviewer used, verdict returned + provenance footer
  * cloud empty/over_budget -> treated unavailable -> local used
  * both fail             -> ReviewerUnavailable (caller then fails closed)
  * local usage is tracked as the structurally-free qwen-mid alias
  * _extract_verdict still reads the VERDICT: header through the appended footer
"""
import unittest
from unittest import mock
import tempfile
from pathlib import Path

import routes_council_gate as gates
import usage_tracker


class GateLocalFallbackTests(unittest.TestCase):
    def _patch_common(self, stack):
        stack.enter_context(mock.patch("persona.load_persona", return_value="SYSTEM"))
        stack.enter_context(mock.patch("council_config._track_usage"))

    def test_cloud_ok_local_not_called(self):
        with mock.patch("router._run_agentic_loop",
                        return_value=("VERDICT: SIGNED-OFF — ok\nbody", 10, 5, 0, 0)) as cloud, \
             mock.patch("providers._call_litellm") as local, \
             mock.patch("persona.load_persona", return_value="SYSTEM"), \
             mock.patch("council_config._track_usage"):
            out = gates._gate_review_llm("dev", "review this", "gate:dev:review")
        self.assertIn("SIGNED-OFF", out)
        cloud.assert_called_once()
        local.assert_not_called()

    def test_cloud_outage_falls_back_to_local(self):
        with mock.patch("router._run_agentic_loop",
                        side_effect=RuntimeError("Anthropic 400: usage limit")) as cloud, \
             mock.patch("providers._call_litellm",
                        return_value=("VERDICT: CONCERNS — local reviewed\ndetail", 8, 4)) as local, \
             mock.patch("persona.load_persona", return_value="SYSTEM"), \
             mock.patch("council_config._track_usage") as track:
            out = gates._gate_review_llm("dev", "review this", "gate:dev:review")
        cloud.assert_called_once()
        local.assert_called_once()
        # local reviewer used the free alias
        self.assertEqual(local.call_args.args[0], "qwen-mid")
        # verdict still parses through the appended provenance footer
        self.assertEqual(gates._extract_verdict(out), "CONCERNS — local reviewed")
        self.assertIn("local fallback", out)
        # usage tracked against the free alias (provider=litellm)
        self.assertIn("qwen-mid", track.call_args.args)

    def test_cloud_empty_verdict_falls_back(self):
        with mock.patch("router._run_agentic_loop",
                        return_value=("   ", 3, 0, 0, 0)), \
             mock.patch("providers._call_litellm",
                        return_value=("VERDICT: READY — local\nx", 5, 2)) as local, \
             mock.patch("persona.load_persona", return_value="SYSTEM"), \
             mock.patch("council_config._track_usage"):
            out = gates._gate_review_llm("kai", "check", "gate:kai:qc")
        local.assert_called_once()
        self.assertEqual(gates._extract_verdict(out), "READY — local")

    def test_cloud_over_budget_falls_back(self):
        with mock.patch("router._run_agentic_loop",
                        return_value=("over_budget: too big", 200000, 0, 0, 0)), \
             mock.patch("providers._call_litellm",
                        return_value=("VERDICT: NOT READY — local\nx", 5, 2)) as local, \
             mock.patch("persona.load_persona", return_value="SYSTEM"), \
             mock.patch("council_config._track_usage"):
            out = gates._gate_review_llm("kai", "check", "gate:kai:qc")
        local.assert_called_once()
        self.assertIn("NOT READY", out)

    def test_both_fail_raises_reviewer_unavailable(self):
        with mock.patch("router._run_agentic_loop",
                        side_effect=RuntimeError("cloud down")), \
             mock.patch("providers._call_litellm",
                        side_effect=RuntimeError("litellm down")), \
             mock.patch("persona.load_persona", return_value="SYSTEM"), \
             mock.patch("council_config._track_usage"):
            with self.assertRaises(gates.ReviewerUnavailable):
                gates._gate_review_llm("dev", "review", "gate:dev:review")

    def test_local_empty_verdict_raises(self):
        with mock.patch("router._run_agentic_loop",
                        side_effect=RuntimeError("cloud down")), \
             mock.patch("providers._call_litellm",
                        return_value=("", 0, 0)), \
             mock.patch("persona.load_persona", return_value="SYSTEM"), \
             mock.patch("council_config._track_usage"):
            with self.assertRaises(gates.ReviewerUnavailable):
                gates._gate_review_llm("dev", "review", "gate:dev:review")

    def test_qwen_mid_alias_is_structurally_free(self):
        self.assertEqual(usage_tracker.COSTS.get("qwen-mid"), (0.0, 0.0))
        self.assertEqual(usage_tracker.COSTS.get("qwen-mid-worker"), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()



class GateFailClosedOnVerdictlessFallbackTests(unittest.TestCase):
    """Regression lock (Codex verify follow-up): a verdict-less LOCAL fallback reply
    must NEVER auto-approve a gate. Proven end-to-end through _process_gate on the
    devops_gate — the one branch whose auto-approval keys off the LLM verdict — so a
    future caller that drops its positive-token guard fails CI here."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "gates"
        self.store = gates.PersistentGateStore(self.root)
        self.stack = mock.patch.object(gates, "_GATES_STORE", self.store)
        self.stack.start()
        self.vault = mock.patch.object(gates, "_VAULT_GATES", self.root)
        self.vault.start()

    def tearDown(self):
        self.vault.stop()
        self.stack.stop()
        self.tempdir.cleanup()

    def test_verdictless_local_reply_routes_to_pending_leo_not_approved(self):
        req = gates.GateRequest(
            gate_id="bug91-verdictless",
            gate_type="devops_gate",
            brief={"title": "routine-looking infra change"},
            callback_url="http://orchestrator.invalid/gates/x/resolve",
        )
        self.store[req.gate_id] = {
            "gate_id": req.gate_id, "gate_type": req.gate_type, "brief": req.brief,
            "status": "processing", "resolution": None,
            "callback_url": req.callback_url, "created_at": "2026-08-31T00:00:00+00:00",
        }
        callbacks = []
        with mock.patch("router._run_agentic_loop", side_effect=RuntimeError("cloud down")), \
             mock.patch("providers._call_litellm",
                        return_value=("the model rambled at length but never emitted a verdict line", 6, 3)), \
             mock.patch("persona.load_persona", return_value="SYS"), \
             mock.patch("council_config._track_usage"), \
             mock.patch.object(gates, "_persist_artifact"), \
             mock.patch.object(gates, "_persist_gate_record"), \
             mock.patch.object(gates, "_buzz_alive", return_value=True), \
             mock.patch.object(gates, "_APPROVAL_SURFACE", "buzz"), \
             mock.patch.object(gates, "_fire_callback",
                               side_effect=lambda _u, r: callbacks.append(r)):
            gates._process_gate(req)
        state = self.store[req.gate_id]
        # Fail-closed: a verdict-less fallback reply is NOT auto-approved — it waits for Leo.
        self.assertEqual(state["status"], "pending_leo")
        self.assertIsNone(state.get("resolution"))
        self.assertEqual(callbacks, [])  # no auto-resolution callback fired


class GateFallbackNeverAutoApprovesTests(unittest.TestCase):
    """A LOCAL fallback reviewer (weaker 7b model) may keep human-decided gates moving
    during a cloud outage, but must NEVER drive an AUTONOMOUS approval — even when it
    emits a genuine-looking positive verdict (ROUTINE / APPROVED). Auto-approval stays
    reserved for the pinned cloud reviewer (BUG 91dbcb0a, Codex verify hardening)."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "gates"
        self.store = gates.PersistentGateStore(self.root)
        self._s = mock.patch.object(gates, "_GATES_STORE", self.store)
        self._s.start()
        self._v = mock.patch.object(gates, "_VAULT_GATES", self.root)
        self._v.start()

    def tearDown(self):
        self._v.stop()
        self._s.stop()
        self.tempdir.cleanup()

    def _seed(self, gate_id, gate_type):
        req = gates.GateRequest(gate_id=gate_id, gate_type=gate_type,
                                brief={"title": "infra change"},
                                callback_url="http://orch.invalid/g/resolve")
        self.store[req.gate_id] = {
            "gate_id": req.gate_id, "gate_type": req.gate_type, "brief": req.brief,
            "status": "processing", "resolution": None,
            "callback_url": req.callback_url, "created_at": "2026-08-31T00:00:00+00:00"}
        return req

    def test_fallback_ROUTINE_does_not_auto_approve_devops(self):
        req = self._seed("bug91-fb-routine", "devops_gate")
        callbacks = []
        with mock.patch("router._run_agentic_loop", side_effect=RuntimeError("cloud down")), \
             mock.patch("providers._call_litellm",
                        return_value=("VERDICT: ROUTINE — looks routine to me\ndetail", 6, 3)), \
             mock.patch("persona.load_persona", return_value="SYS"), \
             mock.patch("council_config._track_usage"), \
             mock.patch.object(gates, "_persist_artifact"), \
             mock.patch.object(gates, "_persist_gate_record"), \
             mock.patch.object(gates, "_buzz_alive", return_value=True), \
             mock.patch.object(gates, "_APPROVAL_SURFACE", "buzz"), \
             mock.patch.object(gates, "_fire_callback", side_effect=lambda _u, r: callbacks.append(r)):
            gates._process_gate(req)
        state = self.store[req.gate_id]
        self.assertEqual(state["status"], "pending_leo")   # escalated, NOT auto-approved
        self.assertIsNone(state.get("resolution"))
        self.assertEqual(callbacks, [])

    def test_cloud_ROUTINE_still_auto_approves_devops(self):
        # Control: the pinned cloud reviewer's ROUTINE verdict DOES still auto-approve,
        # proving the guard narrows only the fallback path.
        req = self._seed("bug91-cloud-routine", "devops_gate")
        callbacks = []
        with mock.patch("router._run_agentic_loop",
                        return_value=("VERDICT: ROUTINE — fine\ndetail", 6, 3, 0, 0)), \
             mock.patch("persona.load_persona", return_value="SYS"), \
             mock.patch("council_config._track_usage"), \
             mock.patch.object(gates, "_persist_artifact"), \
             mock.patch.object(gates, "_persist_gate_record"), \
             mock.patch.object(gates, "_fire_callback", side_effect=lambda _u, r: callbacks.append(r)):
            gates._process_gate(req)
        state = self.store[req.gate_id]
        self.assertEqual(state["status"], "resolved")
        self.assertIs(state["resolution"]["approved"], True)
        self.assertEqual(state["resolution"]["advisor"], "devops")
        self.assertEqual(callbacks, [state["resolution"]])

    def test_fallback_APPROVED_does_not_validate_brief(self):
        with mock.patch("router._run_agentic_loop", side_effect=RuntimeError("cloud down")), \
             mock.patch("providers._call_litellm",
                        return_value=("APPROVED — all five sections pass", 4, 2)), \
             mock.patch("persona.load_persona", return_value="SYS"), \
             mock.patch("council_config._track_usage"):
            approved, reply = gates._kai_validate_brief("some brief")
        self.assertFalse(approved)          # fallback APPROVED is NOT trusted to validate
        self.assertIn("local fallback", reply)

    def test_cloud_APPROVED_validates_brief(self):
        with mock.patch("router._run_agentic_loop",
                        return_value=("APPROVED — all five sections pass", 4, 2, 0, 0)), \
             mock.patch("persona.load_persona", return_value="SYS"), \
             mock.patch("council_config._track_usage"):
            approved, _ = gates._kai_validate_brief("some brief")
        self.assertTrue(approved)


class GateSetupFailsClosedTests(unittest.TestCase):
    """Setup failures (persona load) must fail CLOSED as ReviewerUnavailable — never
    escape as an arbitrary exception (Codex verify hardening, finding #5)."""

    def test_persona_load_failure_fails_closed(self):
        with mock.patch("persona.load_persona", side_effect=RuntimeError("persona file gone")), \
             mock.patch("router._run_agentic_loop") as cloud, \
             mock.patch("providers._call_litellm") as local:
            with self.assertRaises(gates.ReviewerUnavailable):
                gates._gate_review_llm("dev", "review", "gate:dev:review")
        cloud.assert_not_called()
        local.assert_not_called()


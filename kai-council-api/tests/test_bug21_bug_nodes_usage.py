"""BUG-21 regression coverage for bug-triage nodes.

The router returns five values, including prompt-cache token counts. Each
bug-triage graph node must unpack all five and persist the complete usage
record, not merely avoid the original tuple-unpack exception.
"""
import unittest
from unittest import mock

from graphs import bug_nodes


class BugNodesUsageTests(unittest.TestCase):
    def _state(self):
        return {
            "issue_id": "bug-21-regression",
            "issue_name": "BUG-21 regression",
            "issue_description": "Verify five-value agent-loop handling.",
            "priority": "high",
            "diagnosis": "DIAGNOSIS: verified\\nROUTING: dev\\nRISK_LEVEL: low",
            "proposed_fix": "Unpack all five loop values.",
            "lse_review": "DECISION: APPROVE",
            "architect_review": "DECISION: APPROVE",
            "lse_approved": True,
            "architect_approved": True,
            "iteration": 1,
            "audit_log": [],
        }

    def _assert_node_tracks_all_loop_usage(self, node, advisor, trigger, reply):
        with (
            mock.patch.object(bug_nodes, "load_persona", return_value="test system"),
            mock.patch.object(
                bug_nodes,
                "_run_agentic_loop",
                return_value=(reply, 101, 202, 303, 404),
            ) as loop,
            mock.patch.object(bug_nodes, "_track_usage", create=True) as track_usage,
        ):
            result = node(self._state())

        loop.assert_called_once()
        track_usage.assert_called_once_with(
            advisor,
            101,
            202,
            "anthropic",
            bug_nodes.MODEL,
            trigger_source=trigger,
            cache_read_tokens=303,
            cache_creation_tokens=404,
        )
        self.assertTrue(result["audit_log"])

    def test_support_diagnosis_unpacks_and_tracks_five_loop_values(self):
        self._assert_node_tracks_all_loop_usage(
            bug_nodes.support_diagnosis,
            "support-engineer",
            "graph:bug_nodes:support_diagnosis",
            "DIAGNOSIS: test\\nROUTING: dev\\nRISK_LEVEL: low",
        )

    def test_lse_review_unpacks_and_tracks_five_loop_values(self):
        self._assert_node_tracks_all_loop_usage(
            bug_nodes.lse_review,
            "lse",
            "graph:bug_nodes:lse_review",
            "DECISION: APPROVE",
        )

    def test_architect_review_unpacks_and_tracks_five_loop_values(self):
        self._assert_node_tracks_all_loop_usage(
            bug_nodes.architect_review,
            "architect",
            "graph:bug_nodes:architect_review",
            "DECISION: APPROVE",
        )

    def test_kai_validation_unpacks_and_tracks_five_loop_values(self):
        self._assert_node_tracks_all_loop_usage(
            bug_nodes.kai_validation,
            "kai",
            "graph:bug_nodes:kai_validation",
            "DECISION: ESCALATE",
        )


if __name__ == "__main__":
    unittest.main()

import asyncio
import stat
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from fastapi import BackgroundTasks

import routes_council_gate as gates


class GateFailClosedTests(unittest.TestCase):
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

    @staticmethod
    def request(gate_id="bug18-test-gate", gate_type="dev_gate"):
        return gates.GateRequest(
            gate_id=gate_id,
            gate_type=gate_type,
            brief={"title": "Failure-layer test"},
            callback_url="http://orchestrator.invalid/gates/test/resolve",
        )

    def seed(self, req):
        self.store[req.gate_id] = {
            "gate_id": req.gate_id,
            "gate_type": req.gate_type,
            "brief": req.brief,
            "status": "processing",
            "resolution": None,
            "callback_url": req.callback_url,
            "created_at": "2026-07-12T00:00:00+00:00",
        }

    def test_every_reviewer_crash_denies_with_retry_after(self):
        review_paths = [
            ("plan_gate", "_plan_gate_review"),
            ("dev_gate", "_dev_gate_review"),
            ("creative_gate", "_creative_gate_review"),
            ("devops_gate", "_devops_gate_review"),
        ]

        for gate_type, review_name in review_paths:
            with self.subTest(gate_type=gate_type):
                req = self.request(gate_id=f"bug18-{gate_type}", gate_type=gate_type)
                self.seed(req)
                callbacks = []

                with (
                    mock.patch.object(
                        gates,
                        review_name,
                        side_effect=RuntimeError("forced reviewer crash"),
                    ),
                    mock.patch.object(
                        gates,
                        "_fire_callback",
                        side_effect=lambda _url, result: callbacks.append(result),
                    ),
                    mock.patch.object(gates, "_persist_gate_record"),
                ):
                    gates._process_gate(req)

                state = self.store[req.gate_id]
                self.assertEqual(state["status"], "resolved")
                self.assertIs(state["resolution"]["approved"], False)
                self.assertEqual(state["resolution"]["retry_after"], 60)
                self.assertEqual(callbacks, [state["resolution"]])

    def test_reviewer_wrappers_propagate_crashes(self):
        import graphs.graph

        crashed_graph = mock.Mock()
        crashed_graph.invoke.side_effect = RuntimeError("forced graph crash")
        with mock.patch.object(graphs.graph, "get_graph", return_value=crashed_graph):
            with self.assertRaises(gates.ReviewerUnavailable):
                gates._call_advisor("dev", "test", "bug18-wrapper")
            with self.assertRaises(gates.ReviewerUnavailable):
                gates._kai_quality_check("dev", {}, "test")
            with self.assertRaises(gates.ReviewerUnavailable):
                gates._kai_validate_brief("test brief")

    def test_gate_store_survives_process_restart(self):
        req = self.request(gate_id="bug18-restart")
        self.seed(req)

        second_process = gates.PersistentGateStore(self.root)

        self.assertEqual(second_process[req.gate_id], self.store[req.gate_id])
        self.assertEqual(
            stat.S_IMODE((self.root / req.gate_id / "state.json").stat().st_mode),
            0o600,
        )

    def test_receive_gate_is_immediately_durable(self):
        req = self.request()

        asyncio.run(gates.receive_gate(req, BackgroundTasks()))
        restarted_store = gates.PersistentGateStore(self.root)

        self.assertEqual(restarted_store[req.gate_id]["status"], "processing")

    def test_invalid_gate_id_cannot_escape_store_root(self):
        with self.assertRaises(ValueError):
            self.store["../escape"] = {"gate_id": "../escape"}


if __name__ == "__main__":
    unittest.main()

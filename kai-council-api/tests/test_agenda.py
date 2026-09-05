"""Durable conversational agenda — regression tests for KAI-3ba4cabd.

Pin the behaviors that make a multi-step flow survive across turns: start,
one-at-a-time advance, resume-where-left-off after a restart (durable), auto-close
on the last item, the every-turn render block that names the CURRENT item, and the
fail-open contract (a corrupt/missing agenda never raises on the live read path).
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agenda  # noqa: E402


class AgendaEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = agenda._AGENDA_DIR
        agenda._AGENDA_DIR = Path(self._tmp.name)

    def tearDown(self):
        agenda._AGENDA_DIR = self._orig_dir
        self._tmp.cleanup()

    def test_start_then_current_is_first_item(self):
        a = agenda.start("kai", "RYG pillar scan", ["Health", "Wealth", "Relationships"])
        self.assertEqual(a["status"], "active")
        self.assertEqual(a["current"], 0)
        self.assertEqual([it["label"] for it in a["items"]], ["Health", "Wealth", "Relationships"])

    def test_start_requires_title_and_items(self):
        with self.assertRaises(ValueError):
            agenda.start("kai", "", ["x"])
        with self.assertRaises(ValueError):
            agenda.start("kai", "t", [])

    def test_advance_one_at_a_time_records_answer_and_moves(self):
        agenda.start("kai", "scan", ["Health", "Wealth", "Relationships"])
        a = agenda.advance("kai", "green")
        self.assertEqual(a["items"][0]["status"], "done")
        self.assertEqual(a["items"][0]["answer"], "green")
        self.assertEqual(a["current"], 1)  # now on Wealth

    def test_resume_after_restart_is_durable(self):
        agenda.start("kai", "scan", ["Health", "Wealth", "Relationships"])
        agenda.advance("kai", "green")
        # Simulate a fresh process: no in-memory state, read from disk.
        resumed = agenda.get("kai")
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed["current"], 1)
        self.assertEqual(resumed["items"][0]["answer"], "green")

    def test_last_item_auto_closes_the_agenda(self):
        agenda.start("kai", "scan", ["Health", "Wealth"])
        agenda.advance("kai", "green")
        a = agenda.advance("kai", "yellow")
        self.assertEqual(a["status"], "complete")
        self.assertIsNone(agenda.get("kai"))  # no longer active

    def test_advance_with_no_active_agenda_raises(self):
        with self.assertRaises(ValueError):
            agenda.advance("kai")

    def test_render_block_names_current_item_and_marks_progress(self):
        agenda.start("kai", "RYG pillar scan", ["Health", "Wealth", "Relationships"])
        agenda.advance("kai", "green")
        block = agenda.render_block("kai")
        self.assertIn("RYG pillar scan", block)
        self.assertIn("[x] Health", block)
        self.assertIn("[→ ASK NOW] Wealth", block)  # current item flagged
        self.assertIn("advance_agenda", block)
        self.assertIn("One item per turn", block)

    def test_render_block_empty_when_no_agenda(self):
        self.assertEqual(agenda.render_block("kai"), "")

    def test_render_block_is_fail_open_on_corrupt_state(self):
        p = agenda._path("kai")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ this is not valid json")
        # Must NOT raise on the live read path — a corrupt agenda degrades to no-agenda.
        self.assertEqual(agenda.render_block("kai"), "")
        self.assertIsNone(agenda.get("kai"))

    def test_fail_open_on_valid_but_non_object_json(self):
        # Codex-found defect: a valid non-object payload (bare list/string/number)
        # passes json.loads, so a naive .get('status') on it would raise —
        # violating the never-raise contract. get()/render_block must treat any
        # non-dict state as no-agenda, not crash the turn.
        p = agenda._path("kai")
        p.parent.mkdir(parents=True, exist_ok=True)
        for payload in ("[1, 2, 3]", '"just a string"', "42", "true", "null"):
            p.write_text(payload)
            self.assertIsNone(agenda.get("kai"), payload)
            self.assertEqual(agenda.render_block("kai"), "", payload)

    def test_abandon_drops_active_agenda(self):
        agenda.start("kai", "scan", ["Health", "Wealth"])
        agenda.abandon("kai")
        self.assertIsNone(agenda.get("kai"))

    def test_agendas_are_per_advisor(self):
        agenda.start("kai", "kai scan", ["A", "B"])
        agenda.start("sky", "sky flow", ["X"])
        self.assertEqual(agenda.get("kai")["title"], "kai scan")
        self.assertEqual(agenda.get("sky")["title"], "sky flow")


if __name__ == "__main__":
    unittest.main()

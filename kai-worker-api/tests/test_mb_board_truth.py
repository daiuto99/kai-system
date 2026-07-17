"""KAI-792 regressions for Plane detail readback and next_action provenance."""

import json
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from pydantic import ValidationError

WORKER_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKER_API))

from routes import plane as plane_routes  # noqa: E402
from routes import session as session_routes  # noqa: E402


ISSUE_ID = "11111111-2222-4333-8444-555555555555"


class BoardTruthTests(unittest.TestCase):

    def test_plane_detail_includes_description_fields(self):
        issue = {
            "id": ISSUE_ID,
            "name": "Board truth",
            "state": "state-1",
            "priority": "urgent",
            "sequence_id": 792,
            "description_stripped": "",
            "description_html": "<p>content proof</p>",
            "created_at": "2026-07-14T00:00:00Z",
            "updated_at": "2026-07-14T00:01:00Z",
        }
        responses = [
            {"results": [{"id": "state-1", "name": "In Progress", "group": "started"}]},
            issue,
        ]
        with mock.patch.object(plane_routes, "_req", side_effect=responses):
            result = plane_routes.get_plane_issue(ISSUE_ID)

        self.assertEqual(result["description"], "content proof")
        self.assertEqual(result["description_html"], "<p>content proof</p>")

    def test_plane_detail_falls_back_to_non_kai_project(self):
        wp_project = {"id": "wp-project", "name": "WordPress"}
        issue = {
            "id": ISSUE_ID, "name": "WP task", "state": "wp-state",
            "priority": "high", "sequence_id": 20,
        }
        responses = [
            {"results": [{"id": "kai-state", "name": "In Progress", "group": "started"}]},
            HTTPError("https://plane/issue", 404, "not found", None, None),
            {"results": [{"id": plane_routes.KAI_PROJECT_ID}, wp_project]},
            {"results": [{"id": "wp-state", "name": "In Progress", "group": "started"}]},
            issue,
        ]
        with mock.patch.object(plane_routes, "_req", side_effect=responses):
            result = plane_routes.get_plane_issue(ISSUE_ID)

        self.assertEqual(result["id"], ISSUE_ID)
        self.assertEqual(result["project_id"], "wp-project")
        self.assertEqual(result["state"], "In Progress")

    def test_plane_detail_returns_404_when_issue_is_absent_everywhere(self):
        other_project = {"id": "other-project", "name": "Other"}
        responses = [
            {"results": [{"id": "kai-state", "name": "In Progress", "group": "started"}]},
            HTTPError("https://plane/issue", 404, "not found", None, None),
            {"results": [{"id": plane_routes.KAI_PROJECT_ID}, other_project]},
            {"results": [{"id": "other-state", "name": "In Progress", "group": "started"}]},
            HTTPError("https://plane/issue", 404, "not found", None, None),
        ]
        with mock.patch.object(plane_routes, "_req", side_effect=responses), \
                self.assertRaises(HTTPException) as exc:
            plane_routes.get_plane_issue(ISSUE_ID)

        self.assertEqual(exc.exception.status_code, 404)

    def test_next_action_schema_refuses_from_memory_action_text(self):
        request_type = getattr(session_routes, "NextActionRequest", None)
        self.assertIsNotNone(request_type, "structural next_action request schema is missing")
        with self.assertRaises(ValidationError):
            request_type(issue_id=ISSUE_ID, action="remembered stale prose")

    def test_next_action_reader_refuses_unproven_from_memory_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "next_action.json"
            target.write_text(json.dumps({"action": "remembered stale prose"}))
            with mock.patch.object(session_routes, "NEXT_ACTION_PATH", target):
                saved, detail = session_routes._read_guarded_next_action()

        self.assertIsNone(saved)
        self.assertIn("missing live-board provenance", detail)

    def test_next_action_refuses_closed_issue(self):
        request_type = getattr(session_routes, "NextActionRequest", None)
        writer = getattr(session_routes, "write_next_action", None)
        self.assertIsNotNone(request_type)
        self.assertIsNotNone(writer)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "next_action.json"
            closed = {
                "id": ISSUE_ID,
                "name": "Already shipped",
                "state": "Done",
                "state_group": "completed",
                "priority": "high",
                "sequence_id": 791,
            }
            with mock.patch.object(session_routes, "NEXT_ACTION_PATH", target), \
                 mock.patch.object(session_routes.plane_routes, "get_plane_issue", return_value=closed), \
                 self.assertRaises(HTTPException) as exc:
                writer(request_type(issue_id=ISSUE_ID))

            self.assertEqual(exc.exception.status_code, 409)
            self.assertFalse(target.exists())

    def test_next_action_succeeds_only_from_live_open_readback(self):
        request_type = getattr(session_routes, "NextActionRequest", None)
        writer = getattr(session_routes, "write_next_action", None)
        self.assertIsNotNone(request_type)
        self.assertIsNotNone(writer)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "next_action.json"
            live = {
                "id": ISSUE_ID,
                "name": "Board truth",
                "state": "In Progress",
                "state_group": "started",
                "priority": "urgent",
                "sequence_id": 792,
            }
            with mock.patch.object(session_routes, "NEXT_ACTION_PATH", target), \
                 mock.patch.object(session_routes.plane_routes, "get_plane_issue", return_value=live):
                result = writer(request_type(issue_id=ISSUE_ID))

            stored = json.loads(target.read_text())
            self.assertTrue(result["verified"])
            self.assertEqual(stored["source"], "live_plane_readback")
            self.assertEqual(stored["issue_id"], ISSUE_ID)
            self.assertEqual(
                stored["action"],
                f"KAI-792 ({ISSUE_ID}, In Progress, urgent) — Board truth",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

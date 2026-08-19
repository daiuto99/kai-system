import unittest
import json
import tempfile
from pathlib import Path
from unittest import mock

import db
import main


class GatePollerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmpdir.name) / "orchestrator.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.tmpdir.cleanup()

    def _seed_gate(self, *, gate_id, job_status, step_status, opened_at):
        job_id = f"job-{gate_id}"
        step_id = f"step-{gate_id}"
        conn = db.get_conn()
        conn.execute(
            """INSERT INTO jobs
               (id,type,inputs,status,approval_policy,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (job_id, "wordpress_publish_homepage", "{}", job_status,
             "council_gate", opened_at, opened_at),
        )
        conn.execute(
            """INSERT INTO steps
               (id,job_id,name,status,created_at,updated_at)
               VALUES (?,?,?,?,?,?)""",
            (step_id, job_id, "dev_gate", step_status, opened_at, opened_at),
        )
        conn.execute(
            """INSERT INTO gates
               (id,job_id,step_id,gate_type,brief,callback_url,status,opened_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (gate_id, job_id, step_id, "dev", "{}", "http://callback",
             "pending", opened_at),
        )
        conn.commit()
        conn.close()

    def test_first_poll_happens_before_first_sleep(self):
        events = []
        with mock.patch.object(
            main, "_poll_gate_cycle", side_effect=lambda: events.append("poll")
        ):
            main._gate_poller_loop(
                max_cycles=2,
                sleep_fn=lambda delay: events.append(("sleep", delay)),
            )

        self.assertEqual(events[0], "poll")
        self.assertEqual(events, ["poll", ("sleep", 30), "poll"])

    def test_repeated_failures_back_off_and_alert_once(self):
        sleeps = []
        with (
            mock.patch.object(
                main,
                "_poll_gate_cycle",
                side_effect=RuntimeError("forced poll failure"),
            ),
            mock.patch.object(main, "_notify") as alert,
        ):
            main._gate_poller_loop(max_cycles=4, sleep_fn=sleeps.append)

        self.assertEqual(sleeps, [30, 60, 120])
        alert.assert_called_once()
        self.assertIn("3 consecutive", alert.call_args.args[0])

    def test_success_resets_backoff(self):
        sleeps = []
        with (
            mock.patch.object(
                main,
                "_poll_gate_cycle",
                side_effect=[RuntimeError("one"), RuntimeError("two"), None, None],
            ),
            mock.patch.object(main, "_notify") as alert,
        ):
            main._gate_poller_loop(max_cycles=4, sleep_fn=sleeps.append)

        self.assertEqual(sleeps, [30, 60, 30])
        alert.assert_not_called()

    def test_terminal_parent_gate_is_orphaned_without_false_alert(self):
        self._seed_gate(
            gate_id="stale-orphan",
            job_status="failed_permanent",
            step_status="failed_permanent",
            opened_at="2026-05-17T02:07:59Z",
        )

        with (
            mock.patch("httpx.get") as http_get,
            mock.patch.object(main, "_notify") as alert,
        ):
            main._gate_poller_loop(max_cycles=3, sleep_fn=lambda _delay: None)

        http_get.assert_not_called()
        alert.assert_not_called()
        conn = db.get_conn()
        gate = conn.execute(
            "SELECT status,resolution FROM gates WHERE id='stale-orphan'"
        ).fetchone()
        conn.close()
        self.assertEqual(gate["status"], "orphaned")
        self.assertEqual(
            json.loads(gate["resolution"])["reason"],
            "parent job is failed_permanent",
        )

    def test_old_live_wedged_gate_still_alerts(self):
        self._seed_gate(
            gate_id="genuine-wedge",
            job_status="running",
            step_status="awaiting_gate",
            opened_at="2026-05-17T02:07:59Z",
        )
        response = mock.Mock(status_code=404)

        with (
            mock.patch("httpx.get", return_value=response) as http_get,
            mock.patch.object(main, "_notify") as alert,
        ):
            main._gate_poller_loop(max_cycles=3, sleep_fn=lambda _delay: None)

        self.assertEqual(http_get.call_count, 3)
        alert.assert_called_once()
        self.assertIn("3 consecutive", alert.call_args.args[0])
        conn = db.get_conn()
        status = conn.execute(
            "SELECT status FROM gates WHERE id='genuine-wedge'"
        ).fetchone()["status"]
        conn.close()
        self.assertEqual(status, "pending")


if __name__ == "__main__":
    unittest.main()

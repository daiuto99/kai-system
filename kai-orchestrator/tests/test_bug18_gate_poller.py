import unittest
from unittest import mock

import main


class GatePollerTests(unittest.TestCase):
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
            mock.patch.object(main, "_post_slack") as alert,
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
            mock.patch.object(main, "_post_slack") as alert,
        ):
            main._gate_poller_loop(max_cycles=4, sleep_fn=sleeps.append)

        self.assertEqual(sleeps, [30, 60, 30])
        alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""S1-B4 (audit #03) — delivery contract: a page counts as delivered ONLY on
API-200 + ok:true + a real message_id readback (the receipt). Self-contained:
stubs the raw transport, so it runs identically on host and in-container."""
import sys
import unittest
from pathlib import Path

for p in (str(Path(__file__).resolve().parents[2] / "shared"), "/app/shared"):
    if p not in sys.path:
        sys.path.insert(0, p)

import notify_gateway as ng  # noqa: E402


class _Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._b = body

    def json(self):
        return self._b


class DeliveryContract(unittest.TestCase):
    def setUp(self):
        self._orig = (ng._test_mode, ng._secret, ng._raw_post)
        ng._test_mode = lambda: False
        ng._secret = lambda name: "tok" if name == "telegram_bot_token" else "123"

    def tearDown(self):
        ng._test_mode, ng._secret, ng._raw_post = self._orig

    def _send(self, resp, **kw):
        ng._raw_post = lambda *a, **k: resp
        return ng.send_message("123", "hi", reason="test", **kw)

    def test_ok_with_message_id_is_delivered(self):
        r = self._send(_Resp(200, {"ok": True, "result": {"message_id": 42}}))
        self.assertTrue(r["delivered"])
        self.assertEqual(r["message_id"], 42)

    def test_ok_without_message_id_is_not_delivered(self):
        # The core B4 tightening: API accepted (ok) but no receipt -> NOT paged.
        r = self._send(_Resp(200, {"ok": True, "result": {}}))
        self.assertFalse(r["delivered"])

    def test_ok_false_is_not_delivered(self):
        r = self._send(_Resp(200, {"ok": False}))
        self.assertFalse(r["delivered"])

    def test_non_200_is_not_delivered(self):
        r = self._send(_Resp(500, {"ok": True, "result": {"message_id": 1}}))
        self.assertFalse(r["delivered"])

    def test_disable_notification_threads_to_transport(self):
        captured = {}

        def cap(chat_id, text, reply_markup, parse_mode, disable_notification=False):
            captured["dn"] = disable_notification
            return _Resp(200, {"ok": True, "result": {"message_id": 7}})

        ng._raw_post = cap
        ng.send_message("123", "hi", reason="t", disable_notification=True)
        self.assertTrue(captured["dn"])


if __name__ == "__main__":
    unittest.main()

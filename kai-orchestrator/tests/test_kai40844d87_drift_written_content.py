"""KAI-40844d87 — brand_drift must judge the content that actually SHIPPED.

Page 37 (the71c, P-1 production run): the report carried two foreign_color
blues that were nowhere in the written page. Cause: `_run_brand_drift` ran on
the pre-write string, before WordPress's own save-time sanitization dropped the
malformed style attributes those hexes lived in. The check now runs on WP's
write response (edit context -> content.raw), with the submitted body as the
fail-safe when WP echoes no content.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

import capabilities.wordpress as wp

BLUE_IN = '<p style="color:#1E3A8A;font-family:x"y">hi</p>'   # malformed; WP strips the attr
STORED = "<p>hi</p>"                                          # what WP kept


def _resp(ok=True, data=None, status_code=200, body_preview=""):
    return SimpleNamespace(ok=ok, data=data, status_code=status_code,
                           body_preview=body_preview)


def _creds():
    return {"fqdn": "example.test", "app_password": "not-a-secret"}


class WrittenContentSelection(unittest.TestCase):
    def test_prefers_raw(self):
        self.assertEqual(
            wp._written_content({"content": {"raw": STORED, "rendered": "<p>r</p>"}},
                                BLUE_IN),
            STORED)

    def test_falls_back_to_rendered(self):
        self.assertEqual(
            wp._written_content({"content": {"raw": "  ", "rendered": STORED}}, BLUE_IN),
            STORED)

    def test_accepts_plain_string_content(self):
        self.assertEqual(wp._written_content({"content": STORED}, BLUE_IN), STORED)

    def test_falls_back_to_submitted_when_wp_echoes_nothing(self):
        for data in ({}, {"content": {}}, {"content": {"raw": ""}}, {"content": ""}, None):
            self.assertEqual(wp._written_content(data, BLUE_IN), BLUE_IN)


class DriftRunsOnWrittenContent(unittest.TestCase):
    """The regression itself: the checked body is WP's stored body, not ours."""

    def _capture(self):
        seen = {}

        def fake(site, property, content):
            seen["content"] = content
            return {"checked": True, "governed": True, "drift": False, "findings": []}

        return seen, fake

    def test_create_page_checks_what_wp_stored(self):
        seen, fake = self._capture()
        write = _resp(data={"id": 37, "link": "https://example.test/?p=37",
                            "status": "draft", "content": {"raw": STORED}})
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "_run_brand_drift", side_effect=fake), \
             mock.patch.object(wp, "safe_request", return_value=write):
            res = wp.create_page(site="the71company", title="Home", content=BLUE_IN,
                                 creds=_creds(), caller="test", property="the71c")
        self.assertTrue(res.ok)
        self.assertEqual(seen["content"], STORED)
        self.assertNotIn("#1E3A8A", seen["content"])   # the false-flagged blue
        self.assertIs(res.data["brand_drift"]["checked"], True)

    def test_create_page_falls_back_to_submitted_body(self):
        seen, fake = self._capture()
        write = _resp(data={"id": 37, "status": "draft"})   # no content echoed
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "_run_brand_drift", side_effect=fake), \
             mock.patch.object(wp, "safe_request", return_value=write):
            res = wp.create_page(site="the71company", title="Home", content=BLUE_IN,
                                 creds=_creds(), caller="test", property="the71c")
        self.assertTrue(res.ok)
        self.assertIn(BLUE_IN, seen["content"])
        self.assertIn("kai-marker:", seen["content"])      # the body we submitted

    def test_update_page_checks_what_wp_stored(self):
        seen, fake = self._capture()
        seq = [_resp(data={"status": "draft"}),             # GET drafts-only probe
               _resp(data={"id": 42, "status": "draft", "content": {"raw": STORED}})]
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "_run_brand_drift", side_effect=fake), \
             mock.patch.object(wp, "safe_request", side_effect=seq):
            res = wp.update_page(site="the71company", page_id=42, content=BLUE_IN,
                                 creds=_creds(), caller="test", property="the71c")
        self.assertTrue(res.ok)
        self.assertEqual(seen["content"], STORED)

    def test_failed_write_never_reports_drift(self):
        """Nothing shipped -> nothing to judge, and no crash on the failure path."""
        seen, fake = self._capture()
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "_run_brand_drift", side_effect=fake), \
             mock.patch.object(wp, "safe_request",
                               return_value=_resp(ok=False, status_code=500,
                                                  body_preview="boom")):
            res = wp.create_page(site="the71company", title="Home", content=BLUE_IN,
                                 creds=_creds(), caller="test", property="the71c")
        self.assertFalse(res.ok)
        self.assertEqual(res.error["type"], "create_failed")
        self.assertNotIn("content", seen)


class EndToEndAgainstTheRealDetector(unittest.TestCase):
    """With the real detector: the stripped blue must not surface as a warning."""

    def test_no_foreign_color_for_a_hex_wp_dropped(self):
        write = _resp(data={"id": 37, "status": "draft", "content": {"raw": STORED}})
        with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
             mock.patch.object(wp, "safe_request", return_value=write):
            res = wp.create_page(site="the71company", title="Home", content=BLUE_IN,
                                 creds=_creds(), caller="test", property="the71c")
        self.assertTrue(res.ok)
        report = res.data["brand_drift"]
        if not report.get("checked"):
            self.skipTest("the71c BUILD_PROFILE not readable here (ungoverned) — "
                          "nothing for the real detector to judge")
        blues = [f for f in report.get("findings", [])
                 if f.get("type") == "foreign_color" and "1E3A8A" in f.get("detail", "")]
        self.assertEqual(blues, [])


if __name__ == "__main__":
    unittest.main()

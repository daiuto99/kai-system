"""Job 6af6c143 (2026-08-28, AR-3 slice 5): three fail-closed regressions.

1. resume() must HALT the chain the moment a step lands failed_permanent —
   it ran create_page after generate_page failed (snapshot-staleness bug).
2. wordpress.create_page / update_page must refuse a contentless write —
   a 33-byte marker-only draft reached the live site.
3. wp_generate render ladder: trimmed per-section style, empty replies are
   retryable, final attempt drops the guide entirely.
"""
import tempfile
from pathlib import Path
from unittest import mock

import db
from models import CapabilityResult, StepDef
from workflow_base import Workflow


class TwoStepFailShape(Workflow):
    """Step 1 fails permanently; step 2 must never execute."""

    name = "wordpress.build_page_draft"
    steps = [
        StepDef("generate_page", "wordpress.generate_blocks", max_retries=0),
        StepDef("create_page_draft", "wordpress.create_page", max_retries=0),
    ]
    executed: list

    def execute_step(self, step_def, step):
        type(self).executed.append(step_def.name)
        if step_def.name == "generate_page":
            return CapabilityResult(ok=False, status="failed_permanent",
                                    error={"type": "generation_failed"})
        return CapabilityResult(ok=True, status="succeeded", data={"id": 999})


def test_resume_halts_chain_after_failed_permanent_step():
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.object(db, "DB_PATH", Path(tmpdir) / "orchestrator.db"):
            db.init_db()
            TwoStepFailShape.executed = []
            wf = TwoStepFailShape.start({})
            wf.resume()

            conn = db.get_conn()
            try:
                job = conn.execute(
                    "SELECT status FROM jobs WHERE id=?", (wf.job_id,)
                ).fetchone()
                steps = {r["name"]: r["status"] for r in conn.execute(
                    "SELECT name, status FROM steps WHERE job_id=?", (wf.job_id,)
                ).fetchall()}
            finally:
                conn.close()

    assert TwoStepFailShape.executed == ["generate_page"], \
        "create_page_draft executed after a failed_permanent predecessor"
    assert steps["generate_page"] == "failed_permanent"
    assert steps["create_page_draft"] == "pending"
    assert job["status"] == "failed_permanent"


def _refuse_transport(*a, **k):
    raise AssertionError("transport reached despite empty content")


def test_create_page_refuses_empty_content():
    import capabilities.wordpress as wp
    with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
         mock.patch.object(wp, "safe_request", _refuse_transport):
        res = wp.create_page("the71c", "T", "   \n", creds={"fqdn": "x", "app_password": "y"})
    assert not res.ok and res.status == "failed_permanent"
    assert res.error["type"] == "empty_content"


def test_update_page_refuses_empty_content():
    import capabilities.wordpress as wp
    with mock.patch.object(wp, "wp_write_preflight", lambda *a, **k: None), \
         mock.patch.object(wp, "safe_request", _refuse_transport):
        res = wp.update_page("the71c", 32, "", creds={"fqdn": "x", "app_password": "y"})
    assert not res.ok and res.status == "failed_permanent"
    assert res.error["type"] == "empty_content"


GUIDE = """# style.md — X
## 1. Positioning
Long positioning prose that renders do not need.
## 2. Color Palette
| cream | #F5F1EA |
## 3. Typography
| Display | Bricolage |
## 5. Voice rules
Direct, plain.
## 7. Page-pattern precedents
- **Hero** — espresso gradient, ember eyebrow.
- **Services** — white cards off cream.
"""


def test_style_for_section_trims_to_load_bearing_subset():
    import capabilities.wp_generate as g
    out = g._style_for_section(GUIDE, "hero")
    assert "#F5F1EA" in out and "Bricolage" in out and "Direct, plain." in out
    assert "espresso gradient" in out
    assert "white cards" not in out          # other sections' patterns dropped
    assert "positioning prose" not in out    # prose sections dropped
    assert g._style_for_section(None, "hero") is None
    # Codex-found defect: an unmatched type must not drag in every pattern.
    out2 = g._style_for_section(GUIDE, "faq")
    assert "espresso gradient" not in out2 and "white cards" not in out2
    assert "#F5F1EA" in out2                 # tokens still present


def test_render_ladder_drops_style_on_final_attempt():
    import capabilities.wp_generate as g
    seen_styles = []
    valid = '<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->'

    def fake_render(section, style, *, error=None):
        seen_styles.append(style)
        if style is not None:
            raise g.GenerateError("renderer returned empty markup for section hero")
        return valid

    with mock.patch.object(g, "_render", fake_render):
        out = g._render_valid_section({"type": "hero"}, GUIDE)

    assert out == valid
    assert seen_styles[-1] is None, "final attempt should drop the guide"
    assert all(s is not None for s in seen_styles[:-1])


def test_generate_capability_verification_meets_engine_contract():
    """Job c486a810: engine refuses succeeded without verified=True; the raw
    validate report says 'valid' — the capability must wrap it."""
    import capabilities.wp_generate as g
    from capabilities import get_capability
    fn = get_capability("wordpress.generate_blocks")
    fake = {"content": "<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->",
            "plan": [], "sections": [], "style_used": False,
            "validation": {"valid": True, "findings": [], "summary": "ok"}}
    with mock.patch.object(g, "generate_blocks", lambda *a, **k: fake):
        res = fn(site="the71c", brief="b")
    assert res.ok and res.verification["verified"] is True
    assert res.verification["evidence"]["valid"] is True


def test_normalize_fonts_canonicalizes_brand_declarations():
    """Page 34: fallback stacks + slug/case variants -> 14 font_drift highs.
    Brand-mentioning declarations canonicalize; foreign ones are left for the
    drift detector to judge."""
    import capabilities.wp_generate as g
    fonts = ["Bricolage Grotesque", "IBM Plex Sans", "IBM Plex Mono"]
    out = g._normalize_fonts(
        "style=\"font-family:'Bricolage Grotesque', -apple-system, 'Segoe UI', Roboto\">"
        "<p style=\"font-family: ibm-plex-sans\">x</p>"
        "<h2 style=\"font-family: ibm-plex-mono\">Core</h2>"
        "<div style=\"font-family: Comic Sans MS\">y</div>", fonts)
    assert "font-family:'Bricolage Grotesque'\"" in out
    assert "Segoe UI" not in out and "Roboto" not in out
    assert "font-family:'IBM Plex Sans'" in out
    assert "font-family:'IBM Plex Mono'" in out
    # Non-brand declarations force to the BODY face: by contract the brand
    # faces are the only families generated output may carry (page 34: pure
    # system stacks survived the mention-only rule and kept 4 drift highs).
    assert "Comic Sans MS" not in out
    assert g._normalize_fonts("<p>x</p>", fonts) == "<p>x</p>"
    out3 = g._normalize_fonts(
        "style=\"font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif\">", fonts)
    assert out3 == "style=\"font-family:'IBM Plex Sans'\">"

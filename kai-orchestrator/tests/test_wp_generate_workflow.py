"""AR-3 / KAI-965 — build_page_draft generate_page wiring tests (in-container).

Verifies the generator is wired into the governed drafts-only workflow: the step
exists BEFORE create_page_draft, its handler exposes the generated markup as
`page_content` (what create_page reads), and a missing brief fails closed.
"""
import sys


sys.path.insert(0, "/app")

import capabilities
from models import CapabilityResult
from workflows import wordpress_publish_homepage as wph
from workflows.wordpress_build_page_draft import BuildPageDraftWorkflow


def test_generate_step_present_before_create():
    names = [s.name for s in BuildPageDraftWorkflow.steps]
    assert "generate_page" in names
    assert names.index("generate_page") < names.index("create_page_draft")


def test_handler_populates_page_content(monkeypatch):
    def fake_get(name):
        assert name == "wordpress.generate_blocks"

        def cap(site, brief, slug=None):
            return CapabilityResult(
                ok=True, status="succeeded",
                data={"content": "<!-- wp:paragraph --><p>x</p><!-- /wp:paragraph -->",
                      "validation": {"valid": True}})
        return cap

    monkeypatch.setattr(capabilities, "get_capability", fake_get)
    ctx = {"site": "the71c", "brand_slug": "the71c", "brief_text": "make a page"}
    r = wph.PublishHomepageWorkflow._step_generate_page(None, ctx)
    assert r.ok
    assert r.data["page_content"].startswith("<!-- wp:paragraph")


def test_no_brief_fails_closed():
    r = wph.PublishHomepageWorkflow._step_generate_page(None, {"site": "the71c"})
    assert not r.ok
    assert r.error["type"] == "no_brief"


def test_generation_failure_propagates(monkeypatch):
    def fake_get(name):
        def cap(site, brief, slug=None):
            return CapabilityResult(ok=False, status="failed_recoverable",
                                    error={"type": "generation_failed"})
        return cap

    monkeypatch.setattr(capabilities, "get_capability", fake_get)
    r = wph.PublishHomepageWorkflow._step_generate_page(
        None, {"site": "the71c", "brief_text": "b"})
    assert not r.ok
    assert r.error["type"] == "generation_failed"

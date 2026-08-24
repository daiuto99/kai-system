"""KAI-39 — a site key whose brand profile lives under a different slug must be
brand-drift-checked against that profile, not silently go ungoverned.

The hole KAI-36 exposed: `wordpress.create_page` accepts a `property` brand-slug
override, but the build_page_draft workflow never passed it, so a write against
the71company (site key, no BUILD_PROFILE of its own) read as governed:False even
though the71 brand IS seeded under slug the71c. Fix: sites.json maps the key →
brand_slug, load_config surfaces it, and _step_create_page threads it as
`property`. A genuinely-unseeded site (no brand_slug) keeps the fail-safe.
"""
import capabilities.wordpress as wp
from workflows.wordpress_build_page_draft import BuildPageDraftWorkflow

# On-brand the71 content (declared face, brand ground/text, logo present) — no drift.
CLEAN = ("<style>body{font-family:'IBM Plex Sans';background:#F5F1EA;color:#1C1815}</style>"
         "<img src=71-Company.png>")


def test_threading_the71company_via_slug_is_governed():
    # the payoff: threading the71c as property governs the71company
    r = wp._run_brand_drift("the71company", "the71c", CLEAN)
    assert r["governed"] is True
    assert r["checked"] is True
    assert r["drift"] is False


def test_the71company_without_slug_is_ungoverned():
    # documents the hole the fix closes: no property → _site_key(the71company) → no profile
    r = wp._run_brand_drift("the71company", None, CLEAN)
    assert r["governed"] is False
    assert r["checked"] is False


def test_genuinely_unseeded_site_keeps_failsafe():
    # sette-uno has no brand_slug and no profile — must stay ungoverned (WP-20.6d fail-safe)
    r = wp._run_brand_drift("sette-uno", None, CLEAN)
    assert r["governed"] is False
    assert r["checked"] is False


def test_step_create_page_threads_brand_slug_as_property(monkeypatch):
    """_step_create_page must pass ctx['brand_slug'] to create_page as `property`."""
    captured = {}

    def fake_create_page(**kwargs):
        captured.update(kwargs)
        class R:
            ok = True
            verification = {"verified": True}
            data = {"id": 1, "brand_drift": {}}
        return R()

    import capabilities
    monkeypatch.setattr(capabilities, "get_capability", lambda name: fake_create_page)

    wf = BuildPageDraftWorkflow.__new__(BuildPageDraftWorkflow)  # no DB
    ctx = {"site": "the71company", "brand_slug": "the71c", "creds": {"fqdn": "x"},
           "page_title": "T", "page_content": "<p>hi</p>"}
    wf._step_create_page(ctx)
    assert captured.get("property") == "the71c"


def test_step_create_page_property_input_fallback(monkeypatch):
    """With no brand_slug, an explicit `property` input still wins; else None."""
    captured = {}

    def fake_create_page(**kwargs):
        captured.update(kwargs)
        class R:
            ok = True
            verification = {"verified": True}
            data = {"id": 1}
        return R()

    import capabilities
    monkeypatch.setattr(capabilities, "get_capability", lambda name: fake_create_page)

    wf = BuildPageDraftWorkflow.__new__(BuildPageDraftWorkflow)
    ctx = {"site": "sette-uno", "property": "explicit", "creds": {"fqdn": "x"},
           "page_title": "T", "page_content": "<p>hi</p>"}
    wf._step_create_page(ctx)
    assert captured.get("property") == "explicit"

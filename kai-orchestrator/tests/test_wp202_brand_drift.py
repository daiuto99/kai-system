"""WP-20.2 — brand-drift detector + brand token parser.

Proves the detector catches the WP-4/5/6 failure shape (off-brand font, off-brand
ground, missing logo) on authored page content, passes a genuinely on-brand page,
and degrades to a visible 'not checked' state (never a crash, never a silent pass)
when a property has no machine-readable brand spec. the71c is the seeded property.
"""
import brand_profile
import brand_drift

# On-brand: declared families, brand palette (cream ground / ink text / ember accent), logo present.
CLEAN_HTML = """
<style>
@font-face { font-family: 'Bricolage Grotesque'; src: url(/f/bricolage.woff2); }
body { font-family: 'IBM Plex Sans', sans-serif; background: #F5F1EA; color: #1C1815; }
h1 { font-family: 'Bricolage Grotesque', sans-serif; }
.eyebrow { font-family: 'IBM Plex Mono', monospace; color: #C05621; }
</style>
<img src="/wp-content/uploads/2026/06/71-Company.png" alt="The 71 Company">
<h1>Strategy-led consulting</h1>
"""

# Off-brand: generic sans (the tell), a foreign ground color, no brand colors, no logo.
OFFBRAND_HTML = """
<style>
body { font-family: Arial, sans-serif; background: #123456; color: #ffffff; }
h1 { font-family: 'Times New Roman', serif; }
</style>
<h1>Coming Soon</h1>
"""


def test_parse_the71c_tokens():
    t = brand_profile.parse("the71c")
    assert t is not None
    assert "Bricolage Grotesque" in t["fonts"]
    assert "#C05621" in t["palette"]        # ember, normalized upper
    assert "#F5F1EA" in t["required_colors"]
    assert t["logo"] == "71 Company.png"


def test_parse_missing_property_is_none():
    assert brand_profile.parse("does-not-exist-xyz") is None


def test_normalize_hex():
    assert brand_profile.normalize_hex("#c05621") == "#C05621"
    assert brand_profile.normalize_hex("#abc") == "#AABBCC"
    assert brand_profile.normalize_hex("not-a-color") is None


def test_clean_page_has_no_drift():
    r = brand_drift.detect("the71c", CLEAN_HTML)
    assert r["checked"] is True
    assert r["drift"] is False, r["findings"]
    assert r["findings"] == []


def test_offbrand_page_flags_font_and_brand_loss():
    r = brand_drift.detect("the71c", OFFBRAND_HTML)
    assert r["checked"] is True
    assert r["drift"] is True
    types = {f["type"] for f in r["findings"]}
    assert "font_drift" in types          # Arial / Times New Roman
    assert "brand_color_loss" in types    # uses none of cream/ink/ember
    # blocking findings are high severity
    assert any(f["severity"] == "high" for f in r["findings"])


def test_foreign_color_is_advisory_not_blocking_on_its_own():
    # A page that keeps the brand (fonts + required colors + logo) but adds one
    # stray hex should warn, not block.
    html = CLEAN_HTML.replace("</style>", ".note { color: #abcdef; }</style>")
    r = brand_drift.detect("the71c", html)
    assert r["checked"] is True
    assert r["drift"] is False
    assert any(f["type"] == "foreign_color" for f in r["findings"])


def test_no_profile_is_visible_not_crash():
    r = brand_drift.detect("bogus-property-zzz", "<h1>hi</h1>")
    assert r["checked"] is False
    assert r["drift"] is False
    assert r["findings"][0]["type"] == "no_profile"


def test_path_traversal_slug_is_safe():
    r = brand_drift.detect("../../00_System/KEYSTONE", "<h1>hi</h1>")
    assert r["checked"] is False
    assert r["findings"][0]["type"] == "no_profile"

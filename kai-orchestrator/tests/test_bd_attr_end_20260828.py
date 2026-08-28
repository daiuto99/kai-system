"""2026-08-28 (page 34): a font-family declaration that ENDS a style attribute
with no trailing semicolon must not swallow following HTML into the family
name and flag the brand font as foreign."""
import sys
sys.path.insert(0, "/kai-system/shared")
import brand_drift


def test_attr_final_declaration_parses_cleanly():
    html = ('<p style="color:#1C1815;font-family:\'IBM Plex Sans\'">'
            "Build better products</p>")
    fams = brand_drift._families_in(html)
    assert fams == {"IBM Plex Sans"}, fams


def test_semicolon_terminated_still_works():
    html = '<p style="font-family:\'Bricolage Grotesque\';color:#000">x</p>'
    assert brand_drift._families_in(html) == {"Bricolage Grotesque"}

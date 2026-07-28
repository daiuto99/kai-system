"""WP-20.1 — per-property BUILD_PROFILE auto-load.

Proves tier5_standing_context() loads a property's canonical brand spec
(60_Council/properties/<slug>/BUILD_PROFILE.md) as a stable
`property_build_profile` block when a request targets that property, and
degrades visibly (a warning, never a silent skip, never an exception) when the
targeted property has no spec. the71c is the seeded first property (KAI-20).
"""
import context_service


def test_property_profile_loads_for_the71c():
    # A WP request targeting the71c pulls that property's brand spec on top of
    # the agency build_profile.
    result = context_service.tier5_standing_context("kai", property="the71c")

    assert "error" not in result
    assert "property_build_profile" in result["blocks"], result["blocks"]
    text = result["stable_text"]
    # Real brand data from the Leo-approved style.md, not invented values.
    assert "the71c" in text
    assert "Bricolage Grotesque" in text
    assert "#C05621" in text  # ember accent token
    assert "OVERRIDES the agency" in text  # override semantics stated to the model


def test_missing_property_profile_warns_not_crashes():
    result = context_service.tier5_standing_context("kai", property="does-not-exist-xyz")

    assert "error" not in result
    assert "property_build_profile" not in result["blocks"]
    assert any(w.startswith("property_build_profile_missing") for w in result["warnings"]), \
        result["warnings"]


def test_path_traversal_slug_is_sanitized():
    # A hostile slug can never escape the properties/ dir; it resolves to a
    # sanitized (here: empty/reduced) slug and warns rather than reading arbitrary files.
    result = context_service.tier5_standing_context("kai", property="../../00_System/KEYSTONE")

    assert "error" not in result
    assert "property_build_profile" not in result["blocks"]
    assert any(w.startswith("property_build_profile_missing") for w in result["warnings"])


def test_no_property_means_no_property_block_no_warning():
    result = context_service.tier5_standing_context("kai")

    assert "error" not in result
    assert "property_build_profile" not in result["blocks"]
    assert not any(w.startswith("property_build_profile_missing") for w in result["warnings"])

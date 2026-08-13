"""WP-20.6d — ungoverned-property fail-safe.

A brand-bearing write against a property with NO BUILD_PROFILE must be flagged
`governed: False` (visible in the result), never a clean-looking pass; a seeded
property (the71c) is `governed: True`. This is the write-path twin of WP-20.2's
"visible not-checked" degradation — proving it here stops an ungoverned write
from being indistinguishable from a genuinely clean one.
"""
import capabilities.wordpress as wp

# On-brand the71c content (declared face, brand ground/text, logo present) — no drift.
CLEAN_THE71C = (
    "<style>body{font-family:'IBM Plex Sans';background:#F5F1EA;color:#1C1815}</style>"
    "<img src=71-Company.png>"
)


def test_ungoverned_property_flagged():
    r = wp._run_brand_drift("sette-uno", "sette-uno", "<h1>hello</h1>")
    assert r["governed"] is False   # the fail-safe: not a clean-looking pass
    assert r["checked"] is False
    assert r["drift"] is False       # not a hard block — drafts iterate


def test_seeded_property_is_governed():
    r = wp._run_brand_drift("the71c", "the71c", CLEAN_THE71C)
    assert r["governed"] is True
    assert r["checked"] is True
    assert r["drift"] is False

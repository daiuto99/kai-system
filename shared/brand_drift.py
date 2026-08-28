"""WP-20.2 — brand-drift detector.

Runs at the WordPress write chokepoint (`wordpress.create_page`) on the page HTML
KAI is about to author, and compares it against the property's brand tokens
(WP-20.1 / brand_profile). This is the surface the WP-4/5/6 incident actually
failed on: KAI hand-authored an off-brand template (generic bold sans, wrong
ground) and nothing checked it before the write. The detector makes that check
mechanical instead of eyeball.

Scope of THIS detector (v1): checks that live entirely on the authored content —
- font drift  : a concrete, non-brand font family appears (the generic-sans tell)
- palette     : hex colors outside the brand palette (foreign-color drift, warn)
- brand loss  : a page that uses NONE of the required brand colors (high)
- logo        : the property logo asset is not referenced (warn)

Deferred to WP-20.2b (documented, not silently dropped — need live-site reads):
theme.json global-styles palette/type match, logo/favicon media-hash match, and
Coming-Soon template-vs-canonical comparison (architecture §4.3).

`detect()` is pure (no I/O beyond reading the property profile, no network) so it
is fully unit-testable and safe to call inline on the write path.
"""
from __future__ import annotations

import re

import brand_profile

# Generic CSS font keywords / system-stack tokens that are NEVER counted as a
# branded family — listing one of these is a fallback, not drift.
_GENERIC_FONTS = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
    "ui-sans-serif", "ui-monospace", "ui-serif", "ui-rounded", "-apple-system",
    "blinkmacsystemfont", "inherit", "initial", "unset", "revert", "emoji",
    "math", "fangsong",
}

# Capture stops at ; { } AND a double quote: a declaration that ends a
# style attribute with no trailing semicolon (font-family:'X'">...) used
# to swallow the following HTML into the "family name" and flag the brand
# font itself as foreign (page 34, 2026-08-28 — 4 false font_drift highs).
_FONT_FAMILY_DECL = re.compile(r"font-family\s*:\s*([^;{}\"]+)", re.IGNORECASE)
_FONT_FACE_NAME = re.compile(
    r"@font-face\s*\{[^}]*?font-family\s*:\s*['\"]?([^;'\"}]+)", re.IGNORECASE | re.DOTALL
)
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


def _families_in(html: str) -> set[str]:
    """Concrete (non-generic) font family names referenced anywhere in the HTML."""
    found: set[str] = set()
    raw: list[str] = []
    for decl in _FONT_FAMILY_DECL.findall(html):
        raw.extend(decl.split(","))
    raw.extend(_FONT_FACE_NAME.findall(html))
    for token in raw:
        name = token.strip().strip("'\"").strip()
        if not name:
            continue
        if name.lower() in _GENERIC_FONTS:
            continue
        found.add(name)
    return found


def _hexes_in(html: str) -> set[str]:
    out: set[str] = set()
    for h in _HEX.findall(html):
        n = brand_profile.normalize_hex(h)
        if n:
            out.add(n)
    return out


def _logo_referenced(html: str, logo: str) -> bool:
    """True if the logo asset is referenced. WordPress munges spaces to hyphens on
    upload (`71 Company.png` -> `71-Company.png`), so accept either form and the
    bare stem."""
    if not logo:
        return True  # nothing to require
    stem = logo.rsplit(".", 1)[0]
    variants = {
        logo, logo.replace(" ", "-"), logo.replace(" ", "%20"),
        stem, stem.replace(" ", "-"),
    }
    low = html.lower()
    return any(v.lower() in low for v in variants if v)


def detect(slug: str, html: str, *, logo_required: bool = True) -> dict:
    """Compare authored `html` against property `slug`'s brand tokens.

    Returns a structured report:
      {slug, checked: bool, drift: bool, findings: [{type, severity, detail}], summary}
    `drift` is True iff any finding is high-severity. `checked` is False when the
    property has no machine-readable brand spec (caller should treat as a warning,
    same ethos as WP-20.1's property_build_profile_missing).
    """
    tokens = brand_profile.parse(slug)
    if tokens is None:
        return {
            "slug": brand_profile._safe_slug(slug),
            "checked": False,
            "drift": False,
            "findings": [{
                "type": "no_profile",
                "severity": "warn",
                "detail": (f"no machine-readable brand spec for property "
                           f"'{slug}' — brand cannot be verified (escalate Creative "
                           f"gate, do not improvise)"),
            }],
            "summary": "brand not checked: no property spec",
        }

    html = html or ""
    findings: list[dict] = []

    # ── font drift ──────────────────────────────────────────────────────────
    declared = {f.lower() for f in tokens.get("fonts", [])}
    for fam in sorted(_families_in(html)):
        if fam.lower() not in declared:
            findings.append({
                "type": "font_drift",
                "severity": "high",
                "detail": (f"non-brand font family '{fam}' — allowed: "
                           f"{tokens.get('fonts', [])}"),
            })

    # ── palette: foreign colors (warn) + brand loss (high) ──────────────────
    palette = set(tokens.get("palette", []))
    page_hexes = _hexes_in(html)
    for hx in sorted(page_hexes - palette):
        findings.append({
            "type": "foreign_color",
            "severity": "warn",
            "detail": f"color {hx} is not in the brand palette",
        })
    required = set(tokens.get("required_colors", []))
    if required and page_hexes and not (page_hexes & required):
        findings.append({
            "type": "brand_color_loss",
            "severity": "high",
            "detail": (f"page uses none of the required brand colors "
                       f"{sorted(required)} — brand ground/accent is missing"),
        })

    # ── logo presence ───────────────────────────────────────────────────────
    if logo_required and not _logo_referenced(html, tokens.get("logo", "")):
        findings.append({
            "type": "logo_missing",
            "severity": "warn",
            "detail": f"property logo '{tokens.get('logo')}' is not referenced on the page",
        })

    drift = any(f["severity"] == "high" for f in findings)
    highs = sum(1 for f in findings if f["severity"] == "high")
    warns = sum(1 for f in findings if f["severity"] == "warn")
    summary = ("brand-drift: clean" if not findings
               else f"brand-drift: {highs} blocking, {warns} advisory")
    return {
        "slug": tokens.get("slug", slug),
        "checked": True,
        "drift": drift,
        "findings": findings,
        "summary": summary,
    }

"""WP-20.2 — machine-readable brand tokens for a WordPress property.

The per-property brand contract lives as prose in
`60_Council/properties/<slug>/BUILD_PROFILE.md` (WP-20.1, loaded into advisor
context). That same file carries a single fenced ```json block — the
machine-readable projection the brand-drift detector reads. Keeping the tokens
INSIDE the markdown keeps one brand source of truth (no drift between a prose
contract and a sidecar JSON), while giving the detector something it can parse
deterministically instead of scraping prose tables.

`parse()` is side-effect-free and fail-soft: a missing property, a missing JSON
block, or a malformed block all return None (the caller decides whether that is a
warning) — never raises, never touches a path outside the properties dir.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# Mirrors context_service.COUNCIL_PATH (=/vault/60_Council). Overridable for host
# test runs via the same VAULT_PATH convention the rest of the system uses.
import os

_VAULT = Path(os.environ.get("VAULT_PATH", "/vault"))
_PROPERTIES = _VAULT / "60_Council" / "properties"

_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _safe_slug(raw: str) -> str:
    """Sanitize a property slug to a filename-safe token — same guard the
    context loader uses, so a hostile value can never escape the properties dir."""
    return re.sub(r"[^a-z0-9_-]", "", str(raw or "").strip().lower())


def profile_path(slug: str) -> Path | None:
    s = _safe_slug(slug)
    return (_PROPERTIES / s / "BUILD_PROFILE.md") if s else None


def parse(slug: str) -> dict | None:
    """Return the property's brand tokens dict, or None if unavailable.

    Shape: {slug, logo, fonts[list], palette[list of #RRGGBB], required_colors[list]}.
    Palette/required_colors are normalized to upper-case 6-digit hex.
    """
    path = profile_path(slug)
    if not path or not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _JSON_BLOCK.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    data["fonts"] = [str(f) for f in data.get("fonts", []) if str(f).strip()]
    data["palette"] = [normalize_hex(h) for h in data.get("palette", []) if normalize_hex(h)]
    data["required_colors"] = [
        normalize_hex(h) for h in data.get("required_colors", []) if normalize_hex(h)
    ]
    data.setdefault("slug", _safe_slug(slug))
    return data


def normalize_hex(value: str) -> str | None:
    """#abc / #AABBCC -> #AABBCC (upper, expanded). None if not a hex color."""
    if not value:
        return None
    s = str(value).strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", s):
        s = "".join(c * 2 for c in s)
    if re.fullmatch(r"[0-9a-fA-F]{6}", s):
        return "#" + s.upper()
    return None

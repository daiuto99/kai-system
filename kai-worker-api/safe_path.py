"""Shared path safety helper — single implementation for the whole service (L4/S5R-13).

S5R-13 bounds enforced here:
  - max 6 path segments
  - each segment: [A-Za-z0-9._-]+
  - 1 MB content cap enforced at write sites (MAX_CONTENT_BYTES)
"""
import re
from pathlib import Path

_SEGMENT_RE = re.compile(r'^[A-Za-z0-9._-]+$')
_MAX_SEGMENTS = 6
MAX_CONTENT_BYTES = 1_048_576  # 1 MB


def safe_path(base: Path, rel: str) -> Path | None:
    """Resolve rel against base; return None if it would escape base or violates S5R-13 bounds."""
    stripped = rel.lstrip("/")
    if not stripped:
        return base.resolve()
    parts = Path(stripped).parts
    if len(parts) > _MAX_SEGMENTS:
        return None
    for part in parts:
        if not _SEGMENT_RE.match(part):
            return None
    try:
        p = (base / stripped).resolve()
        p.relative_to(base.resolve())
        return p
    except (ValueError, TypeError):
        return None

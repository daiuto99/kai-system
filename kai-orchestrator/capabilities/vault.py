"""vault.read, vault.write, vault.list capabilities — direct vault filesystem access."""
from pathlib import Path

from models import CapabilityResult
from . import capability

_VAULT = Path("/vault")


def _safe_path(base: Path, rel: str) -> Path | None:
    """Resolve rel against base; return None if it would escape base."""
    try:
        p = (base / rel.lstrip("/")).resolve()
        p.relative_to(base.resolve())
        return p
    except ValueError:
        return None


@capability("vault.read")
def read(path: str, **_) -> CapabilityResult:
    """Read a file from the vault. path is relative to /vault."""
    p = _safe_path(_VAULT, path)
    if p is None:
        return CapabilityResult(ok=False, status="failed_fatal",
                                error={"type": "path_escape", "path": path})
    if not p.exists():
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "not_found", "path": str(p)})
    if not p.is_file():
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "not_a_file", "path": str(p)})
    try:
        content = p.read_text(encoding="utf-8")
        return CapabilityResult(ok=True, status="succeeded",
                                data={"path": str(p), "content": content, "bytes": len(content)},
                                verification={"verified": True, "method": "read_ok"})
    except Exception as e:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "read_error", "detail": str(e)})


@capability("vault.write")
def write(path: str, content: str, **_) -> CapabilityResult:
    """Write content to a vault file. Creates parent dirs. path relative to /vault."""
    p = _safe_path(_VAULT, path)
    if p is None:
        return CapabilityResult(ok=False, status="failed_fatal",
                                error={"type": "path_escape", "path": path})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return CapabilityResult(ok=True, status="succeeded",
                                data={"path": str(p), "bytes": len(content)},
                                verification={"verified": True, "method": "write_ok"})
    except Exception as e:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "write_error", "detail": str(e)})


@capability("vault.list")
def list_dir(path: str = "", **_) -> CapabilityResult:
    """List files/dirs in a vault directory. path relative to /vault."""
    p = _safe_path(_VAULT, path) if path else _VAULT
    if p is None:
        return CapabilityResult(ok=False, status="failed_fatal",
                                error={"type": "path_escape", "path": path})
    if not p.exists():
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "not_found", "path": str(p)})
    if not p.is_dir():
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "not_a_dir", "path": str(p)})
    entries = [
        {"name": e.name, "type": "dir" if e.is_dir() else "file",
         "size": e.stat().st_size if e.is_file() else None}
        for e in sorted(p.iterdir())
    ]
    return CapabilityResult(ok=True, status="succeeded",
                            data={"path": str(p), "entries": entries, "count": len(entries)},
                            verification={"verified": True, "method": "list_ok"})

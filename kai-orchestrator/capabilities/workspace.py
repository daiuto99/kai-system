"""workspace.read, workspace.list, workspace.sync capabilities."""
from pathlib import Path
import httpx

from models import CapabilityResult
from . import capability

_WORKSPACE = Path("/workspace")
_SYNCTHING_BASE = "http://172.18.0.1:8384"
_SYNCTHING_KEY_PATH = Path("/run/wp_secrets/syncthing_api_key.txt")
_SONICINK_FOLDER_ID = "sonicink"


def _syncthing_headers() -> dict[str, str] | None:
    try:
        key = _SYNCTHING_KEY_PATH.read_text().strip()
        return {"X-API-Key": key}
    except Exception:
        return None


def _safe_path(base: Path, rel: str) -> Path | None:
    try:
        p = (base / rel.lstrip("/")).resolve()
        p.relative_to(base.resolve())
        return p
    except ValueError:
        return None


@capability("workspace.read")
def read(path: str, **_) -> CapabilityResult:
    """Read a file from the workspace (sonicink). path is relative to workspace root."""
    p = _safe_path(_WORKSPACE, path)
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


@capability("workspace.list")
def list_dir(path: str = "", **_) -> CapabilityResult:
    """List files/dirs in workspace directory. path relative to workspace root."""
    p = _safe_path(_WORKSPACE, path) if path else _WORKSPACE
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
        if not e.name.startswith(".")
    ]
    return CapabilityResult(ok=True, status="succeeded",
                            data={"path": str(p), "entries": entries, "count": len(entries)},
                            verification={"verified": True, "method": "list_ok"})


@capability("workspace.sync")
def sync(direction: str = "status", **_) -> CapabilityResult:
    """Trigger or check Syncthing sync for sonicink folder.
    direction: 'status' (check state), 'rescan' (force rescan).
    """
    headers = _syncthing_headers()
    if headers is None:
        return CapabilityResult(ok=False, status="failed_fatal",
                                error={"type": "no_api_key", "path": str(_SYNCTHING_KEY_PATH)})

    try:
        if direction == "rescan":
            r = httpx.post(
                f"{_SYNCTHING_BASE}/rest/db/scan",
                params={"folder": _SONICINK_FOLDER_ID},
                headers=headers,
                timeout=10.0,
            )
            if r.status_code == 200:
                return CapabilityResult(
                    ok=True, status="accepted",
                    data={
                        "action": "rescan",
                        "folder": _SONICINK_FOLDER_ID,
                        "completed": False,
                        "note": "Rescan triggered — check completion with direction='status'",
                    },
                    verification={"verified": False, "method": "rescan_accepted_async"},
                )
            return CapabilityResult(ok=False, status="failed_recoverable",
                                    error={"type": "syncthing_error", "status": r.status_code, "body": r.text[:200]})

        if direction != "status":
            return CapabilityResult(ok=False, status="failed_fatal",
                                    error={"type": "invalid_direction",
                                           "detail": f"Unknown direction '{direction}'. Use 'status' or 'rescan'.",
                                           "allowed": ["status", "rescan"]})

        # default: status
        r = httpx.get(
            f"{_SYNCTHING_BASE}/rest/db/status",
            params={"folder": _SONICINK_FOLDER_ID},
            headers=headers,
            timeout=10.0,
        )
        if r.status_code != 200:
            return CapabilityResult(ok=False, status="failed_recoverable",
                                    error={"type": "syncthing_error", "status": r.status_code})
        data = r.json()
        return CapabilityResult(ok=True, status="succeeded",
                                data={
                                    "folder": _SONICINK_FOLDER_ID,
                                    "state": data.get("state"),
                                    "needFiles": data.get("needFiles", 0),
                                    "needBytes": data.get("needBytes", 0),
                                    "inSyncFiles": data.get("inSyncFiles", 0),
                                    "globalFiles": data.get("globalFiles", 0),
                                },
                                verification={"verified": True, "method": "status_ok"})
    except httpx.RequestError as e:
        return CapabilityResult(ok=False, status="failed_recoverable",
                                error={"type": "connection_error", "detail": str(e)})

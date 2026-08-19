import json
from pathlib import Path
from typing import Callable

_registry: dict[str, Callable] = {}

_MAP_PATH = Path(__file__).parent / "capability_map.json"
_cap_map: dict = {}


def _load_map() -> dict:
    global _cap_map
    if not _cap_map:
        _cap_map = json.loads(_MAP_PATH.read_text())
    return _cap_map


def capability(name: str):
    """Decorator — registers a function as a named capability."""
    def decorator(fn: Callable) -> Callable:
        _registry[name] = fn
        return fn
    return decorator


def get_capability(name: str) -> Callable:
    if name not in _registry:
        raise KeyError(f"Capability '{name}' not registered")
    return _registry[name]


def get_transports(site: str, operation: str) -> list[str]:
    """Return ordered transport list for (site, operation) from capability_map.json."""
    m = _load_map()
    # Site-specific override
    site_entry = m.get("sites", {}).get(site, {})
    if site_entry.get(operation):
        return site_entry[operation]
    # Site inherits from a profile
    profile = site_entry.get("inherits", "default_cloudways")
    profile_entry = m.get(profile, {})
    if operation in profile_entry:
        return profile_entry[operation]
    return m.get("default_cloudways", {}).get(operation, [])


# Auto-register all capability modules on package import
from . import wordpress as _wp    # noqa: F401, E402
from . import hostops as _hostops  # noqa: F401, E402
from . import council as _council  # noqa: F401, E402

from . import notify as _notify    # noqa: F401, E402
from . import plane as _plane    # noqa: F401, E402
from . import vault as _vault        # noqa: F401, E402
from . import workspace as _workspace  # noqa: F401, E402
from . import calendar as _calendar    # noqa: F401, E402
from . import session as _session    # noqa: F401, E402
from . import model_peer as _model_peer  # noqa: F401, E402
from . import self_modify as _self_modify  # noqa: F401, E402
from . import registry as _registry_cap  # noqa: F401, E402
from . import wp_security as _wp_security  # noqa: F401, E402

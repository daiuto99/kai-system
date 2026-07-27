import os as _os, sys as _sys
# --- mirror runtime PYTHONPATH=/shared for host test runs (WP-20.4 guard tests) ---
_SHARED = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "shared"))
if _os.path.isdir(_SHARED) and _SHARED not in _sys.path:
    _sys.path.insert(0, _SHARED)

collect_ignore = [
    "test_jarvis_system.py",
    "test_5r_ec4_kill_restart.py",
    "test_s2_tasks.py",
    "test_safe_request.py",
    "test_s1_tasks.py",
]

"""KAI-882 L3 gate: inspect the deployed worker route tree (/app/routes)."""
from pathlib import Path
import subprocess


APP = Path("/app")
ROUTES = APP / "routes"

# KAI-882 follow-up 19569ce0 owns removal of this explicit deployed baseline.
# This list is deliberately exact: new, moved, or stale entries fail CI.
LEGACY_EXEMPTIONS = {
    "routes/assets.py:65", "routes/assets.py:127", "routes/assets.py:147",
    "routes/calendar.py:164", "routes/focus.py:57", "routes/focus.py:110",
    "routes/focus.py:137", "routes/focus.py:187", "routes/inbox.py:118",
    "routes/intake.py:105", "routes/intake.py:177", "routes/mode_lock.py:565",
    "routes/orchestrator.py:66", "routes/oura.py:31", "routes/oura.py:43",
    "routes/sprint_a.py:69", "routes/sprint_a.py:126", "routes/t2.py:46",
    "routes/t2.py:132", "routes/telegram.py:138", "routes/wordpress.py:61",
}


def _grep_files(pattern: str, path: Path) -> list[str]:
    """A-6 equivalent for the worker image, which has no orchestrator tests."""
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "-l", pattern, str(path)],
        capture_output=True, text=True,
    )
    return [entry for entry in result.stdout.splitlines() if "__pycache__" not in entry]


def _grep_count(pattern: str, path: Path) -> int:
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "-c", pattern, str(path)],
        capture_output=True, text=True,
    )
    return sum(
        int(line.rsplit(":", 1)[1])
        for line in result.stdout.splitlines()
        if ":" in line
    )


def _bare_json_sites() -> dict[str, str]:
    sites: dict[str, str] = {}
    for filename in _grep_files(".json()", ROUTES):
        path = Path(filename)
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            if ".json()" in line and not line.lstrip().startswith("#"):
                sites[f"{path.relative_to(APP)}:{line_no}"] = line.strip()
    return sites


def test_no_new_or_moved_bare_json_in_routes():
    """L3: baseline is temporary, but any deployed delta blocks ci.sh."""
    sites = _bare_json_sites()
    assert _grep_count(".json()", ROUTES) >= len(sites)
    unexpected = sorted(set(sites) - LEGACY_EXEMPTIONS)
    stale = sorted(LEGACY_EXEMPTIONS - set(sites))
    assert not unexpected and not stale, (
        "KAI-882 L3 guard failures; follow-up 19569ce0 removes baseline. "
        f"new/moved={[(site, sites[site]) for site in unexpected]}; stale={stale}"
    )

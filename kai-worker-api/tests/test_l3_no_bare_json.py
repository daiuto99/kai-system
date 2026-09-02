"""KAI-882 L3 gate: the deployed worker route tree (/app/routes) carries ZERO
bare .json() calls.

Every response/body parse in routes/ goes through the shared safe_http helpers
(safe_json / json_or_error / safe_body), which are defined OUTSIDE routes/ so a
decode error can never surface as an uncaught JSONDecodeError. KAI-893 removed
the last 14 pre-existing exemptions and this guard now enforces the whole risk
class with no carve-outs: any new or moved bare .json() in routes/ fails ci.sh.
"""
from pathlib import Path
import subprocess


APP = Path("/app")
ROUTES = APP / "routes"


def _grep_files(pattern: str, path: Path) -> list[str]:
    result = subprocess.run(
        ["grep", "-r", "--include=*.py", "-l", pattern, str(path)],
        capture_output=True, text=True,
    )
    return [entry for entry in result.stdout.splitlines() if "__pycache__" not in entry]


def _bare_json_sites() -> dict[str, str]:
    sites: dict[str, str] = {}
    for filename in _grep_files(".json()", ROUTES):
        path = Path(filename)
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            if ".json()" in line and not line.lstrip().startswith("#"):
                sites[f"{path.relative_to(APP)}:{line_no}"] = line.strip()
    return sites


def test_no_bare_json_in_routes():
    """L3: any bare .json() in routes/ blocks ci.sh — no exemptions."""
    sites = _bare_json_sites()
    assert not sites, (
        "L3 bare .json() in routes/ — parse via safe_http.safe_json / "
        f"json_or_error / safe_body instead: {[(s, sites[s]) for s in sorted(sites)]}"
    )

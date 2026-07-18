"""KAI-882 L3 gate: inspect the deployed orchestrator transport tree."""
from pathlib import Path
import subprocess

APP = Path("/app")
TRANSPORTS = APP / "transports"


# Kept equivalent to the A-6 helpers in test_jarvis_system.py. Importing that
# standalone system-test script exits when optional requests is absent in CI.
def _grep_files(pattern: str, path: Path) -> list[str]:
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
    return sum(int(line.rsplit(":", 1)[1]) for line in result.stdout.splitlines() if ":" in line)


def test_no_bare_json_in_transports_outside_shared_base():
    files = [Path(filename) for filename in _grep_files(".json()", TRANSPORTS)]
    offenders = [path for path in files if path.name != "base.py"]
    assert _grep_count(".json()", TRANSPORTS) >= len(files)
    assert not offenders, f"L3 bare .json() outside transports/base.py: {offenders}"

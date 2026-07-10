"""EC#1 pytest wrapper. Runs test_5r_ec1_council_dispatch.py as a subprocess
so module-level execution does not fire at import time."""
import subprocess
import sys


def test_ec1_roundtrip():
    """EC#1: run_capability round-trip THROUGH council dispatch."""
    result = subprocess.run(
        [sys.executable, "/app/tests/test_5r_ec1_council_dispatch.py"],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    assert result.returncode == 0

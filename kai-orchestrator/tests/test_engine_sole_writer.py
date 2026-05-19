"""Enforce that only engine.py writes job/step status to SQLite."""
import subprocess

def test_no_direct_status_writes_outside_engine():
    """No file other than engine.py may write status columns directly via SQL."""
    result = subprocess.run(
        ["grep", "-rn",
         r"UPDATE jobs SET status\|UPDATE steps SET status",
         ".", "--include=*.py", "--exclude=engine.py", "--exclude=test_*.py"],
        capture_output=True, text=True,
        cwd="/app"
    )
    hits = [l for l in result.stdout.splitlines() if l.strip()]
    assert not hits, (
        "Direct SQL status writes found outside engine.py:\n" + "\n".join(hits)
    )

def test_no_direct_sql_status_writes_in_workflows():
    """Workflow files must not contain raw SQL status mutations."""
    result = subprocess.run(
        ["grep", "-rn",
         r"conn\.execute.*UPDATE.*status",
         "workflows/", "--include=*.py"],
        capture_output=True, text=True,
        cwd="/app"
    )
    hits = [l for l in result.stdout.splitlines() if l.strip()]
    assert not hits, (
        "Raw SQL status writes in workflow files:\n" + "\n".join(hits)
    )

from pathlib import Path

def pytest_configure(config):
    # Write /tmp/kai_auth.txt from the secrets mount so EC#5 get_auth() resolves.
    auth_file = Path("/tmp/kai_auth.txt")
    if not auth_file.exists():
        cand = Path("/home/leo/kai-system/secrets/kai_worker_auth.txt")
        if cand.exists():
            auth_file.write_text(cand.read_text())

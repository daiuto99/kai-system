import subprocess
import shlex
from .base import SafeResponse

_SSH_KEY = "/run/secrets/cloudways_ssh_key"
_HOST = "master_vvbwxpwpcc@134.209.166.23"
_SSH_OPTS = [
    "-i", _SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
]


def purge(site: str, url_path: str, creds: dict) -> SafeResponse:
    custom_domain = creds.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
    fqdn = creds.get("fqdn", "")
    results = {}
    for host in filter(None, [custom_domain, fqdn]):
        safe_path = shlex.quote(url_path or "/")
        safe_host = shlex.quote(host)
        cmd_str = f"curl -s -o /dev/null -w '%{{http_code}}' -X PURGE -H 'Host: {host}' http://localhost:8080{url_path or '/'}"
        r = subprocess.run(
            ["ssh"] + _SSH_OPTS + [_HOST, cmd_str],
            capture_output=True, text=True, timeout=20,
        )
        results[host] = r.stdout.strip() if r.returncode == 0 else f"ssh_err: {r.stderr[:80]}"

    all_ok = all(v == "200" for v in results.values())
    return SafeResponse(
        ok=all_ok,
        status_code=200 if all_ok else 0,
        data={"purge_results": results},
    )

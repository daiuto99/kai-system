import subprocess
import shlex
from .base import SafeResponse

# Allowlist — transport refuses to operate on anything not in this set
_ALLOWED_OPTIONS = {"kai_cs_active"}

_SSH_KEY = "/run/secrets/cloudways_ssh_key"
_SSH_OPTS = [
    "-i", _SSH_KEY,
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
]


def set_option(site: str, option: str, value: str, creds: dict) -> SafeResponse:
    if option not in _ALLOWED_OPTIONS:
        return SafeResponse(ok=False, error=f"ssh_php_eval: option '{option}' not in allowlist")

    # All arguments escaped — no model-provided strings reach this call
    safe_opt = shlex.quote(option)
    safe_val = shlex.quote(value)
    wp_path = f"/home/1623875.cloudwaysapps.com/{creds['cloudways_sys_user']}/public_html"
    php = (
        f"define('ABSPATH','{wp_path}/');"
        f"require '{wp_path}/wp-load.php';"
        f"update_option({safe_opt},{safe_val});"
        f"echo get_option({safe_opt});"
    )

    host = f"master_vvbwxpwpcc@134.209.166.23"  # noqa: F541
    cmd = ["ssh"] + _SSH_OPTS + [host, f"php -r {shlex.quote(php)}"]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode == 0 and r.stdout.strip() == value:
        return SafeResponse(ok=True, status_code=200, data={"value": r.stdout.strip()})
    return SafeResponse(ok=False, error=r.stderr or f"ssh_php_eval failed (rc={r.returncode})")


# There is no run(raw_php) method. Do not add one.

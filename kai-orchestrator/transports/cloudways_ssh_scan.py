"""Read-only Cloudways scan transport (KAI-1068).

Runs a FIXED, constant batch of read-only wp-cli / read-only SQL against one
Cloudways app over SSH and returns the raw sectioned stdout. It is deliberately
incapable of writing to any WP site:

  * The remote script is a module constant — no model-provided string ever
    reaches the shell. The ONLY substitution is the site's system user, which is
    validated against ``^[a-z0-9]+$`` before use (Cloudways sys users are lower
    alphanumeric, e.g. ``nxwrdbkypd``).
  * Every wp command runs with ``--skip-plugins --skip-themes`` (fast, no plugin
    side effects) and every SQL statement is a ``SELECT``.

Mirrors the SSH-opts / key-path pattern of ``ssh_php_eval`` and
``cloudways_ssh_purge`` so the whole fleet uses one transport posture.
"""
import re
import subprocess

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

_SYSUSER_RE = re.compile(r"^[a-z0-9]+$")

# Section markers the capability parses on. Keep in sync with capabilities/wp_security.py.
# The whole script is a constant; __SYSUSER__ is the sole (validated) substitution.
# Markers are anchored with a LEADING newline (printf '\n===X===\n') because some
# wp commands (notably --format=count) emit no trailing newline, which would glue
# their value onto the following marker and defeat the section parser.
_REMOTE_SCAN_SCRIPT = r"""
cd "$HOME/applications/__SYSUSER__/public_html" 2>/dev/null || { echo "__SCANERR__:cd_failed"; exit 3; }
WP="wp --skip-plugins --skip-themes"
PFX=$($WP config get table_prefix 2>/dev/null)
[ -z "$PFX" ] && PFX=wp_
printf '\n===SITEURL===\n'; $WP option get siteurl 2>&1
printf '\n===HOME===\n'; $WP option get home 2>&1
printf '\n===ADMINS===\n'; $WP user list --role=administrator --field=user_login --format=csv 2>&1
printf '\n===USERCOUNT===\n'; $WP user list --format=count 2>&1
printf '\n===COMMENTS===\n'; $WP comment list --format=count 2>&1
printf '\n===PLUGINS===\n'; $WP plugin list --fields=name,status,version --format=json 2>&1
printf '\n===THEMES===\n'; $WP theme list --fields=name,status,version --format=json 2>&1
printf '\n===AUTOLOAD===\n'; $WP db query "SELECT option_name FROM ${PFX}options WHERE autoload='yes' AND (option_value LIKE '%<script%' OR option_value LIKE '%base64_decode%' OR option_value LIKE '%eval(%' OR option_value LIKE '%gzinflate%')" --skip-column-names 2>&1
printf '\n===SCRIPTPOSTS===\n'; $WP db query "SELECT ID FROM ${PFX}posts WHERE post_status='publish' AND (post_content LIKE '%<script%' OR post_content LIKE '%<iframe%')" --skip-column-names 2>&1
printf '\n===CHECKSUMS===\n'; $WP core verify-checksums 2>&1
printf '\n===DONE===\n'
"""


def scan(sysuser: str) -> SafeResponse:
    """SSH to the app for ``sysuser`` and return raw sectioned scan output.

    Fail-closed: a cd/ssh failure is surfaced as ``ok=False`` (an unscannable
    site is an anomaly for the caller to report, never a silent skip)."""
    if not sysuser or not _SYSUSER_RE.match(sysuser):
        return SafeResponse(ok=False, error=f"invalid cloudways sys user: {sysuser!r}")

    script = _REMOTE_SCAN_SCRIPT.replace("__SYSUSER__", sysuser)
    cmd = ["ssh"] + _SSH_OPTS + [_HOST, script]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return SafeResponse(ok=False, error="scan timed out (180s)")

    if "__SCANERR__:cd_failed" in r.stdout:
        return SafeResponse(ok=False, error="cd_failed: app dir missing", data={"raw": r.stdout})
    if "===DONE===" not in r.stdout:
        return SafeResponse(
            ok=False,
            error=f"scan incomplete (rc={r.returncode}): {(r.stderr or r.stdout)[:160]}",
            data={"raw": r.stdout},
        )
    return SafeResponse(ok=True, status_code=200, data={"raw": r.stdout})

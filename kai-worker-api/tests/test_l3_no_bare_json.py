"""KAI-882 L3 gate: inspect the deployed worker route tree (/app/routes)."""
from pathlib import Path
import subprocess


APP = Path("/app")
ROUTES = APP / "routes"

# KAI-882 follow-up 19569ce0 owns removal of this explicit deployed baseline.
# This list is deliberately exact: new, moved, or stale entries fail CI.
# Re-synced 2026-07-29 (AR-5.3): Slack retirement removed the assets.py DM/upload
# .json() sites and the slack.py stub's body-parse; other exempted bare-json calls
# shifted line numbers as dead Slack code was removed. No NEW bare-json introduced.
# Re-synced 2026-07-31 (KAI-1004): the notify() gateway repointed telegram._tg_send
# through the shared transport, removing ~6 lines above the webhook body-parse — the
# telegram.py bare-json shifted 208 -> 202. Re-synced 2026-08-03 (COMMS P2): the
# channel-agnostic approval change added the `surface` field + present_action above the
# dormant slack_action_internal body-parse, shifting mode_lock.py's bare-json 727 -> 738
# (same dead Slack call, moved only). No NEW bare-json introduced.
# Re-synced 2026-08-05 (KAI-1002 item 2): the mode-lock->Buzz change added GET /mode_lock/pending
# + _post_telegram_alert_held above the dormant slack_action_internal body-parse, shifting
# mode_lock.py's bare-json 738 -> 778 (same dead Slack call, moved only). No NEW bare-json introduced.
# Re-synced 2026-08-27 (KAI-1243): the worker-api Slack purge removed inbox.py's dead
# _slack_token/SLACK_TOKEN_FILE above the council body-parse, shifting inbox.py's
# bare-json 112 -> 107 (same call, moved only). No NEW bare-json introduced.
# Re-synced 2026-08-27 (KAI-1243, cont'd): removing sprint_a.py's dead /slack/interactions
# ingress + helpers shifted its request.json() 112 -> 33; dropping intake.py's dead
# _slack_token + splitting two E701 one-liners shifted its two httpx .json() calls
# 94 -> 89 and 166 -> 161 (same calls, moved only). No NEW bare-json introduced.
# Re-synced 2026-08-27 (KAI-1243, session 42): removing mode_lock.py's dormant Slack
# unlock path (slack_callback + slack_action_internal) DELETED the bare-json body-parse
# at routes/mode_lock.py:778 — dropped from the baseline (a removed call, not a moved one).
LEGACY_EXEMPTIONS = {
    "routes/calendar.py:164", "routes/focus.py:57", "routes/focus.py:110",
    "routes/focus.py:137", "routes/focus.py:187", "routes/inbox.py:107",
    "routes/intake.py:89", "routes/intake.py:161",
    "routes/orchestrator.py:66", "routes/oura.py:31", "routes/oura.py:43",
    "routes/sprint_a.py:33", "routes/telegram.py:202", "routes/wordpress.py:61",
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

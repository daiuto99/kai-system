import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT_PATH = Path("/vault")
COUNCIL_PATH = VAULT_PATH / "60_Council"
WORKER_URL = "http://kai-worker-api:8001"
ORCHESTRATOR_URL = "http://kai-orchestrator:8003"

ADVISOR_CHANNELS = {
    "kai":   "kai",
    "beats": "beats",
    "beats-personal": "beats",
    "ember": "ember",
    "doc": "doc",
    "coach": "coach",
    "council": "kai",
    "council-daily": "kai",
    "council-weekly": "kai",
    "council-monthly": "kai",
    "sky": "sky",
    "roads": "roads",
    "creative": "creative",
    "dev": "dev",
    "devops": "devops",
}

ADVISOR_IDENTITIES = {
    "kai":      {"username": "KAI",      "icon_url": "https://kai.sonicink.space/avatar-kai.png"},
    "ember":    {"username": "Ember",    "icon_url": "https://kai.sonicink.space/avatar-ember.png"},
    "beats":    {"username": "Beats",    "icon_url": "https://kai.sonicink.space/avatar-beats.png"},
    "doc":      {"username": "Doc",      "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "coach":    {"username": "Coach",    "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "creative": {"username": "Creative", "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "tech":     {"username": "Tech",     "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "dev":      {"username": "Dev",      "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "learning": {"username": "Learning", "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "support":  {"username": "Support",  "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "sky":      {"username": "Sky",      "icon_url": "https://kai.sonicink.space/avatar-sky.png"},
    "roads":    {"username": "Roads",    "icon_url": "https://kai.sonicink.space/avatar-roads.png"},
    "ops":      {"username": "Ops",       "icon_url": "https://kai.sonicink.space/icon-192.png"},
    "devops":   {"username": "DevOps",   "icon_url": "https://kai.sonicink.space/icon-192.png"},
}

# Backward-compat — execute_tool.py imports this
ADVISOR_AVATARS = {k: v["icon_url"] for k, v in ADVISOR_IDENTITIES.items()}


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


from usage_tracker import _track_usage, track_api_call  # noqa: F401  re-export


# ── Rate limiting ─────────────────────────────────────────────────────────────
DAILY_COST_CAP_USD   = 5.00   # hard daily spend cap across all advisors
HOURLY_CALL_CAP      = 50     # max calls per hour (loop/runaway protection)
_rate_alert_sent: dict = {}   # track if alert already sent this period

def _check_rate_limit(advisor: str) -> dict:
    """Returns {"blocked": True, "reason": "..."} or {"blocked": False}."""
    import datetime
    try:
        usage_path = Path("/vault/00_System/token_usage.json")
        if not usage_path.exists():
            return {"blocked": False}
        data = json.loads(usage_path.read_text())
        today = datetime.date.today().isoformat()
        hour_key = datetime.datetime.now().strftime("%H")
        day = next((d for d in data.get("days", []) if d["date"] == today), None)
        if day is None:
            return {"blocked": False}

        # Daily cost cap
        if day.get("cost_usd", 0) >= DAILY_COST_CAP_USD:
            _maybe_slack_alert("daily_cap", f":warning: *KAI rate limit hit* — daily spend cap of ${DAILY_COST_CAP_USD:.2f} reached. Calls blocked until midnight.")
            return {"blocked": True, "reason": f"Daily API budget of ${DAILY_COST_CAP_USD:.2f} reached. Resets at midnight."}

        # Hourly call cap
        hour = day.get("hours", {}).get(hour_key, {})
        if hour.get("calls", 0) >= HOURLY_CALL_CAP:
            _maybe_slack_alert("hourly_cap", f":warning: *KAI rate limit hit* — {HOURLY_CALL_CAP} calls in the last hour. Cooling down.")
            return {"blocked": True, "reason": f"Hourly call limit of {HOURLY_CALL_CAP} reached. Try again next hour."}

        return {"blocked": False}
    except Exception as e:
        logger.exception("rate-limit check error: %s", e)
        return {"blocked": False}


def _maybe_slack_alert(key: str, message: str):
    """Post to #kai-system once per rate-limit trigger (not every blocked call)."""
    import datetime
    period = datetime.datetime.now().strftime("%Y-%m-%d-%H" if "hourly" in key else "%Y-%m-%d")
    alert_key = f"{key}:{period}"
    if _rate_alert_sent.get(alert_key):
        return
    _rate_alert_sent[alert_key] = True
    try:
        from pathlib import Path as _Path
        token_file = _Path("/run/secrets/slack_bot_token")
        token = token_file.read_text().strip() if token_file.exists() else ""
        if not token:
            return
        import httpx as _httpx
        _httpx.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": "#kai-system", "text": message, "username": "KAI", "icon_url": "https://kai.sonicink.space/avatar-kai.png"},
            timeout=5,
        )
    except Exception:
        pass

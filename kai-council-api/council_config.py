import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT_PATH = Path("/vault")
COUNCIL_PATH = VAULT_PATH / "60_Council"
WORKER_URL = "http://kai-worker-api:8001"
ORCHESTRATOR_URL = "http://kai-orchestrator:8003"


def _worker_auth() -> tuple[str, str] | None:
    """Basic-auth credential for internal calls to kai-worker-api.

    Bug 48f85706 / aec2d486: the worker's BasicAuthMiddleware authenticates
    every route (bar /health + webhooks). Internal callers attach the worker
    credential they already hold as a Docker secret rather than relying on a
    network-origin bypass or a growing exempt list. Reads /run/secrets first
    (uniform docker-secret path), then the bind-mounted secrets dir fallbacks.
    """
    for p in (
        "/run/secrets/kai_worker_auth",
        "/run/wp_secrets/kai_worker_auth.txt",
        "/home/leo/kai-system/secrets/kai_worker_auth.txt",
    ):
        try:
            raw = Path(p).read_text().strip()
        except Exception:
            continue
        if ":" in raw:
            user, pw = raw.split(":", 1)
            return (user, pw)
    logger.warning("worker_auth: no kai_worker_auth credential found — internal worker calls will 401")
    return None

# Channel-name → advisor routing. Slim post-Slack-Sprint: only channels/DMs
# that still exist in the workspace. Scheduler check-ins still address the
# `council-*` aliases (resolved to KAI), so those stay.
ADVISOR_CHANNELS = {
    "kai":             "kai",
    "sky":             "sky",
    "roads":           "roads",
    "devops":          "devops",
    "m1smoke":         "m1smoke",
    "council":         "kai",
    "council-daily":   "kai",
    "council-weekly":  "kai",
    "council-monthly": "kai",
}

# Slack-posting identities — only advisors that post as themselves in Slack.
# All other advisor output is relayed by KAI with a "Beats says:" prefix.
ADVISOR_IDENTITIES = {
    "kai":    {"username": "KAI",    "icon_url": "https://kai.sonicink.space/avatar-kai.png"},
    "sky":    {"username": "Sky",    "icon_url": "https://kai.sonicink.space/avatar-sky.png"},
    "roads":  {"username": "Roads",  "icon_url": "https://kai.sonicink.space/avatar-roads.png"},
    "devops": {"username": "DevOps", "icon_url": "https://kai.sonicink.space/avatar-devops.png"},
}

# Capitalized labels for the "Beats says:" relay prefix when KAI surfaces a non-Slack advisor
ADVISOR_LABELS = {
    "beats": "Beats", "ember": "Ember", "doc": "Doc", "coach": "Coach",
    "creative": "Creative", "tech": "Tech", "dev": "Dev", "ops": "Ops",
    "learning": "Learning", "support": "Support",
}

# Backward-compat — execute_tool.py imports this
ADVISOR_AVATARS = {k: v["icon_url"] for k, v in ADVISOR_IDENTITIES.items()}


def _slack_token() -> str:
    p = Path("/run/secrets/slack_bot_token")
    return p.read_text().strip() if p.exists() else os.environ.get("SLACK_BOT_TOKEN", "")


from usage_tracker import _track_usage, track_api_call  # noqa: F401  re-export


# ── Rate limiting (S5R-19: tiered budget) ────────────────────────────────────
# Interactive traffic (Leo's chat) degrades to Haiku before the sub-budget is
# exhausted — never hard-blocked. Alert/ops traffic has a separate sub-budget
# and is also never hard-blocked. The total cap feeds the Health Board cost view.
# Only the hourly call cap remains a hard block (loop/runaway protection).
DAILY_COST_CAP_USD     = 5.00  # total daily cap — Health Board cost view
INTERACTIVE_BUDGET_USD = 4.00  # interactive-chat sub-budget; Haiku fallback above this
ALERT_BUDGET_USD       = 1.00  # alerts + critical-ops sub-budget
WARN_THRESHOLD         = 0.80  # warn Leo at this fraction of INTERACTIVE_BUDGET_USD
HOURLY_CALL_CAP        = 50    # calls/hour ceiling — loop/runaway protection (hard block)
_rate_alert_sent: dict = {}

def _check_rate_limit(advisor: str, traffic_type: str = "interactive") -> dict:
    """Return rate-limit decision dict.

    Keys:
      blocked (bool) — only True for hourly runaway; interactive never hard-blocks
      degrade (bool) — interactive spend ≥ INTERACTIVE_BUDGET_USD; caller uses Haiku
      warn    (bool) — interactive spend ≥ 80% of sub-budget; Slack alert already sent
      reason  (str)  — human-readable explanation for blocked/warn states
    """
    import datetime
    try:
        usage_path = Path("/vault/00_System/token_usage.json")
        if not usage_path.exists():
            return {"blocked": False, "degrade": False, "warn": False, "reason": ""}
        data = json.loads(usage_path.read_text())
        today = datetime.date.today().isoformat()
        hour_key = datetime.datetime.now().strftime("%H")
        day = next((d for d in data.get("days", []) if d["date"] == today), None)
        if day is None:
            return {"blocked": False, "degrade": False, "warn": False, "reason": ""}

        cost = day.get("cost_usd", 0.0)
        warn_level = INTERACTIVE_BUDGET_USD * WARN_THRESHOLD

        if traffic_type == "interactive":
            if cost >= INTERACTIVE_BUDGET_USD:
                _maybe_slack_alert(
                    "interactive_budget_exhausted",
                    f":warning: *KAI budget* — interactive spend ${cost:.2f} exceeds "
                    f"${INTERACTIVE_BUDGET_USD:.2f} sub-budget. Degrading to Haiku until midnight.",
                )
                return {"blocked": False, "degrade": True, "warn": False, "reason": ""}
            if cost >= warn_level:
                _maybe_slack_alert(
                    "interactive_budget_warn",
                    f":information_source: *KAI budget* — interactive spend "
                    f"${cost:.2f} / ${INTERACTIVE_BUDGET_USD:.2f} (80%). "
                    f"Haiku fallback activates above ${INTERACTIVE_BUDGET_USD:.2f}.",
                )
                return {
                    "blocked": False, "degrade": False, "warn": True,
                    "reason": f"${cost:.2f} of ${INTERACTIVE_BUDGET_USD:.2f} daily budget used.",
                }
        else:
            # Alert / critical-ops: log if total cap reached, never block
            if cost >= DAILY_COST_CAP_USD:
                _maybe_slack_alert(
                    "total_cap_alert_traffic",
                    f":warning: *KAI budget* — total spend ${cost:.2f} at cap "
                    f"${DAILY_COST_CAP_USD:.2f}. Alert traffic continuing on reserve.",
                )

        # Hourly call cap — hard block for all traffic types (loop/runaway protection)
        hour = day.get("hours", {}).get(hour_key, {})
        if hour.get("calls", 0) >= HOURLY_CALL_CAP:
            _maybe_slack_alert(
                "hourly_cap",
                f":warning: *KAI rate limit hit* — {HOURLY_CALL_CAP} calls in the last hour. Cooling down.",
            )
            return {
                "blocked": True, "degrade": False, "warn": False,
                "reason": f"Hourly call limit of {HOURLY_CALL_CAP} reached. Try again next hour.",
            }

        return {"blocked": False, "degrade": False, "warn": False, "reason": ""}
    except Exception as e:
        logger.exception("rate-limit check error: %s", e)
        return {"blocked": False, "degrade": False, "warn": False, "reason": ""}


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
            json={"channel": "#devops", "text": message, "username": "DevOps", "icon_url": "https://kai.sonicink.space/avatar-devops.png"},
            timeout=5,
        )
    except Exception:
        pass

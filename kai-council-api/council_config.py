import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT_PATH = Path("/vault")
COUNCIL_PATH = VAULT_PATH / "60_Council"
WORKER_URL = "http://kai-worker-api:8001"

ADVISOR_CHANNELS = {
    "kai":   "kai",
    "chief": "kai",
    "beats": "beats",
    "beats-personal": "beats",
    "ember": "ember",
    "doc": "doc",
    "coach": "coach",
    "biz": "biz",
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
    "biz":      {"username": "Biz",      "icon_url": "https://kai.sonicink.space/icon-192.png"},
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


def _track_usage(advisor: str, input_tokens: int, output_tokens: int, provider: str = "anthropic", model: str = "claude-sonnet-4-6"):
    """Append token usage to vault/00_System/token_usage.json"""
    import datetime
    try:
        usage_path = Path("/vault/00_System/token_usage.json")
        now = datetime.datetime.now()
        today = now.date().isoformat()
        hour_key = now.strftime("%H")
        # Cost per provider/model (per 1M tokens in/out) — duplicate key removed
        COSTS = {
            "claude-sonnet-4-6":       (3, 15),
            "claude-opus-4-6":         (15, 75),
            "gpt-4o":                  (5, 15),
            "gpt-4o-mini":             (0.15, 0.6),
            "llama3.2":                (0, 0),
            "llama3.1:8b":             (0, 0),
            "qwen2.5:3b":              (0, 0),
            "gemma3:4b":               (0, 0),
        }
        in_rate, out_rate = COSTS.get(model, (3, 15))
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
        pkey = f"{provider}/{model}"

        if usage_path.exists():
            data = json.loads(usage_path.read_text())
        else:
            data = {"days": [], "total": {"input": 0, "output": 0, "cost_usd": 0.0, "calls": 0, "by_advisor": {}, "by_model": {}}}

        t = data.setdefault("total", {})
        for k, v in [("input", 0), ("output", 0), ("cost_usd", 0.0), ("calls", 0)]:
            t.setdefault(k, v)
        t.setdefault("by_advisor", {})
        t.setdefault("by_model", {})

        day = next((d for d in data["days"] if d["date"] == today), None)
        if day is None:
            day = {"date": today, "input": 0, "output": 0, "cost_usd": 0.0, "calls": 0,
                   "by_advisor": {}, "by_model": {}, "hours": {}}
            data["days"].append(day)

        day.setdefault("by_model", {})
        day.setdefault("hours", {})

        day["input"] += input_tokens
        day["output"] += output_tokens
        day["cost_usd"] = round(day["cost_usd"] + cost, 6)
        day["calls"] += 1
        day["by_advisor"][advisor] = day["by_advisor"].get(advisor, 0) + 1
        day["by_model"][pkey] = day["by_model"].get(pkey, 0) + 1
        if "by_provider" not in day:
            day["by_provider"] = {}
        day["by_provider"][pkey] = day["by_provider"].get(pkey, 0) + 1

        h = day["hours"].setdefault(hour_key, {"calls": 0, "cost_usd": 0.0, "input": 0, "output": 0, "by_model": {}})
        h["calls"] += 1
        h["cost_usd"] = round(h["cost_usd"] + cost, 6)
        h["input"] += input_tokens
        h["output"] += output_tokens
        h["by_model"][pkey] = h["by_model"].get(pkey, 0) + 1

        t["input"] += input_tokens
        t["output"] += output_tokens
        t["cost_usd"] = round(t["cost_usd"] + cost, 6)
        t["calls"] += 1
        t["by_advisor"][advisor] = t["by_advisor"].get(advisor, 0) + 1
        t["by_model"][pkey] = t["by_model"].get(pkey, 0) + 1

        usage_path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.exception("token-usage error: %s", e)


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

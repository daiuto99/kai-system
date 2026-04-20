import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

VAULT_PATH = Path("/vault")
COUNCIL_PATH = VAULT_PATH / "60_Council"
WORKER_URL = "http://kai-worker-api:8001"

ADVISOR_CHANNELS = {
    "kai":   "chief",
    "chief": "chief",
    "beats": "beats",
    "beats-personal": "beats",
    "ember": "ember",
    "doc": "doc",
    "coach": "coach",
    "biz": "biz",
    "council": "chief",
    "council-daily": "chief",
    "council-weekly": "chief",
    "council-monthly": "chief",
    "sky": "sky",
    "roads": "roads",
}

ADVISOR_AVATARS = {
    "kai":      "https://kai.sonicink.space/avatar-kai.png",
    "ember":    "https://kai.sonicink.space/avatar-ember.png",
    "beats":    "https://kai.sonicink.space/avatar-beats.png",
    "doc":      "https://kai.sonicink.space/icon-192.png",
    "coach":    "https://kai.sonicink.space/icon-192.png",
    "biz":      "https://kai.sonicink.space/icon-192.png",
    "creative": "https://kai.sonicink.space/icon-192.png",
    "tech":     "https://kai.sonicink.space/icon-192.png",
    "dev":      "https://kai.sonicink.space/icon-192.png",
    "sky":      "https://kai.sonicink.space/avatar-sky.png",
    "roads":    "https://kai.sonicink.space/avatar-roads.png",
}


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

"""Shared usage tracker — writes to /vault/00_System/token_usage.json.

This file MUST be kept in sync between kai-worker-api/ and kai-council-api/.
Both containers mount /vault, so they share the JSON store; the Python module
is duplicated so each container's import graph stays self-contained.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

USAGE_PATH = Path("/vault/00_System/token_usage.json")

# Cost per provider/model (USD per 1M tokens in/out).
# Verified against Anthropic / OpenAI public pricing as of 2026-06-08.
COSTS: dict[str, tuple[float, float]] = {
    # Claude 4.7 family
    "claude-opus-4-7":               (15.0, 75.0),
    "claude-opus-4-7-20260115":      (15.0, 75.0),
    # Claude 4.6 family
    "claude-sonnet-4-6":             (3.0, 15.0),
    "claude-opus-4-6":               (15.0, 75.0),
    # Claude 4.5 family
    "claude-sonnet-4-5":             (3.0, 15.0),
    "claude-haiku-4-5":              (0.80, 4.0),
    "claude-haiku-4-5-20251001":     (0.80, 4.0),
    # OpenAI
    "gpt-4o":                        (2.50, 10.0),
    "gpt-4o-mini":                   (0.15, 0.60),
    # Ollama (self-hosted, no marginal cost)
    "llama3.2":                      (0.0, 0.0),
    "llama3.1:8b":                   (0.0, 0.0),
    "qwen2.5:3b":                    (0.0, 0.0),
    "gemma3:4b":                     (0.0, 0.0),
}

# Per-call cost for non-token APIs (USD per request).
# Tavily: free tier covers 1k/mo, over that $0.008/search.
PER_CALL_COSTS: dict[str, float] = {
    "tavily/search":  0.008,
}


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.exception("usage_tracker: corrupt usage file, starting fresh")
    return {
        "days": [],
        "total": {"input": 0, "output": 0, "cost_usd": 0.0, "calls": 0,
                  "by_advisor": {}, "by_provider": {}, "by_model": {}},
    }


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _ensure_day(data: dict, today: str) -> dict:
    day = next((d for d in data["days"] if d["date"] == today), None)
    if day is None:
        day = {"date": today, "input": 0, "output": 0, "cost_usd": 0.0, "calls": 0,
               "by_advisor": {}, "by_provider": {}, "by_model": {}, "hours": {}}
        data["days"].append(day)
    for k in ("by_advisor", "by_provider", "by_model", "hours"):
        day.setdefault(k, {})
    return day


def _ensure_totals(data: dict) -> dict:
    t = data.setdefault("total", {})
    for k, v in [("input", 0), ("output", 0), ("cost_usd", 0.0), ("calls", 0)]:
        t.setdefault(k, v)
    for k in ("by_advisor", "by_provider", "by_model"):
        t.setdefault(k, {})
    return t


def _track_usage(advisor: str, input_tokens: int, output_tokens: int,
                 provider: str = "anthropic",
                 model: str = "claude-sonnet-4-6") -> None:
    """Record a token-based LLM call. Cost computed from COSTS; unknown
    models default to Sonnet pricing (3/15)."""
    try:
        in_rate, out_rate = COSTS.get(model, (3.0, 15.0))
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
        pkey = f"{provider}/{model}"

        now = datetime.datetime.now()
        today = now.date().isoformat()
        hour_key = now.strftime("%H")

        data = _load(USAGE_PATH)
        t = _ensure_totals(data)
        day = _ensure_day(data, today)

        day["input"] += input_tokens
        day["output"] += output_tokens
        day["cost_usd"] = round(day["cost_usd"] + cost, 6)
        day["calls"] += 1
        day["by_advisor"][advisor] = day["by_advisor"].get(advisor, 0) + 1
        day["by_provider"][pkey] = day["by_provider"].get(pkey, 0) + 1
        day["by_model"][pkey] = day["by_model"].get(pkey, 0) + 1

        h = day["hours"].setdefault(hour_key, {"calls": 0, "cost_usd": 0.0,
                                               "input": 0, "output": 0, "by_model": {}})
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
        t["by_provider"][pkey] = t["by_provider"].get(pkey, 0) + 1
        t["by_model"][pkey] = t["by_model"].get(pkey, 0) + 1

        _save(USAGE_PATH, data)
    except Exception as e:
        logger.exception("track_usage error: %s", e)


def track_api_call(advisor: str, provider: str, endpoint: str,
                   cost_usd: float | None = None) -> None:
    """Record a non-token API call (Tavily, Plane mutation, etc).
    If cost_usd is None, looks up PER_CALL_COSTS[f'{provider}/{endpoint}']."""
    try:
        pkey = f"{provider}/{endpoint}"
        cost = cost_usd if cost_usd is not None else PER_CALL_COSTS.get(pkey, 0.0)

        now = datetime.datetime.now()
        today = now.date().isoformat()
        hour_key = now.strftime("%H")

        data = _load(USAGE_PATH)
        t = _ensure_totals(data)
        day = _ensure_day(data, today)

        day["cost_usd"] = round(day["cost_usd"] + cost, 6)
        day["calls"] += 1
        day["by_advisor"][advisor] = day["by_advisor"].get(advisor, 0) + 1
        day["by_provider"][pkey] = day["by_provider"].get(pkey, 0) + 1
        day["by_model"][pkey] = day["by_model"].get(pkey, 0) + 1

        h = day["hours"].setdefault(hour_key, {"calls": 0, "cost_usd": 0.0,
                                               "input": 0, "output": 0, "by_model": {}})
        h["calls"] += 1
        h["cost_usd"] = round(h["cost_usd"] + cost, 6)
        h["by_model"][pkey] = h["by_model"].get(pkey, 0) + 1

        t["cost_usd"] = round(t["cost_usd"] + cost, 6)
        t["calls"] += 1
        t["by_advisor"][advisor] = t["by_advisor"].get(advisor, 0) + 1
        t["by_provider"][pkey] = t["by_provider"].get(pkey, 0) + 1
        t["by_model"][pkey] = t["by_model"].get(pkey, 0) + 1

        _save(USAGE_PATH, data)
    except Exception as e:
        logger.exception("track_api_call error: %s", e)

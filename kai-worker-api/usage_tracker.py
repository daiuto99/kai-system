"""Shared usage tracker — writes to /vault/00_System/token_usage.json.

This file MUST be kept in sync between kai-worker-api/, kai-council-api/, and
kai-orchestrator/. All three containers mount /vault, so they share the JSON
store; the Python module is duplicated so each container's import graph stays
self-contained.

Data shape (per dimension entry):
    by_advisor[name]  = {calls, cost_usd, input, output}
    by_model[key]     = {calls, cost_usd, input, output}
    by_provider[key]  = {calls, cost_usd, input, output}
    by_trigger[key]   = {calls, cost_usd, input, output}

Trigger source values are colon-hierarchical (e.g. "slack:dm",
"scheduler:morning_brief"). Frontend splits on `:` to render source vs function.

Legacy int values (from pre-2026-06-09 days) are migrated inline on write.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

USAGE_PATH = Path("/vault/00_System/token_usage.json")

# Cost per provider/model (USD per 1M tokens in/out).
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
    "o4-mini":                       (1.10, 4.40),
    # Ollama (self-hosted)
    "llama3.2":                      (0.0, 0.0),
    "llama3.1:8b":                   (0.0, 0.0),
    "qwen2.5:3b":                    (0.0, 0.0),
    "gemma3:4b":                     (0.0, 0.0),
}

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
                  "by_advisor": {}, "by_provider": {}, "by_model": {}, "by_trigger": {}},
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
               "by_advisor": {}, "by_provider": {}, "by_model": {}, "by_trigger": {},
               "hours": {}}
        data["days"].append(day)
    for k in ("by_advisor", "by_provider", "by_model", "by_trigger", "hours"):
        day.setdefault(k, {})
    return day


def _ensure_totals(data: dict) -> dict:
    t = data.setdefault("total", {})
    for k, v in [("input", 0), ("output", 0), ("cost_usd", 0.0), ("calls", 0)]:
        t.setdefault(k, v)
    for k in ("by_advisor", "by_provider", "by_model", "by_trigger"):
        t.setdefault(k, {})
    return t


def _bump(d: dict, key: str, calls: int, cost: float,
          input_tokens: int, output_tokens: int) -> None:
    """Increment a breakdown dimension entry. Migrates legacy int values inline."""
    if not key:
        return
    entry = d.get(key)
    if isinstance(entry, int):  # legacy flat-int → migrate
        entry = {"calls": entry, "cost_usd": 0.0, "input": 0, "output": 0}
    if not isinstance(entry, dict):
        entry = {"calls": 0, "cost_usd": 0.0, "input": 0, "output": 0}
    entry["calls"] = entry.get("calls", 0) + calls
    entry["cost_usd"] = round(entry.get("cost_usd", 0.0) + cost, 6)
    entry["input"] = entry.get("input", 0) + input_tokens
    entry["output"] = entry.get("output", 0) + output_tokens
    d[key] = entry


def _track_usage(advisor: str, input_tokens: int, output_tokens: int,
                 provider: str = "anthropic",
                 model: str = "claude-sonnet-4-6",
                 trigger_source: str | None = None) -> None:
    """Record a token-based LLM call. Cost computed from COSTS; unknown
    models default to Sonnet pricing (3/15)."""
    try:
        in_rate, out_rate = COSTS.get(model, (3.0, 15.0))
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
        pkey = f"{provider}/{model}"
        trigger = trigger_source or "unknown"

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

        _bump(day["by_advisor"],  advisor, 1, cost, input_tokens, output_tokens)
        _bump(day["by_provider"], pkey,    1, cost, input_tokens, output_tokens)
        _bump(day["by_model"],    pkey,    1, cost, input_tokens, output_tokens)
        _bump(day["by_trigger"],  trigger, 1, cost, input_tokens, output_tokens)

        h = day["hours"].setdefault(hour_key, {"calls": 0, "cost_usd": 0.0,
                                               "input": 0, "output": 0, "by_model": {}})
        h["calls"] += 1
        h["cost_usd"] = round(h["cost_usd"] + cost, 6)
        h["input"] += input_tokens
        h["output"] += output_tokens
        _bump(h["by_model"], pkey, 1, cost, input_tokens, output_tokens)

        t["input"] += input_tokens
        t["output"] += output_tokens
        t["cost_usd"] = round(t["cost_usd"] + cost, 6)
        t["calls"] += 1
        _bump(t["by_advisor"],  advisor, 1, cost, input_tokens, output_tokens)
        _bump(t["by_provider"], pkey,    1, cost, input_tokens, output_tokens)
        _bump(t["by_model"],    pkey,    1, cost, input_tokens, output_tokens)
        _bump(t["by_trigger"],  trigger, 1, cost, input_tokens, output_tokens)

        _save(USAGE_PATH, data)
    except Exception as e:
        logger.exception("track_usage error: %s", e)


def track_api_call(advisor: str, provider: str, endpoint: str,
                   cost_usd: float | None = None,
                   trigger_source: str | None = None) -> None:
    """Record a non-token API call (Tavily, etc).
    If cost_usd is None, looks up PER_CALL_COSTS[f'{provider}/{endpoint}']."""
    try:
        pkey = f"{provider}/{endpoint}"
        cost = cost_usd if cost_usd is not None else PER_CALL_COSTS.get(pkey, 0.0)
        trigger = trigger_source or "unknown"

        now = datetime.datetime.now()
        today = now.date().isoformat()
        hour_key = now.strftime("%H")

        data = _load(USAGE_PATH)
        t = _ensure_totals(data)
        day = _ensure_day(data, today)

        day["cost_usd"] = round(day["cost_usd"] + cost, 6)
        day["calls"] += 1
        _bump(day["by_advisor"],  advisor, 1, cost, 0, 0)
        _bump(day["by_provider"], pkey,    1, cost, 0, 0)
        _bump(day["by_model"],    pkey,    1, cost, 0, 0)
        _bump(day["by_trigger"],  trigger, 1, cost, 0, 0)

        h = day["hours"].setdefault(hour_key, {"calls": 0, "cost_usd": 0.0,
                                               "input": 0, "output": 0, "by_model": {}})
        h["calls"] += 1
        h["cost_usd"] = round(h["cost_usd"] + cost, 6)
        _bump(h["by_model"], pkey, 1, cost, 0, 0)

        t["cost_usd"] = round(t["cost_usd"] + cost, 6)
        t["calls"] += 1
        _bump(t["by_advisor"],  advisor, 1, cost, 0, 0)
        _bump(t["by_provider"], pkey,    1, cost, 0, 0)
        _bump(t["by_model"],    pkey,    1, cost, 0, 0)
        _bump(t["by_trigger"],  trigger, 1, cost, 0, 0)

        _save(USAGE_PATH, data)
    except Exception as e:
        logger.exception("track_api_call error: %s", e)

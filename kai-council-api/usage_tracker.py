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

Trigger source values are colon-hierarchical (e.g. "telegram:dm",
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

# ── Pricing: single source of truth ─────────────────────────────────────────
# KAI-1283: paid per-token + per-call rates live in ONE authored home,
# provider_registry.json. This module loads them from there at import. The
# hardcoded tables below are a FALLBACK, used only when the registry is
# unreadable; a drift-guard test (tests/test_pricing_registry_drift.py) asserts
# the fallback mirrors the registry so the safety net can never silently
# diverge from the source.
REGISTRY_PATH = Path("/vault/00_System/provider_registry.json")

# Self-hosted models are structurally free (local inference) — not a price that
# can drift, so they live here rather than in the paid registry.
_SELF_HOSTED_ZERO: dict[str, tuple[float, float]] = {
    "llama3.2":      (0.0, 0.0),
    "llama3.1:8b":   (0.0, 0.0),
    "qwen2.5:3b":    (0.0, 0.0),
    "qwen2.5:7b":    (0.0, 0.0),
    "gemma3:4b":     (0.0, 0.0),
    # LiteLLM aliases that route to the self-hosted models above (BUG 91dbcb0a:
    # the council gate local-fallback reviewer tracks usage by the alias).
    "qwen-mid":        (0.0, 0.0),
    "qwen-mid-worker": (0.0, 0.0),
}

# Fallback paid rates — MUST mirror provider_registry.json token_per_1m
# (drift-guarded). Only consulted if the registry can't be read.
_FALLBACK_PAID: dict[str, tuple[float, float]] = {
    "claude-opus-4-7":               (15.0, 75.0),
    "claude-opus-4-7-20260115":      (15.0, 75.0),
    "claude-sonnet-4-6":             (3.0, 15.0),
    "claude-opus-4-6":               (15.0, 75.0),
    "claude-sonnet-4-5":             (3.0, 15.0),
    "claude-haiku-4-5":              (0.80, 4.0),
    "claude-haiku-4-5-20251001":     (0.80, 4.0),
    "gpt-4o":                        (2.50, 10.0),
    "gpt-4o-mini":                   (0.15, 0.60),
    "o4-mini":                       (1.10, 4.40),
}
_FALLBACK_PER_CALL: dict[str, float] = {"tavily/search": 0.008}


def _paid_from_registry() -> tuple[dict, dict] | None:
    """Load paid per-token (input, output) + per-call rates from the provider
    registry. Returns None on any read/shape error so the caller falls back to
    the hardcoded tables — pricing must never break on a bad registry file."""
    try:
        reg = json.loads(REGISTRY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    paid: dict[str, tuple[float, float]] = {}
    per_call: dict[str, float] = {}
    for pid, p in (reg.get("providers") or {}).items():
        for model, r in (p.get("token_per_1m") or {}).items():
            try:
                paid[model] = (float(r["input"]), float(r["output"]))
            except (KeyError, TypeError, ValueError):
                continue
        for unit, usd in (p.get("per_call_usd") or {}).items():
            try:
                per_call[f"{pid}/{unit}"] = float(usd)
            except (TypeError, ValueError):
                continue
    return (paid, per_call) if paid else None


def _build_costs() -> tuple[dict, dict]:
    loaded = _paid_from_registry()
    if loaded is None:
        logger.warning(
            "usage_tracker: provider_registry unreadable — using fallback price tables"
        )
        paid, per_call = dict(_FALLBACK_PAID), dict(_FALLBACK_PER_CALL)
    else:
        paid, per_call = loaded
    # Self-hosted $0 models are always present; registry paid rates are authoritative.
    return {**_SELF_HOSTED_ZERO, **paid}, per_call


# Cost per provider/model (USD per 1M tokens in/out) — registry-sourced at import.
COSTS, PER_CALL_COSTS = _build_costs()


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
                 trigger_source: str | None = None,
                 cache_read_tokens: int = 0,
                 cache_creation_tokens: int = 0) -> None:
    """Record a token-based LLM call. Cost computed from COSTS.
    Cache-read tokens cost 10% of input rate; cache-creation tokens cost 125% of input rate.
    Unknown models default to Sonnet pricing (3/15)."""
    try:
        in_rate, out_rate = COSTS.get(model, (3.0, 15.0))
        cost = (
            input_tokens            * in_rate
            + output_tokens         * out_rate
            + cache_read_tokens     * in_rate * 0.1
            + cache_creation_tokens * in_rate * 1.25
        ) / 1_000_000
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
        day["cache_read"]     = day.get("cache_read", 0)     + cache_read_tokens
        day["cache_creation"] = day.get("cache_creation", 0) + cache_creation_tokens

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
        t["cache_read"]     = t.get("cache_read", 0)     + cache_read_tokens
        t["cache_creation"] = t.get("cache_creation", 0) + cache_creation_tokens
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
